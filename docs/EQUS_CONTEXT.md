# EQUS Project Context — Equivalent Batteries for the Clarios Plant

This document is the single source of truth for any agent working on this repository. Read it fully before writing migrations, loaders or transformations. It contains no code on purpose: it explains the business problem, how the current Excel workbooks compute equivalent batteries, how that logic maps to the target database, the full target schema and where every column comes from in the Excel files.

---

## 1. The problem

### 1.1 Business context

Clarios operates a lead-acid automotive battery plant in Colombia (QAD site/warehouse 1515, plus warehouses 1010 for plastic components, 1520 and 1521 for lead recycling). The plant makes batteries of very different sizes and complexity: small cars, trucks, AGM/EFB start-stop. Counting physical units therefore says little about the real industrial effort.

Management measures throughput with **equivalent batteries (EQ, "EQUS")**: every physical output of every process is converted into "standard batteries" using a standard consumption per battery, a complexity multiplier and the weight of that process in the plant's total conversion cost. The plant total, **EQUS BATT**, is the single throughput indicator used to compare months and to compute **conversion cost per equivalent battery**. A second, independent indicator, **EQUS POLY**, measures the plastic injection plant (warehouse 1010).

### 1.2 Current state

Today the calculation lives in a set of Excel workbooks fed by manual exports from the ERP (QAD). The monthly close consists of pasting new QAD exports into the workbooks, refreshing about 34 pivot tables and copying the result. The process has these problems:

- **Constants hard-coded inside formulas.** The same numbers (6.08, 84, 0.5, 1.33, 1.14, 2.15, 3.39, 4.29, 12.8 and eight process weights) are repeated in many cells, and some copies disagree.
- **No history of the rules.** When a standard changes, there is no record of when or what the previous value was, so past months cannot be recomputed as reported.
- **External links** to other workbooks, including an item master stored in one person's personal OneDrive.
- **Fixed-size lookup ranges** that silently truncate (a lookup that stops at row 1,384 of a 1,751-row catalogue).
- **Data defects in the exports** that inflate the result. Duplicated product-structure links double the active material of some batteries, which overstates the Formation EQ by about 37,940 EQ in FY26.
- **No audit trail** linking a reported number back to the source rows that produced it.

### 1.3 Goal

Replace the workbooks with a PostgreSQL database organised as a medallion architecture:

- **Bronze**: an immutable copy of every export exactly as it arrived.
- **Silver**: cleaned, typed, deduplicated and conformed tables, plus the two per-product conversion tables and the configuration of the calculation.
- **Gold**: the EQ calculation as views, data-quality checks and the frozen monthly close.

The database must reproduce the Excel result where the Excel is right and correct it where the Excel is wrong, with every difference explained (see §9).

### 1.4 Repository facts

- Database: PostgreSQL with the extensions **timescaledb** and **pgcrypto**. Both are created by migration **0001**, which already exists. UUIDs default to `gen_random_uuid()`.
- Migrations: Alembic, one revision per medallion layer. The planned revisions are 0002 Bronze, 0003 Silver and 0004 Gold. Hypertables and retention policies, if wanted, go in a later revision. Check the repository for an existing plan before numbering.
- **Source workbooks are in `data/raw_excels/`.** Expected file names are listed in §2. Always list the directory first, because names may differ slightly in spaces and hyphens.
- Locale of the data: Spanish headers with accents, comma-free numeric cells, dates as Excel dates. QAD timestamps in the "Info" sheets use the format `dd/mm/yyyy h:mm:ss a. m.` in America/Bogota time.

---

## 2. Source workbooks (`data/raw_excels/`)

| File | Origin | Content | Role |
|---|---|---|---|
| `Calculo de equivalente - Explorar transacciones por art.xlsx` | QAD exploration *Explorar Transacciones por Art*, program `icbr011.p` | Sheet **Datos**: 138,773 production receipts (transaction type RCT-WO), 2025-10-01 → 2026-09-30, 49 columns A–AW. Sheets *Condiciones de búsqueda* and *Info* hold the query metadata. | Production quantities for every process (full FY26) |
| `Calculo de equivalente - Unidades facturadas.xlsx` | QAD *Explorar Histórico de Facturas Clarios*, program `zzbr007.p` | Sheet **Datos**: 14,458 invoice lines, warehouse 1515, channel Venta, product lines 3103 and 3104, FY26, columns A–AW. Also *Condiciones de búsqueda* and *Info*. | Units shipped (Shipping driver) |
| `BOM BATERIAS FORMADAS.xlsx` | QAD *Product struc Clarios*, program `xxbr201.p`, plus manual columns | Product structures by level: sheets **PASTA**, **PLACAS**, **BAT ENS**, **BAT FOR**, **BAT TERM** (hidden). Also the pivot **TD_FORM**, the unfinished sheet *MATERIAL ACTIVO*, and *Condiciones de búsqueda* / *Info*. | Computes kg of positive paste per formed battery (Formation conversion) |
| `ESTRUCTURA DECORADO.xlsx` | QAD `xxbr201.p` plus manual catalogue | Sheet **Datos** (31,907 rows: structures of decorated 3103, semi-decorated 3102 and imported 3104 batteries), **ELEMENT DECO** (catalogue of decoration pieces), pivot **TD**, plus auxiliary sheets *BAT SEMIDECO*, *No Element*, *Bat sin Element*. | Computes decoration pieces per decorated battery (Decoration conversion) |
| `EQUS BATT.xlsx` | All of the above plus calculation | 24 sheets. The engine: **REPORTE QAD** (one month of RCT-WO plus calculated columns), **Unit_Ship** (one month of invoices), **RESUMEN EQU** (the final calculation), **Clasif_Poly**, **Lineas Produccion**, **INSTRUCC**, **L.PRODUCCION** and many pivots. | The current calculation; source of the catalogues and the rules |
| `Calculo de equivalente- Comparativo lineas.xlsx` | Manual | Sheet **Hoja1**, 50 rows: plant area (column B), process name in the energy database (C), QAD production line (D). | Maps lines to plant areas; not used by EQUS itself |
| `Item_Master.xlsx` | Manual / QAD | **Not provided yet.** Referenced by external links from *EQUS BATT* and *ESTRUCTURA DECORADO*. Sheets **Datos** (item master, columns A–BD) and **Grupo-Fam** (family to battery type). | Item attributes not present elsewhere (family, battery type, product line of components) |

