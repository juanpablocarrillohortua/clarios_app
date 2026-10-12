# Database overview

A first read for anyone joining the project. It explains what the database holds,
how the pieces relate and where to look next. For every column of every table, see
the [data dictionary](data_dictionary.md).

**Where things stand.** The tables described here exist, and the migrations that
create them are tested. The equivalent-batteries work is built end to end: a loader
lands the workbooks in Bronze (section 3), a build turns them into typed Silver
tables (section 5), and Gold computes the equivalent batteries as views, checks the
data and freezes the monthly close (section 6). Nothing fills the energy tables
yet, so on a fresh database they are empty.

---

## 1. What the database is for

One PostgreSQL 16 database (with the TimescaleDB extension) serves two purposes
for the Clarios battery plant:

| Purpose | Question it answers | Where the data comes from |
|---|---|---|
| **Energy monitoring** | How much electricity and gas did each plant use, and how efficiently? | Meter readings and energy files |
| **Equivalent batteries (EQUS)** | How much did the plant really produce, once batteries of different sizes are made comparable? | Exports from QAD (the plant's ERP), saved as Excel workbooks, plus a few hand-made Excel catalogues |

The two meet in one indicator: energy used per equivalent battery.

---

## 2. Four schemas, one direction of travel

Data moves through three layers, each stricter than the one before. A fourth schema
holds the lists the others refer to. A fifth, `reporting`, stores nothing: it is
the read-only window BI tools such as Power BI look through (section 6).

```mermaid
flowchart LR
    subgraph src["Sources"]
        direction TB
        M["Meter readings<br/>and energy files"]
        Q["QAD exports<br/>(Excel)"]
        H["Hand-made catalogues<br/>(Excel)"]
    end

    subgraph bronze["bronze: what arrived, untouched"]
        direction TB
        RR["raw_readings"]
        LT["five landing tables"]
    end

    subgraph silver["silver: cleaned and checked"]
        direction TB
        SR["readings"]
        SQ["quarantine"]
        ES["EQUS tables<br/>items, structures,<br/>movements, rules"]
    end

    subgraph gold["gold: published numbers"]
        direction TB
        GK["kpi_hourly, kpi_daily"]
        GS["plant_daily_summary"]
        EG["EQUS views<br/>v_resumen_equ, checks"]
        EC["EQUS close<br/>resumen_equ"]
    end

    REF[("reference<br/>plants, meters,<br/>shifts, targets")]
    RP["reporting<br/>read-only views<br/>for Power BI"]

    M --> RR --> SR --> GK
    SR --> GS
    RR -. "rejected rows" .-> SQ
    Q --> LT
    H --> LT
    LT --> ES
    ES --> EG
    EG -. "close-month" .-> EC
    EG --> RP
    EC --> RP
    REF -. "looked up by" .-> SR
    REF -. "looked up by" .-> GK
```

| Schema | In one sentence | Can rows change? |
|---|---|---|
| `bronze` | An exact copy of what each source sent. | Never. Rows are only added. |
| `silver` | The same data, typed, checked and de-duplicated. | A correction replaces a row but keeps the old one. |
| `gold` | The figures that reports and dashboards show. | KPIs are recomputed per period. EQUS figures are views; a closed month is frozen and changes only by a reclose. |
| `reference` | Plants, meters, shifts and targets. | Maintained by people. |
| `reporting` | Flat views of the EQUS figures for Power BI. | Nothing is stored; the views read Gold. |

The rule behind the layers: **never repair data where it lands**. Bronze keeps the
evidence, Silver decides what is valid, Gold publishes. If a number looks wrong, you
can always walk back from Gold to the exact spreadsheet row it came from.

---

## 3. Bronze: the landing area

Created by [`0002_create_bronze_schema.py`](../migrations/versions/0002_create_bronze_schema.py).

### How one workbook becomes Bronze rows

This is what the loader does for each workbook.

```mermaid
flowchart TD
    W["A workbook exported from QAD"]
    W --> A["1. Archive the file unchanged<br/>in object storage"]
    A --> F["2. Catalogue it<br/>raw_files, one row per distinct file"]
    F --> Q{"3. Did a completed run<br/>already land these sheets?"}
    Q -- "yes" --> S["Stop: nothing to do"]
    Q -- "no" --> R["4. Open a run<br/>ingestion_runs, state = running"]
    R --> L["5. Copy every sheet row into its landing table<br/>one spreadsheet row = one Bronze row<br/>all sheets in one transaction"]
    L -- "all rows in" --> C["6. Close the run<br/>state = completed, row_count filled in,<br/>period = first and last date found in the rows"]
    L -- "anything fails" --> X["Undo every row of the run<br/>state = failed, error filled in"]
```

Two consequences worth knowing:

- **Loading the same file twice does nothing.** A sheet that a completed run already
  landed is skipped. To land it again on purpose, use `--force`; Bronze then holds
  both copies, each under its own run.
- **A run is all or nothing.** A `completed` run has all its rows. A `failed` run has
  none, and its `error` column says why.
- **The loader assumes no year.** It lands whatever dates an export holds, and each
  run records the first and last *Fecha Efectiva* it found (`window_start`,
  `window_end`). Which fiscal year a figure belongs to is decided later, in Gold.

### Loading the workbooks

Put the workbooks in `data/raw_excels/` and run:

```bash
make equs-load
```

This lands every workbook the calculation uses, in a fixed order, and prints one
line per sheet. About 205,000 rows take under a minute. The files are only read,
never changed. To land a single workbook or chosen sheets, see the `energy equs
load` examples in the [README](../README.md).

**Exports of other years go in the same folder.** Name each one with the usual
name followed by anything, and `make equs-load` lands the ones it has not landed
yet:

```text
Calculo de equivalente - Explorar transacciones por art.xlsx        the current export
Calculo de equivalente - Explorar transacciones por art FY24.xlsx   an older one
Calculo de equivalente - Unidades facturadas FY24.xlsx              its invoices
```

This works for the two movement exports only (transactions and invoices). The
structure and catalogue workbooks are snapshots, so there is one file of each,
under its exact name: to update one, replace the file. Any workbook in the
folder that the loader does not recognise is listed at the end of its output as
not loaded.

### The tables

```mermaid
erDiagram
    ingestion_runs ||--o{ raw_rct_wo : "wrote"
    ingestion_runs ||--o{ raw_facturas : "wrote"
    ingestion_runs ||--o{ raw_product_struc : "wrote"
    ingestion_runs ||--o{ raw_item_master : "wrote"
    ingestion_runs ||--o{ raw_manual_sheets : "wrote"
    raw_files |o--o{ raw_rct_wo : "source of"
    raw_files |o--o{ raw_facturas : "source of"
    raw_files |o--o{ raw_product_struc : "source of"
    raw_files |o--o{ raw_item_master : "source of"
    raw_files |o--o{ raw_manual_sheets : "source of"

    ingestion_runs {
        uuid run_id PK
        text source_key
        text state
        date window_start
        date window_end
        text qad_program
        jsonb search_conditions
        bigint row_count
    }
    raw_files {
        uuid raw_file_id PK
        char64 content_hash UK
        text filename
        text source_key
        textarray sheet_names
    }
    raw_rct_wo {
        uuid raw_rct_wo_id PK
        timestamptz ingested_at PK
        uuid ingestion_run_id FK
        uuid raw_file_id FK
        text sheet_name
        int source_row
        char64 content_hash
        jsonb payload
    }
    raw_facturas {
        uuid raw_facturas_id PK
        timestamptz ingested_at PK
        jsonb payload
    }
    raw_product_struc {
        uuid raw_product_struc_id PK
        timestamptz ingested_at PK
        jsonb payload
    }
    raw_item_master {
        uuid raw_item_master_id PK
        timestamptz ingested_at PK
        jsonb payload
    }
    raw_manual_sheets {
        uuid raw_manual_sheets_id PK
        timestamptz ingested_at PK
        text dataset_key
        jsonb payload
    }
    raw_readings {
        uuid raw_reading_id PK
        timestamptz ingested_at PK
        uuid ingestion_run_id
        jsonb payload
    }
```

The five landing tables have the same columns; the diagram spells them out once, on
`raw_rct_wo`. `raw_readings` is the landing table for energy readings. It is older
than the others and is not linked to the two ledgers by a foreign key.

| Table | What lands in it | Rows with the FY26 files |
|---|---|---|
| `ingestion_runs` | One row per load: which source, which period, what state, how many rows. | 7 |
| `raw_files` | One row per archived workbook, identified by the hash of its bytes. | 6 |
| `raw_rct_wo` | Production receipts: *Explorar transacciones por art* › `Datos`. | 138,773 |
| `raw_facturas` | Invoice lines: *Unidades facturadas* › `Datos`. | 14,458 |
| `raw_product_struc` | Product structures: *BOM BATERIAS FORMADAS* › `PASTA`, `PLACAS`, `BAT ENS`, `BAT FOR`, `BAT TERM`, and *ESTRUCTURA DECORADO* › `Datos`. | 48,475 |
| `raw_item_master` | The item master: *Item_Master* › `Datos`, `Grupo-Fam`. The workbook has not been delivered yet. | 0 |
| `raw_manual_sheets` | Hand-made catalogues. `dataset_key` says which one: `clasif_poly`, `lineas_produccion`, `instrucc`, `element_deco`, `comparativo_lineas`. | 3,633 |
| `raw_readings` | Energy meter readings. No loader yet. | 0 |

There are seven runs for six files because *ESTRUCTURA DECORADO* feeds two loads: its
product structures and its catalogue of decoration pieces.

### Three rules that make Bronze trustworthy

1. **The row is stored as it arrived.** Each row keeps its cells in a JSON `payload`.
   The keys are the column headers of the sheet, in Spanish and with their accents.
   The values are the cell text. Nothing is converted: a date stays text, and `N/A`
   stays `N/A`.
2. **Rows are never changed.** The database rejects any `UPDATE` on a landing table.
   A correction is a new row from a new run.
3. **Duplicates are kept.** The exports contain repeated rows, and removing them
   changes the result of the calculation. That decision belongs to Silver, where it
   is visible. Bronze keeps every copy.

This is the payload of the first production receipt (shortened):

```json
{
  "Fecha Efectiva": "2025-12-10",
  "Número Artículo": "111000002",
  "Número de transacción": "17240425",
  "Tipo Transacción": "RCT-WO",
  "Cambiar cantidad de ubic": "70000",
  "Costo Total": "182346891.593783",
  "Campo Equs": "N/A",
  "Línea Producción": "MAQBUJEO"
}
```

How a cell becomes text:

| In the workbook | In the payload |
|---|---|
| Text | Exactly as it is, not trimmed. `N/A` stays `N/A`. |
| A whole number, such as 900 | `900`, never `900.0` |
| Any other number | Its plain decimal form: `0.0636`, `0.00001`. Never `1e-05`. |
| A date, a date and time, a time | `2025-12-10`, `2025-12-10T11:34:05`, `11:34:00` |
| True / false | `true` / `false` |
| An empty cell | No key at all |

How a column gets its key:

| The header is… | The key is… | Example |
|---|---|---|
| A text | That text, trimmed | `Fecha Efectiva` |
| Repeated | The text plus ` #2`, ` #3` | `Descripción`, `Descripción #2` |
| Blank | The Excel column letter | `P` |
| Missing (the sheet has no header row) | The Excel column letter | `A`, `B`, `D` |

Rows that are completely empty are not landed. Every other row is, including rows
that repeat an earlier one.

Around the payload, every landing row records where it came from:

| Column | Meaning |
|---|---|
| `ingestion_run_id` | The load that wrote the row. |
| `raw_file_id` | The archived workbook it came from. |
| `source_uri` | Where the original is archived, and which sheet: `bucket://key#sheet`. |
| `sheet_name`, `source_row` | The sheet and the Excel row number. |
| `content_hash` | A fingerprint of the payload, used to find identical rows. |
| `ingested_at` | When the row landed. |

---

## 4. Reference: the lists everything else points to

Created by [`0003_create_silver_schema.py`](../migrations/versions/0003_create_silver_schema.py),
together with Silver.

```mermaid
erDiagram
    plants ||--o{ meters : "has"
    plants ||--o{ shifts : "works"
    plants ||--o{ targets : "is judged by"
    meters |o--o{ meters : "parent of"

    plants {
        uuid plant_id PK
        text code UK
        text name
        text timezone
        boolean is_active
    }
    meters {
        uuid meter_id PK
        uuid plant_id FK
        text tag
        text commodity
        text unit
        uuid parent_meter_id FK
    }
    shifts {
        uuid shift_id PK
        uuid plant_id FK
        text name
        time starts_at
        time ends_at
    }
    targets {
        uuid target_id PK
        uuid plant_id FK
        text kpi_key
        numeric value
        timestamptz valid_from
        timestamptz valid_to
    }
```

Targets carry dates of validity. A target agreed for this year does not change how
last year was judged.

---

## 5. Silver: cleaned and checked

Created by [`0003_create_silver_schema.py`](../migrations/versions/0003_create_silver_schema.py).
Silver holds two families of tables that do not touch each other: the energy
readings, and the equivalent-batteries (EQUS) tables.

### Energy readings

A solid line is a foreign key the database enforces. A dashed line is a link by
value that the database does not enforce.

```mermaid
erDiagram
    meters ||..o{ readings : "measures"
    meters ||--o{ consolidation_audit : "disputed on"
    ingestion_runs ||..o{ readings : "loaded by"
    raw_readings ||..o{ quarantine : "rejected from"

    readings {
        uuid reading_id PK
        timestamptz ts_utc PK
        uuid meter_id
        uuid plant_id
        float value
        text quality
        uuid ingestion_run_id
        timestamptz superseded_at
    }
    quarantine {
        uuid quarantine_id PK
        uuid run_id
        uuid bronze_row_id
        text rule
        text reason
        jsonb raw_payload
        timestamptz resolved_at
    }
    consolidation_audit {
        uuid audit_id PK
        timestamptz ts_utc
        uuid meter_id FK
        text winning_source
        float divergence_pct
    }
    meters {
        uuid meter_id PK
    }
    ingestion_runs {
        uuid run_id PK
    }
    raw_readings {
        uuid raw_reading_id PK
    }
```

| Table | What it holds |
|---|---|
| `readings` | One valid reading per meter and hour, in UTC. |
| `quarantine` | Every row that failed a check, with the rule it broke. Nothing is thrown away. |
| `consolidation_audit` | Each case where two sources disagreed, and which one won. |

Three things to remember:

- **Time is always UTC, on the hour.** Local time is used only when showing data.
- **Missing is not zero.** An hour nobody reported has quality `missing` and an empty
  value. A zero would lower every average.
- **Corrections do not overwrite.** The old row gets a `superseded_at` date and a new
  row is added.

### Equivalent batteries (EQUS)

Eighteen tables and two materialized views hold everything the calculation reads.
Their names are Spanish because they mirror the Excel sheets and QAD headers the
plant already uses. Every line in this diagram is a foreign key.

```mermaid
erDiagram
    item_master ||--o{ bom_estructura : "is parent in"
    item_master ||--o{ bom_estructura : "is component in"
    item_master ||--o{ reporte_qad : "produced"
    item_master ||--o{ unit_ship : "invoiced"
    item_master ||--o| element_deco : "may be a decoration piece"
    item_master ||--o| clasif_poly : "may be a plastic piece"
    item_master ||--o{ costo_std : "cost history"
    linea_de_productos ||--o{ item_master : "classifies"
    campo_equs ||--o{ item_master : "categorises"
    lineas_produccion ||--o{ reporte_qad : "reported by"
    almacen ||--o{ reporte_qad : "received in"
    almacen ||--o{ lineas_produccion : "houses"
    planta ||--o{ lineas_produccion : "groups"
    vendido_a ||--o{ unit_ship : "bought"
    equs_version ||--|{ factores_equ : "has"
    factores_equ ||--o{ instrucc : "is fed by"

    item_master {
        text numero_articulo PK
        text descripcion
        text linea_de_productos FK
        text campo_equs FK
        char polaridad
    }
    bom_estructura {
        bigint bom_id PK
        text articulo_matriz FK
        text articulo_componente FK
        numeric cantidad_por
        date fin_efectivo
        text calculo
        text source_sheet
        uuid source_run_id FK
    }
    reporte_qad {
        bigint numero_de_transaccion PK
        date fecha_efectiva
        text numero_articulo FK
        text linea_produccion FK
        numeric cambiar_cantidad_de_ubic
        text campo_equs
        uuid source_run_id FK
    }
    unit_ship {
        text num_fact PK
        text orden PK
        int linea PK
        date fecha_efectiva
        text numero_articulo FK
        numeric cantidad_fac
        uuid source_run_id FK
    }
    equs_version {
        int version PK
        text nombre
        date vigente_desde
        date vigente_hasta
    }
    factores_equ {
        int version PK
        text cuenta_stats PK
        text proceso
        text fuente
        numeric estandar_por_bat
        numeric multiplicador
        numeric peso
        numeric factor
    }
    instrucc {
        int version PK
        text cuenta_stats PK
        int regla_n PK
        text linea_de_productos
        text campo_equs
        text prefijo_linea
    }
    lineas_produccion {
        text linea PK
        text planta FK
        text almac FK
    }
```

| Group | Tables | What they hold |
|---|---|---|
| Catalogues | `item_master`, `linea_de_productos`, `campo_equs`, `lineas_produccion`, `planta`, `almacen`, `vendido_a`, `costo_std`, `grupo_fam` | Who and what the movements refer to: items, product lines, categories, production lines, plant areas, warehouses, customers. |
| Structures | `bom_estructura`, `element_deco`, `clasif_poly`, `costo_conversion` | What each product is made of, which components are decoration pieces, and the factor of each plastic piece. |
| Movements | `reporte_qad`, `unit_ship` | One row per production receipt and per invoice line. |
| The equation | `equs_version`, `factores_equ`, `instrucc` | The standards, multipliers and weights of each driver, and which rows feed it, with dates of validity. |
| Conversions | `td_form`, `td_deco` (materialized views) | Kg of positive paste per formed battery, and decoration pieces per decorated battery. |

Three things to remember:

- **The equation is data, not code.** `EQ = quantity x (multiplier / standard) x weight`.
  The three numbers of each driver are rows of `factores_equ`, and the factor is
  computed by the database from them. To change a standard, add a version; do not
  edit a past one, or past months change.
- **`N/A` is a value.** In `campo_equs` it means "not applicable". It is never empty.
- **Item codes are text.** `21500000A` is a real item.

`grupo_fam` and `costo_conversion` are empty: the workbooks that feed them have not
been delivered.

### How Bronze becomes Silver

```mermaid
flowchart TD
    B["Bronze rows of completed runs"]
    B --> S["Snapshots<br/>structures, catalogues<br/>latest run of each sheet"]
    B --> M["Movements<br/>receipts, invoice lines<br/>every run, newest copy of each"]
    S --> C1["Drop what is not a link<br/>pasted header rows, rows without a component"]
    C1 --> C2["Keep each link once<br/>same parent, component, dates and quantity"]
    M --> I["Describe every item code seen anywhere<br/>first source that knows each attribute wins"]
    C2 --> I
    I --> W["Write Silver<br/>catalogues, then structures, then movements"]
    W --> V["Refresh td_form and td_deco"]
    V --> R["Data-quality report"]
    W -- "any step fails" --> X["Undo everything: Silver is left as it was"]
```

Run it after loading the workbooks:

```bash
make equs-silver
```

It takes about half a minute and prints a report: rows per table, what was dropped
from each structure sheet, and the checks that need a person (production lines
without a plant area, batteries produced without a conversion, items QAD does not
classify). Four properties are worth knowing:

- **All or nothing.** The whole build is one transaction.
- **Safe to repeat.** Building twice from the same Bronze gives the same Silver, row
  for row.
- **Snapshots are replaced, movements accumulate.** The product structure is whatever
  the latest load says. A receipt, once in Silver, stays.
- **Nothing is dropped to make a reference hold.** If a receipt names an item that no
  source describes, the item is created with the description `(sin descripción)` and
  listed in the report.

---

## 6. Gold: the published numbers

Created by [`0004_create_gold_schema.py`](../migrations/versions/0004_create_gold_schema.py).

```mermaid
erDiagram
    plants ||--o{ kpi_hourly : "has"
    plants ||--o{ kpi_daily : "has"
    plants ||--o{ plant_daily_summary : "has"

    kpi_hourly {
        uuid kpi_hourly_id PK
        timestamptz period_start PK
        uuid plant_id FK
        text kpi_key
        float value
        float coverage
    }
    kpi_daily {
        uuid kpi_daily_id PK
        timestamptz period_start PK
        uuid plant_id FK
        text kpi_key
        text granularity
        float value
        float coverage
        float target_value
    }
    plant_daily_summary {
        uuid summary_id PK
        timestamptz day PK
        uuid plant_id FK
        float energy_kwh
        float gas_kwh
        float production_equivalent_units
        float sec_kwh_per_unit
        float deviation_pct
        float coverage
    }
    plants {
        uuid plant_id PK
    }
```

| Table | What it holds |
|---|---|
| `kpi_hourly` | One value per plant, indicator and hour. |
| `kpi_daily` | The same per day, week or month, with the target in force. |
| `plant_daily_summary` | One wide row per plant and day; what the dashboard's first page reads. |

Every Gold row has a `coverage` between 0 and 1: the share of the period that is
backed by reliable readings. Read it before trusting the value next to it. In
`plant_daily_summary`, a positive `deviation_pct` means the plant used **more**
energy than expected.

### Equivalent batteries (EQUS)

The last step of moving the calculation out of Excel. Gold **applies** the
equation that Silver keeps as data: every view filters receipts and invoices with
the rules of `silver.instrucc`, sums what each driver asks for, and multiplies by
its factor in `silver.factores_equ`, for the version of the equation in force that
month. No number of the equation is written in a view, so a new standard or a new
rule is a new version in Silver and Gold needs no change.

```mermaid
flowchart TD
    subgraph S["silver"]
        direction TB
        RQ["reporte_qad<br/>production receipts"]
        US["unit_ship<br/>invoice lines"]
        TF["td_form, td_deco<br/>paste and pieces per battery"]
        CP["clasif_poly<br/>plastic categories"]
        RU["factores_equ, instrucc,<br/>equs_version"]
    end
    subgraph G["gold"]
        direction TB
        VR["v_reporte_qad<br/>REPORTE QAD with its calculated columns"]
        PR["v_reporte_qad_regla, v_unit_ship_regla<br/>each row paired with the rules it matches"]
        CC["v_cantidad_cuenta<br/>column D"]
        RE["v_resumen_equ<br/>column H"]
        BM["v_equs_batt_mes<br/>rows 36 to 45"]
        IN["inyeccion<br/>EQUS POLY"]
        DQ["dq_* views<br/>six checks"]
        FN["range functions<br/>any dates"]
        CL[("resumen_equ<br/>frozen close")]
    end
    RQ --> VR
    TF --> VR
    CP --> VR
    VR --> PR
    US --> PR
    RU --> PR
    PR --> CC --> RE --> BM
    VR --> IN
    PR --> FN
    VR --> DQ
    RE -- "close-month" --> CL
```

| Object | What it holds | In the workbook |
|---|---|---|
| `v_reporte_qad` | Each production receipt with its kg of paste, decoration pieces and plastic EQ | Sheet REPORTE QAD and its columns AY, AZ, BE, BF, BG |
| `v_resumen_equ` | Quantity, factor and EQ of each driver and month | **Column H of RESUMEN EQU** |
| `v_equs_batt_mes` | EQ of each process and EQUS BATT, by month | Rows 36 to 45 |
| `inyeccion` | EQUS POLY by month and category | Pivot INYECCION |
| `lineas_de_pdcto` | Production by product line and Campo Equs | Pivot LINEAS DE PDCTO |
| `v_costo_por_eq` | Conversion cost per EQ | New |
| `dq_*` | Six data-quality checks; three of them block a close | New |
| `resumen_equ` | The official close of each month | "ESTA SE COPIA" |

**Three ways to read it.**

- **Column H of one month, now:** `make resumen MONTH=2026-09`. The same as
  `SELECT * FROM gold.v_resumen_equ WHERE mes = '2026-09-01'`.
- **Any range of days:** `make report START=2026-03-15 END=2026-04-10` writes JSON
  laid out like RESUMEN EQU, with warnings when the range cuts a month or spans two
  versions of the equation.
- **The official number:** `make close MONTH=2026-09` freezes the month after the
  data-quality checks pass. A closed month never changes by itself; only a
  reclose replaces it, and the replaced values are kept.

**Keeping it current.** The views read Silver live, so they are up to date as soon
as Silver is. `make refresh-all` does everything after new workbooks arrive: load,
build Silver, refresh the two conversion tables, refresh statistics, and print
column H of the latest month. `make refresh` does the last three only. A refresh
never touches a closed month, and two refreshes never run at once.

### Reading it from Power BI

Schema `reporting` (revision 0006) restates these figures as twelve flat views a
BI tool can load and relate:

| Group | Views |
|---|---|
| Monthly figures | `equs_mensual` (column H by month), `equs_batt_mensual`, `equs_poly_mensual`, `calidad_datos_mensual` |
| The closes | `cierres`, `cierres_calidad`, `equs_cerrado`, `equs_cerrado_historial` |
| The detail behind each driver | `aporte_cuenta`, `recibos_produccion` |
| Dimensions | `cuentas`, `articulos` |

It stores nothing. Every number comes from a Gold view, so Power BI and
`make resumen` cannot disagree.

Power BI connects with its own login, created by `make powerbi`. That login can
read these views and nothing else, and its sessions are read-only. It arrives
through `reporting-bridge`, an opt-in container that relays one port
(127.0.0.1:5433) to the database. Step by step, with a model and starter
measures: [powerbi.md](powerbi.md).

---

## 7. Which tables are time-partitioned

Five tables are TimescaleDB *hypertables*: PostgreSQL tables that are split into
chunks by time, which keeps queries over a date range fast. You query them like any
other table. Set up by [`0005_create_hypertables.py`](../migrations/versions/0005_create_hypertables.py).

| Table | Split by | Old data |
|---|---|---|
| `bronze.raw_readings` | `ingested_at` | Compressed after 30 days, deleted after 2 years |
| `silver.readings` | `ts_utc` | Compressed after 90 days |
| `gold.kpi_hourly` | `period_start` | Compressed after 180 days |
| `gold.kpi_daily` | `period_start` | Kept as is |
| `gold.plant_daily_summary` | `day` | Kept as is |

The five EQUS landing tables are ordinary tables for now. Their primary key already
includes `ingested_at`, so they can become hypertables later without being rebuilt.

---

## 8. Which migration creates what

The database is built only by the numbered files in `migrations/versions/`, applied
in order. To change the shape of the database, add a new file; do not create or
alter tables by hand.

```mermaid
flowchart LR
    m1["0001<br/>extensions<br/>timescaledb, pgcrypto"]
    m2["0002<br/>bronze<br/>8 tables"]
    m3["0003<br/>reference + silver<br/>4 + 21 tables, 2 views,<br/>version 1 of the equation"]
    m4["0004<br/>gold<br/>3 + 6 tables, 15 views,<br/>8 functions"]
    m5["0005<br/>hypertables,<br/>compression, retention"]
    m6["0006<br/>reporting<br/>12 views for Power BI,<br/>role reporting_reader"]
    m1 --> m2 --> m3 --> m4 --> m5 --> m6
```

Revisions 0002, 0003 and 0004 were rewritten in place while the EQUS layers were
built. A database migrated before a rewrite is brought up to date by going down to
the revision before the rewritten one and up again: for the Gold rewrite,
`make db-downgrade` until `alembic current` says `0003`, then `make db-upgrade`.
That leaves Bronze and Silver as they are, but it drops `gold`, closed months
included. Revision 0006 was added as a new file, so `make db-upgrade` applies it
to any database already at `0005`.

---

## 9. The monthly close, step by step

```mermaid
flowchart TD
    A["make refresh-all<br/>new workbooks landed, Silver built,<br/>conversion tables refreshed"] --> B["make resumen MONTH=…<br/>look at column H"]
    B --> C["make close MONTH=…"]
    C --> D["data-quality checks of the month"]
    D -- "a blocking check has rows" --> E["refused (exit 1)<br/>fix the data, or close again with ACCEPT_WARNINGS=1"]
    D -- "clean, or accepted" --> F["column H frozen in gold.resumen_equ<br/>one transaction, recorded in gold.cierre_mes"]
    F --> G["later changes to Silver<br/>move the views, never the close"]
    G -- "the close must change" --> H["make close MONTH=… RECLOSE=1<br/>old values kept in resumen_equ_historial"]
```

---

## 10. Looking around

The database publishes no port to your machine, so open `psql` inside its container:

```bash
make up                                          # start the stack
make db-upgrade                                  # apply the migrations
make refresh-all                                 # land the EQUS workbooks, build Silver, refresh Gold
make resumen MONTH=2026-09                       # column H of RESUMEN EQU for a month
docker compose exec db psql -U energy -d energy  # open a SQL prompt
```

Plain `\dt` shows almost nothing, because the tables are not in the default
`public` schema. Name the schema:

```sql
\dn                       -- list the schemas
\dt bronze.*              -- list the tables of a schema
\d+ bronze.raw_rct_wo     -- columns, indexes and triggers of a table

-- The latest loads
SELECT flow_name, source_key, state, row_count, started_at
FROM bronze.ingestion_runs
ORDER BY started_at DESC
LIMIT 10;

-- Rows landed per sheet of the product structures
SELECT sheet_name, count(*)
FROM bronze.raw_product_struc
GROUP BY sheet_name
ORDER BY sheet_name;

-- A production receipt by its QAD transaction number
SELECT sheet_name, source_row, payload
FROM bronze.raw_rct_wo
WHERE payload ->> 'Número de transacción' = '17240425';

-- Rows that landed more than once
SELECT content_hash, count(*)
FROM bronze.raw_product_struc
GROUP BY content_hash
HAVING count(*) > 1;

-- Silver: what a formed battery is made of, one level down
SELECT b.articulo_componente, m.descripcion, b.cantidad_por, b.fin_efectivo
FROM silver.bom_estructura AS b
JOIN silver.item_master AS m ON m.numero_articulo = b.articulo_componente
WHERE b.articulo_matriz = '231000001';

-- Silver: the factor of every driver of the equation
SELECT proceso, cuenta_stats, estandar_por_bat, multiplicador, peso, factor
FROM silver.factores_equ
WHERE version = 1
ORDER BY proceso, cuenta_stats;

-- Silver: production of a month by category
SELECT campo_equs, sum(cambiar_cantidad_de_ubic) AS cantidad
FROM silver.reporte_qad
WHERE fecha_efectiva >= DATE '2026-09-01' AND fecha_efectiva < DATE '2026-10-01'
GROUP BY campo_equs
ORDER BY cantidad DESC;

-- Gold: column H of RESUMEN EQU for a month
SELECT proceso, cuenta_stats, cantidad, factor, equs
FROM gold.v_resumen_equ
WHERE mes = DATE '2026-09-01'
ORDER BY proceso, cuenta_stats;

-- Gold: equivalent batteries by month
SELECT * FROM gold.v_equs_batt_mes ORDER BY mes;

-- Gold: any range of days, both ends included
SELECT * FROM gold.resumen_equ_rango(DATE '2026-03-15', DATE '2026-04-10');

-- Gold: the data-quality checks of a month
SELECT * FROM gold.calidad_datos_rango(DATE '2026-09-01', DATE '2026-09-30');
```

---

## Glossary

| Term | Meaning |
|---|---|
| **QAD** | The plant's ERP system. Its reports are exported to Excel. |
| **EQUS** | Equivalent batteries: production expressed in standard batteries, so that months with a different product mix can be compared. |
| **Run** | One execution of a load. Every Bronze row points at the run that wrote it. |
| **Driver** | One line of the EQ calculation (*cuenta STATS*): for example kg of oxide, or assembled truck batteries. Each has a factor. |
| **Product structure** | The list of what an item is made of (BOM). One row is one parent-to-component link. |
| **Conversion table** | `td_form` or `td_deco`: how many kg of paste, or how many decoration pieces, one battery stands for. |
| **Materialized view** | A query whose result is stored like a table and recomputed on request. |
| **Payload** | The JSON column that holds a source row exactly as it arrived. |
| **Provenance** | The columns that say where a row came from: run, file, sheet, row number. |
| **Content hash** | A fixed-length fingerprint (SHA-256) of a file or a row. Equal content gives an equal hash. |
| **Hypertable** | A table that TimescaleDB splits into chunks by time. |
| **Migration** | A numbered file that changes the shape of the database. |
| **Coverage** | The share of a period that is backed by reliable data, from 0 to 1. |
| **Column H** | The column of sheet RESUMEN EQU that holds the EQ of each driver; `gold.v_resumen_equ` in the database. |
| **Close** | The official EQ of a month, frozen in `gold.resumen_equ` so that later corrections to the data do not change a published number. |
| **Version (of the equation)** | A set of factors and rules with a validity period. Each month is computed with the version in force that month. |
| **Reporting login** | The read-only database user Power BI connects with (`powerbi` by default). It can read the `reporting` views and nothing else. |
| **Bridge** | `reporting-bridge`, the opt-in container that relays one port to the database so Power BI can reach it. |

## Related documents

- [Data dictionary](data_dictionary.md): every column of every table
- [Architecture](architecture.md): how the code is organised around the database
- [EQUS context](EQUS_CONTEXT.md): the business rules of the equivalent-batteries calculation
- [Power BI](powerbi.md): connecting Power BI Desktop and the Power BI Service to the reporting views
- [Runbook](runbook.md): backups, restore, incident response
