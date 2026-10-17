# Dataset specs

A dataset spec says what the app expects of one kind of data people load: the
sheet a file must hold, its columns in order, the type and rules of each
column, how the rows are kept, and how they may be entered. One YAML file per
dataset, in `config/datasets/`.

- **The API reads the specs once, when it starts,** and refuses to start if one
  is malformed. The message names the file, the path in it and the problem.
- **`GET /api/datasets`** serves them to the web app, and
  **`GET /api/datasets/{dataset_key}/template`** returns the empty workbook of
  each ([the API guide](api.md#2-endpoints)). Both need `loads:write`.
- **Nothing checks a file against a spec yet.** The rules a spec declares are
  applied to uploads from increment 5 of the
  [frontend plan](frontend-plan.md#8-build-roadmap).

Built on 2026-10-17, as increment 4.

## The specs today

| Spec | Data | Kind | Entered by | Columns |
|---|---|---|---|---|
| `lineas_produccion` | Production lines: sheet `Lineas Produccion` of the EQUS BATT workbook | snapshot | upload, grid, form | 10 |
| `qad_facturas` | Invoice lines: sheet `Datos` of the QAD export "Unidades facturadas" | movement | upload | 49 |

Both rest on real data. Their headers were copied from the workbooks, and a
test checks them against the workbooks whenever `data/raw_excels/` holds them.
The rules (patterns, ranges, required columns) were measured on the FY26 rows on
2026-10-17: every row satisfies them.

## The format, version 1

```yaml
version: 1

key: lineas_produccion            # lowercase snake_case, equal to the file name
kind: snapshot                    # snapshot or movement
label: datasets.lineas_produccion.name              # keys of frontend/src/i18n/es.json
description: datasets.lineas_produccion.description
entry_modes: [upload, grid, form]

bronze:
  source_key: manual_sheets       # a source of energy.domain.landing.LANDING_SOURCES
  sheet: Lineas Produccion        # exactly as the workbook names it
  dataset_key: lineas_produccion  # for manual_sheets only
  header_row: 1                   # optional, 1 by default

silver:
  table: silver.lineas_produccion # the Silver table the rows feed

natural_key: [Línea]              # payload keys of required columns
# period_column: Fecha Efectiva   # movements only

columns:
  - {header: Línea, type: text, required: true, pattern: "^[A-Z0-9]+$"}
  - {header: Firma Automática, type: text, allowed: ["Sí", "No"]}
```

**The dataset:**

| Field | Required | Rule |
|---|---|---|
| `version` | yes | `1`. A file of another version is refused, not guessed at |
| `key` | yes | Lowercase snake_case, and the file's name: `<key>.yaml` |
| `kind` | yes | `snapshot`: replaced whole on every load (catalogues, product structures). `movement`: rows add up, and a corrected row replaces the old one by its natural key |
| `label`, `description` | yes | Keys of the web app's translation file, never the texts themselves. A test checks that they exist |
| `entry_modes` | yes | Any of `upload`, `grid`, `form`, none twice. A QAD export (a source whose system is `qad`) is only ever uploaded |
| `bronze.source_key` | yes | One of the five Bronze sources |
| `bronze.sheet` | yes | A name Excel accepts, and not a calculated sheet (`REPORTE QAD`, `Unit_Ship`) |
| `bronze.dataset_key` | for `manual_sheets` | Lowercase snake_case. Refused for the other sources, which take none |
| `bronze.header_row` | no | From 1; 1 when absent |
| `silver.table` | yes | `silver.<name>`, a table the Silver build writes. Several datasets may feed one table |
| `natural_key` | yes | Payload keys of required columns, none twice |
| `period_column` | movements only | The payload key of a required date column. For a source that is dated, it must be that source's own column (`Fecha Efectiva`) |
| `columns` | yes | Every column, in the workbook's order |

**Each column:**

| Field | Required | Rule |
|---|---|---|
| `header` | yes | The header text exactly as the workbook writes it: accents, capitals and inner spaces included. No space at either end, because Bronze trims headers |
| `type` | yes | `text`, `integer`, `decimal` or `date`. A date column takes real date cells only |
| `required` | no | `true` makes an empty cell an error. `false` when absent |
| `pattern` | no | Text columns only. A regular expression the whole value must match |
| `allowed` | no | Text columns only. The only values accepted; each quoted, none twice |
| `min`, `max` | no | Number columns only, with `min` not above `max` |
| `severity` | no | `error` (when absent) or `warning`: what breaking `pattern`, `allowed`, `min` or `max` does. Allowed only on a column that has one of them. A wrong type or an empty required cell is always an error |
| `label` | no | A key of the translation file. Without it the app shows the header, which is already the users' own word |

Any field not in these tables is refused, so a typo such as `requried` cannot
pass silently.

## Headers and keys

A spec writes **headers**, never payload keys. The key Bronze stores a column
under is derived from the headers by `energy.domain.landing.header_keys`, the
same function the Bronze loader uses:

- a header is trimmed at both ends, and nothing else is changed;
- a repeated header gets a suffix: the second `Almac` of the invoices is
  `Almac #2`.

So a header is written once, in the spec, and Bronze, the template and
`GET /api/datasets` all derive the same keys from it. Natural keys and the
period column name keys (`Almac #2`), not headers.

**The Silver build reads payload keys as fixed text,** which cannot be
generated from the spec. `tests/unit/test_dataset_specs_committed.py` ties the
two: every key the Silver SQL reads from a dataset's rows must be a key of its
spec. A spec that drops or renames a column Silver reads fails that test.

## The template

`GET /api/datasets/{dataset_key}/template` returns `<dataset_key>.xlsx`:

- one sheet, named as the spec names it;
- the spec's headers on its header row, in order, repeats included;
- no data, and no other sheet.

Read back by the Bronze rules, its header row gives exactly the spec's keys; a
test reads every template that way. To help people type it also formats each
column for its type, adding no cell: text columns as Text, so Excel cannot turn
an item code into a number; date columns as dates; whole numbers without
decimals. A column with `allowed` values offers them in a drop-down list,
unless a value holds a comma or a quote, or the list is too long for Excel.

## Adding a dataset spec

1. **Find the sheet's route.** The spec's source, sheet and dataset key must be
   one the Bronze loader lands (`ROUTES` in
   `src/energy/pipelines/flows/equs_bronze_load.py`). A test fails otherwise.
2. **Copy the headers from the workbook, by script, never by typing them.**
   Accents, double spaces and lookalike characters are invisible on screen.
   Print them with Python's `ascii()`, which shows every character:

   ```bash
   uv run python -c "from python_calamine import CalamineWorkbook as W; \
   [print(ascii(h)) for h in W.from_path('data/raw_excels/<file>.xlsx').get_sheet_by_name('<sheet>').to_python()[0]]"
   ```

3. **Write `config/datasets/<key>.yaml`.** Mark required the columns without
   which Silver drops a row. Add patterns and ranges only where every real row
   satisfies them: a rule that real data breaks will block a monthly load once
   uploads are checked. Leave codes that may one day hold a letter without a
   pattern.
4. **Add the label and description** to `frontend/src/i18n/es.json`, under
   `datasets.<key>`.
5. **Check it:** `energy datasets check` (below), then `make test`.

## Checking a spec

```bash
uv run energy datasets check                          # the specs of CONFIG_DIR
uv run energy datasets check --config-dir <folder>    # a copy being edited
```

It reads the specs with the loader the API uses when it starts, so a spec it
accepts is one the API accepts. It prints one line per spec, or every problem
found and exits 1:

```text
error: dataset specs are invalid (1 problem):
  config/datasets/qad_facturas.yaml: columns[24].type: 'decimel' is not one of text, integer, decimal, date
```

Problems are reported all at once, across files. One exception: while a column
is malformed, the checks that need every column (the natural key, the period
column) wait, and appear on the next run once the columns are sound.

**What else is refused:** YAML that does not parse (with its line), a key
written twice in one mapping (the usual YAML loader would keep the second
silently), a file in the folder that is not `<key>.yaml` (a `.yml` would
otherwise be ignored), two specs with one key, two specs for the same sheet of
the same source, and a missing or empty folder.

**The YAML trap.** YAML reads an unquoted `No`, `Yes`, `On` or `Off` as true
or false. Quote every allowed value: `allowed: ["Sí", "No"]`. The check says so
when it finds one.

## Not done yet

- **Applying the rules to a file** is increment 5: the upload, the file and
  schema checks, and the list of issues.
- **One sheet per spec.** The formed product structures land five sheets under
  one source, and `Clasif_Poly` holds two tables side by side. Increment 12
  decides how to specify them: one spec per sheet, or a version 2 of the
  format.
- **No page shows the specs yet.** The web app lists them and offers the
  template from increment 5, in the first step of the loading wizard.