**Do not load these sheets as sources:** *REPORTE QAD* and *Unit_Ship* inside *EQUS BATT* (they contain Excel-calculated columns; use the raw exports instead), and any pivot sheet (TD, TD_FORM, TD_*, LINEAS DE PDCTO, etc.). The pivots are useful only to validate results.

---

## 3. Vocabulary

| Term | Meaning |
|---|---|
| **BE** | *Batería Ensamblada*, assembled battery (product line 2201, item prefix 221/223) |
| **BF** | *Batería Formada*, formed battery: electrically charged (3101, prefix 231/232) |
| **BS** | *Batería Semidecorada*, semi-decorated (3102, prefix 241) |
| **BT** | *Batería Terminada / Decorada*, finished, customer-branded (3103, prefix 311–313) |
| **Imported** | Finished imported battery (3104, prefix 321/322) |
| **OREC** | Area making oxide, grids, lead strip and pasted plates |
| **F&A** | Formation and Finishing (lines FAFORM*, FADECO*) |
| **RC** | Recycling: crushing, furnaces, refining |
| **RCT-WO** | QAD transaction "receipt from work order", i.e. reported production |
| **Campo Equs** | Custom QAD item attribute that assigns an item to an EQ category (SLI-S, TRUCK-B, OXIDO, PLACASLI, CAJA SLI…) |
| **Cuenta STATS** | Code of an EQ driver in the corporate report (OXPROD_KG, COSPRODU_TRUCK…) |
| **Active material** | Positive paste (becomes PbO₂ during formation), in kg |
| **Decoration element** | Label, terminal cap, terminal cover, post protector, warranty card, sticker |
| **Línea de productos** | QAD product line code, which identifies the process level (see §3.1) |
| **Línea Producción** | QAD production line that reported the receipt (ENSLIN01, OEMPBM01…) |

### 3.1 Product line codes

1100 bushings; 1102–1109 lead raw materials and scrap; 1200 covers; 1201 containers; 1202 handles and accessories; 1204 vent caps; 1300–1309 chemicals, acid, polypropylene; 1401 labels; 2101 lead oxide and pastes; 2102 grids; 2103 lead strip; 2104 Cominco plates; 2105 JCI casting plates; 2106 Sovema expanded plates; 2201 assembled batteries; 3101 formed batteries; 3102 semi-decorated; 3103 decorated; 3104 imported finished.

### 3.2 Item code prefixes

111 bushings · 113–117 lead · 121–122 covers · 131 containers · 141–142 caps and handles · 150–157 raw materials · 160–162 paper and separators · 171–174 labels, warranty cards, stickers · 211 oxide and pastes · 212 strip · 213 grids · 214/215/216 plates · 221/223 BE · 231/232 BF · 241 BS · 311–313 BT · 321–322 imported. **Item codes are text, not numbers**: `21500000A` and `21100000A` exist (a fictitious plate and paste used for imported batteries).

---

## 4. How the Excel computes equivalent batteries

### 4.1 The general equation

For every driver *k*, called a *cuenta STATS*:

> **EQ_k = Q_k × (m_k ÷ s_k) × w_k**

- **Q_k**: physical quantity of the month that belongs to driver *k* (kg or units).
- **s_k**: standard consumption per standard battery (for example 6.08 kg of oxide, or 84 plates).
- **m_k**: complexity multiplier (for example 2.15 for a truck battery; 1 when not applicable).
- **w_k**: weight of the process in the plant's conversion cost.

> **EQUS BATT = Σ_k EQ_k** over the drivers that add to the total.

The factor **f_k = m_k ÷ s_k × w_k** is the EQ value of one physical unit. The weights add up to exactly 100 %: OREC 34.6 % (oxide 7.4, grids 5.7, negative strip 3.7, plates 17.8) + Assembly 35.4 % + F&A 25.9 % (Formation 14.6, Decoration 11.3) + Shipping 4.1 %. Positive strip and casting grids share the same 5.7 %.

### 4.2 Driver table (sheet *RESUMEN EQU* of *EQUS BATT*)

The quantity of each driver is in column D, the factor in column G and the EQ in column H. "Selection" describes which production rows feed the driver.

| Row | Cuenta STATS | Process | Selection (what Q_k sums) | s_k | m_k | w_k | Factor f_k |
|---|---|---|---|---|---|---|---|
| 2 | OXPROD_KG | OREC | Campo Equs = OXIDO (kg) | 6.08 | 1 | 7.4 % | 0.012171 |
| 3 | GRIDS_REG_POS | OREC | Campo Equs = CINTA(+) (kg of positive strip) | 42 (84 × 0.5) | 23.634 grids/kg | 5.7 % | 0.032074 |
| 4 | GRIDS_CAST | OREC | Product line 2102, grids (units) | 42 | 1.33 | 5.7 % | 0.001805 |
| 5 | GRIDS_NEG | OREC | Campo Equs = CINTA(-) (kg of negative strip) | 42 | 25.28 grids/kg | 3.7 % | 0.022270 |
| 6 | PLATEPRD_SLI | OREC | Campo Equs = PLACASLI (all plate lines) | 84 | 1 | 17.8 % | 0.002119 |
| 7 | PLATEPRD_TRUCK | OREC | Campo Equs = PLACA HD | 84 | 1.14 | 17.8 % | 0.002416 |
| 8 | COSPRODU_SLI | Assembly | Product line 2201 **except** Campo Equs TRUCK-B | 1 | 1 | 35.4 % | 0.354 |
| 9 | COSPRODU_TRUCK | Assembly | Product line 2201 **and** Campo Equs TRUCK-B | 1 | 2.15 | 35.4 % | 0.7611 |
| 10 | POS_ACT_MTL | Formation | Product line 3101; sums **quantity × kg of positive paste per BF** | 3.39 | 1 | 14.6 % | 0.043068 |
| 14 | COMP_DEC | Decoration | Product lines 3102, 3103, 3104; sums **quantity × decoration pieces per battery** | 4.29 | 1 | 11.3 % | 0.026340 |
| 15 | CARTON_BATT | Decoration | Batteries packed in cartons; **hard-coded to 0** | 4.29 | 12.8 | 11.3 % | 0.337156 |
| 16 | UNITSHIP | Shipping | Invoice lines of product lines 3103 and 3104, channel Venta (net of credit notes) | 1 | 1 | 4.1 % | 0.041 |
| 11–13 | 15BATPRD, FORM_PRD_SLI, FORM_PRD_TRUCK | Info only | Formed batteries (3101), split by TRUCK-B | — | — | 0 | does not add |
| 18 | LEADPROD | Info only | Refined lead: production lines starting with RCREFI, in tonnes | — | — | 0 | does not add |

