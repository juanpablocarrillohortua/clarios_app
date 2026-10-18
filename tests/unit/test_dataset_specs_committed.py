"""The committed dataset specs, against everything else that names the same columns.

A spec's headers are the one list of a dataset's columns. Three other places
name them and cannot be generated from it, so these tests tie them together:

- the Bronze loader's routing table, which lands the same sheet from the CLI;
- the Silver build, whose SQL reads payload keys as fixed text: every key it
  reads from a dataset must be a key of that dataset's spec;
- the web app's translation file, which holds the label each spec names.

The last test reads the real workbooks, when they are present, and checks that
each spec's headers are exactly the workbook's, accents and spaces included.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Final

import pytest

from energy.adapters.calamine_workbook import CalamineWorkbookReader
from energy.adapters.equs_silver_sql import BUILD_STEPS
from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.domain.dataset_spec import DatasetKind, RemovedRows
from energy.domain.landing import LANDING_SOURCES, SheetSpec, cell_text
from energy.pipelines.flows.equs_bronze_load import ROUTES, WorkbookRoute

if TYPE_CHECKING:
    from pathlib import Path

    from energy.domain.dataset_spec import DatasetCatalogue, DatasetSpec

WORKBOOKS: Final = "data/raw_excels"
SILVER_SQL: Final = "\n".join(step.sql for step in BUILD_STEPS)
PAYLOAD_KEY: Final = re.compile(r"payload ->> '([^']+)'")


@pytest.fixture(scope="module")
def catalogue(repo_root: Path) -> DatasetCatalogue:
    return load_dataset_specs(repo_root / "config")


def _route(spec: DatasetSpec) -> WorkbookRoute | None:
    """The loader's route that lands the spec's sheet, if there is one."""
    wanted = SheetSpec(spec.bronze.sheet, spec.bronze.dataset_key, spec.bronze.header_row)
    for route in ROUTES:
        if route.source_key != spec.bronze.source_key:
            continue
        sheets = route.sheets or LANDING_SOURCES[route.source_key].default_sheets
        if wanted in sheets + route.optional_sheets:
            return route
    return None


def _silver_reads(spec: DatasetSpec) -> set[str]:
    """Every payload key the Silver build reads from the spec's rows."""
    if spec.bronze.dataset_key is not None:
        anchor = f"FROM eq_manual WHERE dataset_key = '{spec.bronze.dataset_key}'"
    else:
        table = {"qad_facturas": "raw_facturas", "qad_rct_wo": "raw_rct_wo"}[spec.bronze.source_key]
        anchor = f"FROM bronze.{table}"
    keys: set[str] = set()
    for match in re.finditer(re.escape(anchor), SILVER_SQL):
        # The keys read by the SELECT that this FROM closes.
        select = SILVER_SQL.rfind("SELECT", 0, match.start())
        keys.update(PAYLOAD_KEY.findall(SILVER_SQL, select, match.start()))
    return keys


def test_every_spec_lands_a_sheet_the_loader_lands(catalogue: DatasetCatalogue) -> None:
    for spec in catalogue:
        assert _route(spec) is not None, f"{spec.key}: no route of ROUTES lands this sheet"


def test_silver_reads_no_key_a_spec_lacks(catalogue: DatasetCatalogue) -> None:
    for spec in catalogue:
        read = _silver_reads(spec)

        assert read, f"{spec.key}: the Silver build reads nothing from it"
        assert read <= set(spec.keys), f"{spec.key}: Silver reads {sorted(read - set(spec.keys))}"


def test_the_silver_reads_of_the_pilots_are_known(catalogue: DatasetCatalogue) -> None:
    # Pinned, so that a change to how the test finds them cannot make it pass
    # by finding nothing.
    lines = catalogue.get("lineas_produccion")
    invoices = catalogue.get("qad_facturas")

    assert _silver_reads(lines) == {
        "Línea",
        "Descripción",
        "Almac",
        "Planificación de Unidades por Hora",
    }
    assert len(_silver_reads(invoices)) == 27


def test_every_silver_table_is_one_the_build_writes(catalogue: DatasetCatalogue) -> None:
    for spec in catalogue:
        assert f"INSERT INTO {spec.silver_table}" in SILVER_SQL, spec.key


def test_what_a_snapshot_says_of_a_removed_row_is_what_the_build_does(
    catalogue: DatasetCatalogue,
) -> None:
    # A table the build empties before filling it drops a row a new version
    # leaves out; one it only upserts keeps it. The confirmation tells people
    # which, so the spec must say what the build does.
    for spec in catalogue:
        if spec.kind is not DatasetKind.SNAPSHOT:
            assert spec.removed_rows is None, spec.key
            continue
        emptied = re.search(rf"(DELETE FROM|TRUNCATE) {re.escape(spec.silver_table)}\b", SILVER_SQL)
        expected = RemovedRows.DROPPED if emptied else RemovedRows.KEPT
        assert spec.removed_rows is expected, spec.key


def test_every_label_is_a_text_of_the_web_app(catalogue: DatasetCatalogue, repo_root: Path) -> None:
    texts = json.loads((repo_root / "frontend/src/i18n/es.json").read_text(encoding="utf-8"))

    def text_of(key: str) -> Any:
        node: Any = texts
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return node

    for spec in catalogue:
        named = [spec.label, spec.description, *(c.label for c in spec.columns if c.label)]
        for key in named:
            assert isinstance(text_of(key), str), f"{spec.key}: {key} is not in es.json"


def test_the_headers_are_the_workbooks_own(catalogue: DatasetCatalogue, repo_root: Path) -> None:
    checked = 0
    for spec in catalogue:
        route = _route(spec)
        assert route is not None
        path = repo_root / WORKBOOKS / route.filename
        if not path.exists():
            continue
        sheet = CalamineWorkbookReader().open_workbook(path.read_bytes()).sheet(spec.bronze.sheet)
        rows = list(sheet.rows)
        header = [cell_text(cell) or "" for cell in rows[spec.bronze.header_row - 1]]
        while header and not header[-1].strip():
            header.pop()

        assert tuple(header) == spec.headers, spec.key
        checked += 1
    if not checked:
        pytest.skip(f"no workbook of the specs in {WORKBOOKS}")