Totals in the same sheet: EQUS BATT (row 36), EQUS OREC (38), EQUS ENSAMBLE (39), EQUS F&A (40), EQUS SHIPPING (41), check that the total equals the sum of the parts (42), EQUS FORMACION (44), EQUS DECORADO (45). KPIs: kg of active material per formed battery (48), decoration pieces per decorated battery (50). Column M holds a previous close pasted by hand (it matches Nov-2025) and column N = M − E is the variance. Cell E1 says "ESTA SE COPIA": column E is what gets copied into the official report.

**Interpretation of the constants** (the workbook does not label them; confirm with Finance before changing): 6.08 kg of oxide per standard battery; 84 plates per standard battery; 42 = 84 × 0.5 grids per battery (one grid yields two plates); 23.634 and 25.28 grids per kg of positive and negative strip; a casting grid counts as 1.33 expanded grids; a truck plate counts as 1.14 SLI plates; a truck battery counts as 2.15 SLI batteries; 3.39 kg of positive paste per standard battery; 4.29 decoration pieces per standard battery; a carton counts as 12.8 pieces.

**Note on TRUCK-S:** only TRUCK-B gets the 2.15 multiplier. TRUCK-S assembled batteries (234,906 in FY26) count as SLI. This may be intentional; flag it, do not change it.

### 4.3 Where each quantity comes from in the Excel

1. QAD production receipts are pasted into **REPORTE QAD**.
2. The pivot **LINEAS DE PDCTO** sums column U (*Cambiar cantidad de ubic*) by *Descripción* (product line description) and *Campo Equs*. Rows 2–9 and 11–13 of RESUMEN EQU look values up in that pivot.
3. **Formation (row 10):** column AZ of REPORTE QAD multiplies the quantity of each product-line-3101 row by the kg of positive paste of that item, looked up in the pivot *TD_FORM* of *BOM BATERIAS FORMADAS*. The pivot *TD MAT ACTIVO* sums AZ.
4. **Decoration (row 14):** column AY multiplies the quantity of each 3102/3103/3104 row by the decoration pieces of that item, looked up in the pivot *TD* of *ESTRUCTURA DECORADO*. The pivot *TD COMP DECO* sums AY.
5. **Shipping (row 16):** the pivot *TD_UNIT_Ship* sums *Cantidad fac* of Unit_Ship.
6. **Refined lead (row 18):** the pivot *REFINO* sums the quantity by production line RCREFI01–03.
7. **EQUS POLY (rows 20–34):** for warehouse 1010 only, column BE looks up the poly category of the item in *Clasif_Poly* (its Campo Equs), BG the cuenta STATS and BF = quantity × FACTOR EQU. RESUMEN EQU sums BF by category.

Each production row is assigned to a month by **Fecha Efectiva** (column A, the accounting date), not by the entry date (column F). They differ in 10,711 rows.

### 4.4 Formation conversion: kg of positive paste per formed battery

This is a bottom-up walk through the product structure, one level per sheet of *BOM BATERIAS FORMADAS*:

1. **PLACAS.** For each plate, the rows whose component description starts with "PASTA" give the kg of paste per plate. Example: plate 216000006 *PLACA LN (+) SOV 63A* consumes 0.0636 kg of paste 211000003 *PASTA AUTOMOTRIZ POSITIVA EXPANDED* (cell P128). That number is the engineering standard stored in QAD; Excel does not compute it.
2. **BAT ENS** (assembled batteries). For each plate component of a BE: paste per battery = plates per battery × paste per plate. Positive plates fill column X *PASTA (+)* and negative plates fill column Y *PASTA (−)*. Which column gets the formula was decided by hand, row by row (the plate polarity is visible in the description as "(+)" or "(−)"). Example: BE 221000001 uses 36 positive plates × 0.0636 = 2.2896 kg and 30 negative plates × 0.066 = 1.98 kg. Columns Z and AA extract the plate technology (SOV, JCI, COM) from the description.
3. **BAT FOR** (formed batteries). Formation adds no paste, so the BF inherits the paste of its BE component: column X sums column X of BAT ENS for that BE. Column AB *CALCULO* marks the row that carries the BE: "SI" (1,251 rows) when paste was found, "NO" (105 rows) when the BE is missing from BAT ENS (paste 0, so those batteries add nothing to Formation).
4. **TD_FORM** (pivot, filter CALCULO = SI): formed battery → Σ PASTA (+). 1,248 items, mean 3.26 kg, range 0.79–24.7.

Only the **positive** paste enters the EQ calculation. The standard 3.39 kg is also positive paste. The negative paste is computed but unused.

**Imported batteries:** BFs 232xxxxx are formed from imported crude BEs 222xxxxx. Those BEs use a fictitious plate 21500000A *PLACA PARA BAT IMPO* (60 per battery) with a fictitious paste 21100000A *PASTA BAT IMPO* at 0.058 kg, which gives 3.48 kg. The author placed it in the positive column, so treat it as positive (a documented assumption).

### 4.5 Decoration conversion: pieces per decorated battery

In sheet **Datos** of *ESTRUCTURA DECORADO*, each BOM row of a 3102/3103/3104 battery gets column X *ELEMENTO DECORACION* by this rule:

- If the component's product line is 1202 **and** its description starts with "SOBRECUB" or "MANIJA" → not an element.
- Else, if the component's product line is 1401 (labels) **or** its description starts with "TAPA ", "CUBRE", "POST PROTECTOR", "TERM-PRT" or "TERMINAL-PRT" → look the component up in the catalogue **ELEMENT DECO**, column H ("SI"/"NO").
- Otherwise → not an element.

The component's product line (column Y) comes from the external Item_Master (column 4 of its Datos sheet). The pivot **TD** (filters: ELEMENTO DECORACION = SI, Fin Efectivo blank) sums *Cantidad Por* per parent: **decoration pieces per battery** (4,889 items, mean 4.3, range 0–9).

**Defect:** from row 29,261 on (1,531 rows, mostly imported 3104), the formula in column X omits "TERM-PRT" and "TERMINAL-PRT", so those terminal protectors are not counted. The target applies the full rule to every row.

### 4.6 EQUS POLY

For production rows of warehouse 1010: **EQ = quantity × FACTOR EQU of the item** (from *Clasif_Poly*). The unit is one SLI container. Category factors (table Q1:S13 of Clasif_Poly): CAJA SLI 1.00 · CAJ TRUC 3.37 · CAJA 31 1.54 · CUB 1P 0.51 · CUB 2P 0.58 · CUB TRUC 1.31 · SOBRECUB 0.54 · MANIJA 0.11 · ACCMED 0.35 · ACCESOR 0.05 · TAPONES 0.05 · PELLET 0. EQUS POLY is **not** added to EQUS BATT.

Conflict: a side table in RESUMEN EQU (R19:T31) says TAPONES = 0.28, while Clasif_Poly and the formula actually used say 0.05. Use 0.05 and flag it.

---

## 5. Target architecture

### 5.1 Layers and flow

1. **Extract**: run the three QAD explorations (production RCT-WO, invoices and, when they change, product structures) and obtain the item master.
2. **Bronze**: land every sheet row unchanged as JSONB, with full provenance.
3. **Silver**: parse, type, deduplicate, conform and connect. Build the two conversion tables (TD_FORM, TD_DECO). Maintain the configuration of the equation (factors, rules, versions).
4. **Gold**: compute the EQ chain as views, run data-quality checks and freeze the official monthly result.
5. **Consume**: reports, conversion cost per EQ, energy per EQ (joining plant areas to the energy database).

### 5.2 The equation as data

The equation of §4.1 is stored as data, not code:

- **silver.instrucc** defines **Q_k**: which production or invoice rows belong to each driver.
- **silver.factores_equ** defines **s_k, m_k, w_k**: the stored factor f_k is generated from them, and a "source" column says what to sum (plain quantity, quantity × paste, quantity × pieces, or invoiced units).
- **silver.equs_version** gives each set of rules a validity period, so every month is computed with the rules in force at that time.
- The gold views always do the same thing: filter with INSTRUCC, sum, multiply by the factor of the version in force.

### 5.3 Translation from Excel to the database

| Excel element | Database element |
|---|---|
| Pasting exports into REPORTE QAD / Unit_Ship | Loader into bronze.raw_rct_wo / bronze.raw_facturas, then silver.reporte_qad / silver.unit_ship |
| Sheets PASTA, PLACAS, BAT ENS, BAT FOR, BAT TERM, Datos (ESTRUCTURA) | One table, silver.bom_estructura |
| Pivot TD_FORM (BOM workbook) | silver.td_form (materialized view) |
| Datos column X + pivot TD (ESTRUCTURA) | silver.td_deco (materialized view) |
| Columns AY, AZ, BE, BF, BG of REPORTE QAD | gold.v_reporte_qad |
| Pivot LINEAS DE PDCTO | gold.lineas_de_pdcto |
| RESUMEN EQU column D and the pivots that feed it | gold.v_cantidad_cuenta (driven by silver.instrucc) |
| RESUMEN EQU column G (factors) | silver.factores_equ |
| RESUMEN EQU column H (EQ per driver) | gold.v_resumen_equ |
| RESUMEN EQU rows 36–45 (totals by process) | gold.v_equs_batt_mes |
| Pivot INYECCION (EQUS POLY) | gold.inyeccion |
| "ESTA SE COPIA" (copying column E) and column M | gold.resumen_equ (frozen monthly close) |
| (not in Excel) | gold.v_costo_por_eq, data-quality views |

---

## 6. Target schema

All identifiers are lowercase snake_case and mirror the Excel sheet and column names. Types are described in words. "PK" = primary key, "FK" = foreign key. Columns marked *lineage* reference the Bronze run that produced the row.

### 6.1 Bronze (schema `bronze`)

Bronze stores rows **verbatim**: a JSONB `payload` whose keys are the original header texts (repeated headers get a suffix " #2", " #3"; blank headers use the column letter) and whose values are the cell text as exported. No numeric or date parsing, "N/A" stays "N/A", duplicates are kept, rows are append-only (UPDATE is rejected; only retention may delete).

| Table | Content |
|---|---|
| `ingestion_runs` | One row per load: flow name, source key (one of qad_rct_wo, qad_facturas, qad_product_struc, item_master, manual_sheets), source system, plant code, state (pending/running/completed/failed/cancelled), business window (Fecha Efectiva range), QAD program / exploration / user / query timestamp (from the *Info* sheet), search conditions as a JSON array (from *Condiciones de búsqueda*), timestamps, row count, error, parameters. |
| `raw_files` | One row per archived workbook: unique SHA-256 of the bytes, storage bucket and object key, file name, content type, size, plant code, source key, list of sheet names, uploader, upload time. |
| `raw_rct_wo` | Rows of *Explorar transacciones* › Datos |
| `raw_facturas` | Rows of *Unidades facturadas* › Datos |
| `raw_product_struc` | Rows of BOM › PASTA, PLACAS, BAT ENS, BAT FOR, BAT TERM and ESTRUCTURA › Datos |
| `raw_item_master` | Rows of Item_Master › Datos and Grupo-Fam |
| `raw_manual_sheets` | Rows of hand-maintained catalogues, with a `dataset_key`: clasif_poly, lineas_produccion, instrucc (no header row), element_deco, comparativo_lineas |

Every landing table has: a UUID id, `ingested_at`, `ingestion_run_id`, `raw_file_id`, `source_system`, `source_uri`, `sheet_name`, `source_row` (Excel row number, at least 1), `content_hash` (SHA-256 of the canonical JSON payload, indexed but **not** unique), `plant_code` and `payload`. The primary key is (id, ingested_at), so the tables can become TimescaleDB hypertables later.

### 6.2 Silver — catalogues

**silver.planta** — one row per plant area.

| Column | Type | Origin |
|---|---|---|
| planta | text, PK | *Comparativo lineas* › Hoja1 column B (RC, Componentes, OREC, Ensamble, F&A) |
| nombre | text | Same value, or a descriptive name maintained by hand |

**silver.almacen** — one row per warehouse.

| Column | Type | Origin |
|---|---|---|
| almac | text, PK | RCT-WO column H *Almac* (1010, 1515, 1520, 1521) |
| nombre | text | Maintained by hand (1010 plastic components, 1515 battery plant, 1520/1521 recycling) |

**silver.lineas_produccion** — one row per QAD production line.

| Column | Type | Origin |
|---|---|---|
| linea | text, PK | *EQUS BATT* › Lineas Produccion column A *Línea*; plus any line present in RCT-WO column AV |
| descripcion | text | Lineas Produccion column C |
| almac | text, FK almacen | Lineas Produccion column B |
| planta | text, FK planta | *Comparativo lineas* column B, joined on column D = linea. Homologate `OEMPSOV` → `OEMPSOV1`. Lines missing from the Comparativo stay empty and are reported. |
| grupo | text | *EQUS BATT* › L.PRODUCCION column I *GRUPO*, joined on column K |
| proceso_energia | text | *Comparativo lineas* column C |
| unidades_por_hora | decimal | Lineas Produccion column D |

**silver.linea_de_productos** — one row per product line.

| Column | Type | Origin |
|---|---|---|
| linea_de_productos | text, PK | RCT-WO column N; invoices column T |
| descripcion | text | RCT-WO column O; invoices column U |

**silver.campo_equs** — one row per EQ category.

| Column | Type | Origin |
|---|---|---|
| campo_equs | text, PK | Distinct values of RCT-WO column AS, invoices column AS and Clasif_Poly column I. **"N/A" is a valid value meaning "not applicable", not a null.** |
| descripcion | text | Maintained by hand |

**silver.grupo_fam** — battery family groups.

| Column | Type | Origin |
|---|---|---|
| familia_placa | text, PK | Item_Master › Grupo-Fam column A (the value found in Item_Master › Datos column G, for example "LBN2-9 AX") |
| tipo_bat | text | Grupo-Fam column D (LIVIANA, MEDIANA, PESADA, U1) |
| familia | text | Grupo-Fam column F (24, LBN2, 4DLT…) |

**silver.item_master** — one row per item, of any kind. Until the real Item_Master is available, it is assembled from every workbook; the first source in this order wins for each attribute: RCT-WO, invoices, product structures (as parent, then as component), ELEMENT DECO, Clasif_Poly.

| Column | Type | Origin |
|---|---|---|
| numero_articulo | text, PK | RCT-WO B; invoices V; structures A (parent) and H, or I for PASTA/BAT TERM (component); ELEMENT DECO A; Clasif_Poly A; Item_Master Datos A |
| descripcion | text | RCT-WO C; invoices W; structures B (parent) or *Descripción Componente*; ELEMENT DECO B; Clasif_Poly B |
| descripcion2 | text | Column C of BAT FOR, BAT ENS, PASTA and BAT TERM: the second half of the QAD description, parents only. In PLACAS and ESTRUCTURA Datos, column C is the unit of measure. |
| unidad_de_medida | text | RCT-WO AA; invoices X; structures (PLACAS C, PASTA/BAT TERM H, Datos C; UMCOMP for components); ELEMENT DECO C; Clasif_Poly C |
| linea_de_productos | text, FK | RCT-WO N; invoices T; structures G (parent). **For decoration components: ESTRUCTURA › Datos column Y overrides** (it comes from Item_Master). ELEMENT DECO D; Clasif_Poly D. |
| campo_equs | text, FK | RCT-WO AS; invoices AS |
| tipo_de_articulo | text | Structures E (Datos: F); Clasif_Poly F. Overloaded field: brand for BT, technology for BE/BF, polarity or technology for plates, polarity for pastes. |
| grpo | text | Structures F (Datos: E); Clasif_Poly G |
| familia_placa | text, FK grupo_fam | Item_Master Datos column G (empty until the file is available) |
| peso_neto | decimal | RCT-WO AT |
| polaridad | "+" or "−" | **Not in the Excel; derived.** For pastes: tipo_de_articulo POSITVO/NEGATIVO (sheet PASTA column E) first, otherwise the description (NEG → −, otherwise +; "PASTA BAT IMPO" is + by documented assumption). For plates: "(+)"/"(−)" in the description, or tipo_de_articulo when it is POSITVO/NEGATIVO; "PLACA PARA BAT IMPO" is +. Empty for other items. |
| estado | text | Structures D |

**silver.costo_std** — standard cost history.

| Column | Type | Origin |
|---|---|---|
| numero_articulo | text, PK, FK | RCT-WO B; structures A/H |
| vigente_desde | date, PK | Fecha Efectiva of the RCT-WO row (AR changes over time) or the structure's *Fecha Modificada* |
| vigente_hasta | date | Derived: next vigente_desde |
| costo_std | decimal | RCT-WO AR *Precio* (more precise than AP); structures *COSTO_STD* |

**silver.vendido_a** — customers.

| Column | Type | Origin |
|---|---|---|
| vendido_a | text, PK | Invoices J |
| ordenar_nombre | text | Invoices K |
| pais | text | Invoices P (normalise "COLOMBIA"/"Colombia") |
| tipo | text | Invoices L (NAL, EXT, IBT, PERU) |

### 6.3 Silver — structures and catalogues of the calculation

**silver.bom_estructura** — one row per parent→component link, all levels in one table. An item is the parent of some rows and the component of others; this chains the levels BT → BF → BE → plate → paste → oxide → lead.

| Column | Type | Origin (BAT FOR, BAT ENS, PLACAS, ESTRUCTURA Datos / PASTA, BAT TERM) |
|---|---|---|
| bom_id | identity, PK | Generated |
| articulo_matriz | text, FK item_master | Column A / A — *Artículo Matriz* |
| articulo_componente | text, FK item_master | Column H / I — *Artículo Componente* |
| cantidad_por | decimal (8 decimals) | Column P / Q — *Cantidad Por* (negative = recovered scrap) |
| umcomp | text | Column J / K |
| inicio_efectiva | date | Column N / O |
| fin_efectivo | date | Column O / P (empty = in force) |
| desp | decimal | Column R / S (scrap %) |
| operacion | integer | Column S / T |
| referencia | text | Column K / L |
| fecha_modificada | date | Column T / U |
| id_del_usuario | text | Column U / V |
| calculo | "SI"/"NO" | BAT FOR column AB *CALCULO*; "SI" for every other row |
| source_sheet | text, lineage | Bronze sheet_name |
| source_run_id | uuid, lineage | Bronze ingestion_run_id |

Not carried: Excel-calculated columns (Costo_Tot, PASTA (+/−), TECNO, ELEMENTO DECORACION, LINEA DE PRODUCTO), COSTO_STD (goes to costo_std) and descriptions (go to item_master). Sheets *BAT SEMIDECO* and *Bat sin Element* duplicate Datos and are not used.

**silver.element_deco** — decoration catalogue.

| Column | Type | Origin |
|---|---|---|
| numero_articulo | text, PK, FK | ESTRUCTURA › ELEMENT DECO column A |
| grpo | text | Column G (ETIQUETA FRONTAL, ETIQUETA SUPERIOR, ETIQUETA GARANTIA, ESTICKER…) |
| tipo_de_articulo | text | Column F |
| elemento_deco | boolean | Column H ("SI" → true) |

**silver.clasif_poly** — plastic piece factors.

| Column | Type | Origin |
|---|---|---|
| numero_articulo | text, PK, FK | *EQUS BATT* › Clasif_Poly column A |
| campo_equs | text | Column I |
| cuenta_stats | text | Column J |
| factor_equ | decimal | Column K (must agree with the category table Q1:S13) |

**silver.td_form** (materialized view) — kg of paste per formed battery. Replaces the TD_FORM pivot.

| Column | Type | Derivation |
|---|---|---|
| articulo_matriz | text, PK | BF item (product line 3101) |
| pasta_pos | decimal | Σ over BOM rows of the BF whose component has paste, with calculo = SI: cantidad_por(BF→BE) × Σ over the BE's plates [cantidad_por(BE→plate) × Σ positive-paste cantidad_por(plate→paste)] |
| pasta_neg | decimal | Same with negative paste (informative) |

**silver.td_deco** (materialized view) — decoration pieces per battery. Replaces Datos column X + pivot TD.

| Column | Type | Derivation |
|---|---|---|
| articulo_matriz | text, PK | Item of product line 3102, 3103 or 3104 |
| comp_deco | decimal | Σ cantidad_por of in-force BOM rows (fin_efectivo empty) whose component satisfies the rule of §4.5 and is an element in silver.element_deco |

**silver.costo_conversion** — conversion cost from accounting. Not in these workbooks; empty until provided.

| Column | Type | Origin |
|---|---|---|
| mes | date, PK | First day of the month |
| planta | text, PK, FK | Plant area |
| proceso | text, PK | OREC, ENSAMBLE, FORMACION, DECORADO, SHIPPING |
| costo_cop | decimal | Accounting close |

### 6.4 Silver — movements

**silver.reporte_qad** — one row per production receipt. Natural key: *Número de transacción* (unique in the data).

| Column | Type | Origin (*Explorar transacciones* › Datos = REPORTE QAD) |
|---|---|---|
| numero_de_transaccion | bigint, PK | D *Número de transacción* |
| tipo_transaccion | text | E (RCT-WO) |
| fecha_efectiva | date | A *Fecha Efectiva* (**defines the month**) |
| fecha | date | F *Fecha* (entry date) |
| tiempo | text | G |
| numero_articulo | text, FK item_master | B |
| almac | text, FK almacen | H |
| ubicacion | text | I |
| lote_serie | text | J |
| orden | text | L (work order) |
| id | bigint | M (work order id) |
| linea_produccion | text, FK lineas_produccion | AV (2,518 rows from program icunrc01.p have no line; keep them) |
| cambiar_cantidad_de_ubic | decimal | U — **the quantity used everywhere** (do not use T, it differs in 41 rows); 596 negative reversals are kept |
| unidad_de_medida | text | AA |
| precio | decimal | AR (standard unit cost) |
| costo_total | decimal | AQ (= U × AR) |
| campo_equs | text | AS — snapshot at export time, "N/A" kept as text |
| peso_neto | decimal | AT |
| status_inventario | text | AC |
| program | text | AL |
| id_del_usuario | text | AM |
| source_run_id | uuid, lineage | Bronze run |

**silver.unit_ship** — one row per invoice line. Natural key: (*Núm Fact*, *Orden*, *Línea*), unique in the data.

| Column | Type | Origin (*Unidades facturadas* › Datos = Unit_Ship) |
|---|---|---|
| num_fact | text, PK | B |
| orden | text, PK | C |
| linea | integer, PK | R |
| remision | text | F |
| canal | text | H |
| fecha_de_la_factura | date | I |
| fecha_efectiva | date | AT (**defines the month**) |
| vendido_a | text, FK vendido_a | J |
| embarcar_para | text | N |
| numero_articulo | text, FK item_master | V |
| cantidad_fac | decimal | Y (negative = credit note) |
| precio_de_lista | decimal | AB |
| porcentaje_descuento | decimal | AE |
| precio_neto | decimal | AI |
| monto_factura | decimal | AJ (transaction currency) |
| moneda | text | AK |
| tipos_de_cambio | decimal | AL |
| importe_de_factura_mb | decimal | AM (COP) |
| total_plomo | decimal | AR (kg of lead) |
| campo_equs | text | AS |
| source_run_id | uuid, lineage | Bronze run |

### 6.5 Silver — configuration of the equation

**silver.equs_version** — sets of rules with validity. Not in the Excel.

| Column | Type | Origin |
|---|---|---|
| version | integer, PK | Generated |
| nombre | text | For example "STD FY26" |
| vigente_desde | date | First month the version applies |
| vigente_hasta | date | Last month (empty = open) |
| aprobado_por | text | Who approved it |

**silver.factores_equ** — one row per driver and version. Seed with the values of §4.2 as version 1, valid from 2025-10-01.

| Column | Type | Origin |
|---|---|---|
| version | integer, PK, FK | — |
| cuenta_stats | text, PK | RESUMEN EQU column A |
| proceso | text | Grouping of rows 38–45: OREC, ENSAMBLE, FORMACION, DECORADO, SHIPPING, or INFO |
| descripcion | text | RESUMEN EQU column B |
| unidad | text | KG or UN |
| fuente | one of PRODUCCION, MAT_ACTIVO, COMP_DECO, FACTURACION | What to sum: quantity, quantity × pasta_pos, quantity × comp_deco, or invoiced units |
| estandar_por_bat | decimal | The divisor in column G |
| multiplicador | decimal | The multiplier in column G |
| peso | decimal | The percentage in column G (0 for informative drivers) |
| factor | decimal, generated | multiplicador ÷ estandar_por_bat × peso |
| suma_eq | boolean | False for 15BATPRD/FORM_PRD_*/LEADPROD |

**silver.instrucc** — which rows feed each driver. Within one row all non-empty conditions must hold (AND); several rows of the same driver add up (OR).

| Column | Type | Origin |
|---|---|---|
| version, cuenta_stats | PK + FK factores_equ | — |
| regla_n | integer, PK | Rule number |
| linea_de_productos | text | Condition on the product line (empty = any) |
| campo_equs | text | Condition on Campo Equs |
| campo_equs_excluir | text | Campo Equs that must not match |
| prefijo_linea | text | The production line must start with this text |
| signo | +1 / −1 | Sign of the contribution |

Seed rules (version 1): OXPROD_KG ← campo OXIDO; GRIDS_REG_POS ← campo CINTA(+); GRIDS_CAST ← product line 2102; GRIDS_NEG ← campo CINTA(-); PLATEPRD_SLI ← campo PLACASLI; PLATEPRD_TRUCK ← campo PLACA HD; COSPRODU_SLI ← line 2201 excluding TRUCK-B; COSPRODU_TRUCK ← line 2201 and TRUCK-B; POS_ACT_MTL ← line 3101; COMP_DEC ← lines 3102, 3103, 3104 (three rules); UNITSHIP ← invoice lines 3103, 3104 with channel Venta (two rules); FORM_PRD_SLI ← 3101 excluding TRUCK-B; FORM_PRD_TRUCK ← 3101 and TRUCK-B; LEADPROD ← production line prefix RCREFI; CARTON_BATT has no rule.

Origin in the Excel: the formulas of RESUMEN EQU column D, the text of sheet INSTRUCC (column D) and the filters of the pivots that feed RESUMEN EQU.

### 6.6 Gold (schema `gold`)

| Object | Kind | Columns | Meaning / Excel equivalent |
|---|---|---|---|
| v_reporte_qad | view | every reporte_qad column + mes, linea_de_productos, campo_equs_eq (snapshot, or item master when null), mat_activo = quantity × td_form.pasta_pos, comp_deco = quantity × td_deco.comp_deco, new_equ / cuenta / valor_equ (= quantity × clasif_poly.factor_equ) for warehouse 1010 only | REPORTE QAD with columns AZ, AY, BE, BG, BF |
| lineas_de_pdcto | view | mes, linea_de_productos, descripcion, campo_equs, cantidad, mat_activo, comp_deco | Pivot LINEAS DE PDCTO, per month |
| v_cantidad_cuenta | view | version, cuenta_stats, mes, cantidad | Q_k: v_reporte_qad (or unit_ship for FACTURACION) filtered by instrucc |
| v_resumen_equ | view | mes, version, proceso, cuenta_stats, descripcion, cantidad, factor, equs, suma_eq | RESUMEN EQU column H, with the version in force for the month |
| v_equs_batt_mes | view | mes, equs_orec, equs_ensamble, equs_formacion, equs_decorado, equs_shipping, equs_batt | RESUMEN EQU rows 36–45 |
| inyeccion | view | mes, cuenta, new_equ, piezas, valor_equ | Pivot INYECCION (EQUS POLY) |
| v_costo_por_eq | view | mes, proceso, equs, costo_cop, cop_por_eq | New: conversion cost ÷ EQ |
| resumen_equ | table | mes (PK), version (PK), cuenta_stats (PK), cantidad, equs, fecha_calculo | The frozen official close ("ESTA SE COPIA") |
| dq_* | views | — | Data-quality checks run before closing (§8) |

---

## 7. Known data defects and how each layer handles them

| Defect | Where | Bronze | Silver / Gold |
|---|---|---|---|
| Repeated structure links: 7 links in PLACAS (a second block from another export pasted below, with different standard cost — for example plate 215000015 at rows 92 and 191), 13 identical rows in BAT ENS, 11 in BAT FOR, 790 in ESTRUCTURA Datos | Product structures | Kept verbatim | Deduplicate by **business key** (parent, component, inicio_efectiva, fin_efectivo, cantidad_por), keeping the row of the latest run. This fixes Formation (−37,940 EQ in FY26). |
| Second header row pasted inside PLACAS (row 257) | PLACAS | Kept | Discard rows whose *Artículo Matriz* equals the header text |
| "N/A" in Campo Equs (6,582 rows: lead, chemicals, plus 1,495 covers/handles that should have a category) | RCT-WO AS | Kept as text | Keep "N/A" as a value; report items whose Clasif_Poly category disagrees |
| Alphanumeric item codes (21500000A, 21100000A) | Structures | Kept | Item codes are text |
| Datos column X formula changes at row 29,261 | ESTRUCTURA | n/a | Apply the full rule uniformly |
| TAPONES factor 0.28 vs 0.05 | RESUMEN EQU vs Clasif_Poly | n/a | Use 0.05; flag |
| Strip yield 19.72/23.48 in INSTRUCC vs 23.634/25.28 in formulas | EQUS BATT | n/a | Use 23.634/25.28 |
| CARTON_BATT hard-coded 0 | RESUMEN EQU E15 | n/a | Driver exists without rules (EQ 0) |
| Stale *Condiciones de búsqueda* (says Oct–Dec 2025, data covers FY26) | Explorar transacciones | Store as delivered; let the run window be overridden | Use Fecha Efectiva of the rows |
| Lines without plant area: FADECO05, POLINY05, MAQBUJEO (active) + 11 inactive; `OEMPSOV` should be `OEMPSOV1` | Comparativo lineas | Kept | Homologate; report lines without plant |
| 36 % of produced weight has no battery type (TIPO BAT #N/D) | MIX BAT / Item_Master | — | Out of EQUS scope; report |
| Credit notes (203 lines, −49,373 units) | Invoices | Kept | UNITSHIP is net, as in the Excel |

---

## 8. Data-quality checks before closing a month (gold)

1. Production rows of product lines 2101–3104 that match **no** INSTRUCC rule (new or unclassified Campo Equs).
2. Rows matching **two** rules of the same process (double counting).
3. Formed batteries produced without td_form paste, and decorated batteries produced without td_deco pieces.
4. Items whose Campo Equs in QAD disagrees with their Clasif_Poly category.
5. Production lines without a plant area.
6. Month-over-month variation of each driver above a threshold.
7. Structure links duplicated by business key in the latest Bronze runs (count and EQ impact).

---

## 9. Expected results (acceptance tests)

The target values below were produced by a reference implementation of exactly the rules in this document, run on the files in `data/raw_excels/`. If your result differs, a rule was implemented differently: find which before accepting the difference.

### 9.1 September 2026

| Item | Excel (RESUMEN EQU) | Database, Bronze loaded as is (no deduplication) | Database with deduplication (target) |
|---|---|---|---|
| EQ OREC | 71,040.72 | 71,040.72 | 71,040.72 |
| EQ Assembly | 85,014.78 | 85,014.78 | 85,014.78 |
| EQ Formation | 28,202.34 | 28,208.78 | **27,940.86** |
| EQ Decoration | 23,589.45 | 23,615.00 | **23,615.00** |
| EQ Shipping | 7,978.97 | 7,978.97 | 7,978.97 |
| **EQUS BATT** | 215,826.26 | 215,858.25 | **215,590.33** |
| **EQUS POLY** | 503,033.19 | 503,033.19 | 503,033.19 |

September 2026 driver quantities: oxide 1,162,129 kg; positive strip 0; casting grids 9,761,100; negative strip 264,006 kg; SLI plates 14,859,820; HD plates 790,407; SLI assembled 205,185; truck assembled 16,265; active material 654,835.08 kg in the Excel and 648,763.763 kg deduplicated; decoration pieces 895,564 in the Excel and 896,534 with the full rule; formed SLI 194,846 and formed truck 14,816; units shipped 194,609; refined lead 1,806,206 kg (the Excel shows it in tonnes: 1,806.206).

Differences explained: Formation is lower after deduplication because of the repeated plate 215000015 in PLACAS. Decoration is higher because the full decoration rule counts the TERM-PRT protectors of imported batteries. Formation +6.4 in the middle column comes from one duplicated BAT FOR row (231001483).

### 9.2 FY26 by month, target (deduplicated)

| Month | OREC | Assembly | Formation | Decoration | Shipping | EQUS BATT |
|---|---|---|---|---|---|---|
| 2025-10 | 96,087.03 | 113,560.03 | 25,850.60 | 22,594.51 | 11,683.24 | 269,775.41 |
| 2025-11 | 68,626.84 | 74,008.00 | 26,810.80 | 22,471.41 | 7,625.02 | 199,542.07 |
| 2025-12 | 47,843.26 | 61,534.55 | 18,167.56 | 16,960.54 | 7,867.70 | 152,373.59 |
| 2026-01 | 73,321.33 | 80,682.71 | 26,004.14 | 22,657.61 | 7,780.16 | 210,445.95 |
| 2026-02 | 71,903.12 | 78,049.65 | 25,793.29 | 23,659.73 | 7,986.92 | 207,392.72 |
| 2026-03 | 31,579.99 | 36,719.71 | 17,602.28 | 15,550.75 | 5,969.93 | 107,422.66 |
| 2026-04 | 52,322.01 | 55,410.03 | 20,265.43 | 18,345.91 | 6,303.59 | 152,646.95 |
| 2026-05 | 64,957.93 | 74,283.01 | 25,009.54 | 19,518.58 | 8,414.35 | 192,183.40 |
| 2026-06 | 75,433.48 | 81,922.26 | 31,470.00 | 24,206.55 | 9,138.37 | 222,170.65 |
| 2026-07 | 79,039.71 | 81,625.89 | 28,667.21 | 23,561.24 | 9,015.29 | 221,909.32 |
| 2026-08 | 58,159.65 | 52,244.01 | 22,934.64 | 19,058.99 | 6,873.24 | 159,270.54 |
| 2026-09 | 71,040.72 | 85,014.78 | 27,940.86 | 23,615.00 | 7,978.97 | 215,590.33 |
| **FY26** | | | | | | **2,310,724** |

For reference, replicating the Excel exactly (with its defects) gives FY26 = 2,348,656, Formation 334,456 instead of 296,516.

### 9.3 Spot checks of the conversion tables (deduplicated)

| Item | td_form.pasta_pos | td_form.pasta_neg | Note |
|---|---|---|---|
| 231000001 (BF 75 6*5) | 2.2896 | 1.98 | 36 × 0.0636 and 30 × 0.066 |
| 231000902 (BF B19 GN 4.3) | 2.1513 | 2.0472 | The Excel shows 4.3026 because of the duplicated plate |
| 232000008 (imported) | 3.48 | 0 | 60 × 0.058, fictitious import plate |

| Item | td_deco.comp_deco | Note |
|---|---|---|
| 311000006 | 4 | Same as the Excel |
| 311005893 | 2 | The Excel shows 4 because of duplicated Datos rows |
| 321000729 | 6 | The Excel shows 4 because it misses the two TERM-PRT protectors |

### 9.4 Bronze row counts per sheet

Explorar transacciones › Datos 138,773 · Unidades facturadas › Datos 14,458 · PASTA 29 · PLACAS 220 non-empty rows (including the pasted header) · BAT ENS 8,340 · BAT FOR 4,566 · BAT TERM 3,413 · ESTRUCTURA Datos 31,907 · Clasif_Poly 1,753 · Lineas Produccion 64 · INSTRUCC 15 (no header) · ELEMENT DECO 1,751 · Comparativo lineas Hoja1 50.

---

## 10. Open questions for the business (do not decide them in code)

1. Should TRUCK-S assembled batteries get the truck multiplier (2.15), like TRUCK-B?
2. TAPONES poly factor: 0.05 (in use) or 0.28 (side table)?
3. Is the carton driver (CARTON_BATT) still in use? Today it is 0.
4. Plant area of FADECO05, POLINY05 and MAQBUJEO.
5. Confirm that imported paste (PASTA BAT IMPO) counts as positive active material.
6. Should UNITSHIP be net of credit notes (current) or gross?
7. Correct in QAD the Campo Equs "N/A" of covers and handles that do have a poly category.
