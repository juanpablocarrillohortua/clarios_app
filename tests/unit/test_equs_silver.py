"""Tests for the Silver build of the equivalent-batteries data, without a database.

Layer: tests (unit)

The build itself is SQL and is tested against PostgreSQL in the integration
suite. What can be checked here is everything around it: how the report reads,
that the use case hands back exactly what the store reported, that the wiring
connects to nothing until asked, and that the SQL module is the fixed text it
claims to be.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from energy.adapters import equs_silver_sql as sql
from energy.adapters.postgres_equs_silver import PostgresSilverBuildRepository
from energy.domain.equs_silver import (
    CategoryConflict,
    ItemQuantity,
    LineWithoutPlant,
    PolyMismatch,
    SeveralAssembled,
    SilverBuildReport,
    StructureSheet,
    describe,
)
from energy.domain.exceptions import PipelineError
from energy.pipelines.flows.equs_silver_build import build_builder, build_silver
from energy.ports.equs_silver import SilverBuildRepository
from energy.services.equs_silver_builder import EqusSilverBuilder

pytestmark = pytest.mark.unit


def _report(**overrides: object) -> SilverBuildReport:
    """Return a report shaped like the one the real workbooks give."""
    values: dict[str, object] = {
        "rows": {"item_master": 13_317, "reporte_qad": 138_773, "td_form": 1_248},
        "structure": (
            StructureSheet("BAT FOR", 4_566, 1, 0, 11, 0, 453, 4_554),
            StructureSheet("Datos", 31_907, 0, 741, 70, 0, 0, 31_096),
        ),
        "receipts_left_out": 0,
        "invoice_lines_left_out": 2,
        "placeholders": {"linea_de_productos": 3, "item_master": 0},
        "items_without_description": (),
        "lines_without_plant": (LineWithoutPlant("FADECO05", 1_284), LineWithoutPlant("X", 0)),
        "poly_factor_mismatches": (
            PolyMismatch("157000012", "PELLET", Decimal("0"), None),
            PolyMismatch("142000020", "MANIJA", Decimal("0.5"), Decimal("0.11")),
        ),
        "poly_repeated_items": ("111000005",),
        "formed_without_paste": (ItemQuantity("231000002", Decimal("1234.5"), 3),),
        "decorated_without_pieces": (),
        "na_with_poly_category": tuple(
            CategoryConflict(f"12100{n:04d}", "CUB 1P", n) for n in range(1, 15)
        ),
        "formed_with_several_assembled": (SeveralAssembled("231001483", 2, Decimal("1008")),),
        "notes": ("Item_Master is not landed.",),
        "seconds": 25.6,
    }
    values.update(overrides)
    return SilverBuildReport(**values)  # type: ignore[arg-type]


class FakeRepository:
    """Answers a build with a prepared report, or fails the way a store would."""

    def __init__(self, report: SilverBuildReport | None = None, error: Exception | None = None):
        self.builds = 0
        self._report = report or _report()
        self._error = error

    def build(self) -> SilverBuildReport:
        self.builds += 1
        if self._error is not None:
            raise self._error
        return self._report


def test_duplicates_dropped_is_per_sheet() -> None:
    """The figure the specification asks for, read off the structure rows."""
    assert _report().duplicates_dropped == {"BAT FOR": 11, "Datos": 70}


def test_describe_lists_rows_then_structure_then_checks() -> None:
    """A person reads the tables first, then what was done, then what needs them."""
    lines = describe(_report())
    text = "\n".join(lines)

    assert lines[0] == "Rows per Silver table"
    assert "  reporte_qad              138,773" in lines
    assert lines.index("Product structure, per sheet") < lines.index("Checks")
    assert "  Datos       31,907        0         741        70            0  31,096" in lines
    assert "  Rows read by a pasted header: BAT FOR (453)" in lines
    assert "Movements left out: 0 production receipts, 2 invoice lines" in lines
    assert "Catalogue rows nobody describes: linea_de_productos 3" in lines
    assert "Items without description: none" in lines
    assert "  FADECO05 (1,284 receipts), X (0 receipts)" in lines
    assert "157000012 PELLET: 0 against no entry" in text
    assert "142000020 MANIJA: 0.5 against 0.11" in text
    assert "  231000002 (1,234)" in lines or "  231000002 (1,235)" in lines
    assert "  231001483 (2 assembled, 1,008 produced)" in lines
    assert lines[-2:] == ["Notes", "  Item_Master is not landed."]


def test_describe_shows_a_few_examples_and_counts_the_rest() -> None:
    """Fourteen items are a number and its first examples, not fourteen lines."""
    lines = describe(_report(), examples=3)

    assert 'Items QAD marks "N/A" that Clasif_Poly classifies: 14' in lines
    examples = lines[lines.index('Items QAD marks "N/A" that Clasif_Poly classifies: 14') + 1]
    assert examples.count("CUB 1P") == 3
    assert examples.endswith("and 11 more")


def test_describe_of_a_clean_build_has_no_notes_and_no_examples() -> None:
    """Nothing to report reads as "none", and the notes heading disappears."""
    clean = _report(
        lines_without_plant=(),
        poly_factor_mismatches=(),
        poly_repeated_items=(),
        formed_without_paste=(),
        na_with_poly_category=(),
        formed_with_several_assembled=(),
        placeholders={"linea_de_productos": 0},
        notes=(),
    )
    lines = describe(clean)

    assert "Notes" not in lines
    assert "Catalogue rows nobody describes: none" in lines
    assert sum(line.endswith(": none") for line in lines) == 9
    assert not any("pasted header" in line for line in describe(_report(structure=())))


def test_the_builder_returns_what_the_store_reported() -> None:
    """The use case adds nothing and hides nothing."""
    repository = FakeRepository()
    assert isinstance(repository, SilverBuildRepository)

    report = EqusSilverBuilder(repository).build()

    assert report is repository.build()
    assert repository.builds == 2


def test_the_builder_lets_a_refusal_through() -> None:
    """An empty Bronze is the caller's to hear about, not to be swallowed."""
    repository = FakeRepository(error=PipelineError("Bronze holds no completed load"))

    with pytest.raises(PipelineError, match="no completed load"):
        EqusSilverBuilder(repository).build()


def test_wiring_connects_to_nothing_until_a_build_runs() -> None:
    """The builder is assembled lazily, like the loader."""
    engine = create_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere")

    builder = build_builder(engine)

    assert isinstance(builder, EqusSilverBuilder)
    assert isinstance(PostgresSilverBuildRepository(engine), SilverBuildRepository)


def test_build_silver_reports_a_database_it_cannot_reach() -> None:
    """A dead database is a clear error of the platform's own kind."""
    from energy.domain.exceptions import RepositoryError  # noqa: PLC0415

    engine = create_engine(
        "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere",
        connect_args={"connect_timeout": 1},
    )

    with pytest.raises(RepositoryError, match="could not build Silver"):
        build_silver(engine)


# -----------------------------------------------------------------------------
# The SQL module
# -----------------------------------------------------------------------------
def _statements() -> dict[str, str]:
    """Return every statement of the module, by name."""
    found = {step.name: step.sql for step in sql.BUILD_STEPS}
    found.update({name: value for name, value in vars(sql).items() if name.startswith("REPORT_")})
    return found


def test_the_steps_run_in_dependency_order() -> None:
    """Staging before catalogues, catalogues before what points at them."""
    names = [step.name for step in sql.BUILD_STEPS]

    assert len(names) == len(set(names)) == 13
    assert names[0] == "parsing functions"
    assert names.index("item master") < names.index(
        "product structure, decoration catalogue and plastic factors"
    )
    assert names.index("item master") < names.index("production receipts and invoice lines")
    assert names[-1] == "standard cost history"


def test_the_statements_are_fixed_text() -> None:
    """Nothing is bound or formatted into them: they are sent exactly as written."""
    for name, statement in _statements().items():
        assert "{" not in statement.replace("{4}", "").replace("{2}", ""), name
        assert "%s" not in statement, name
        assert not re.search(r"(?<![:\w]):[a-z_]+\b", statement), name


def test_bronze_is_only_read() -> None:
    """The build writes Silver and its own temporary tables, nothing else."""
    writes = re.compile(
        r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE)\s+([a-z_]+\.)?([a-z_]+)",
        re.IGNORECASE,
    )
    for step in sql.BUILD_STEPS:
        for match in writes.finditer(step.sql):
            schema, table = match.group(2), match.group(3)
            if match.group(1).upper() == "UPDATE" and table.upper() == "SET":
                continue  # the DO UPDATE SET of an upsert
            assert schema == "silver." or table.startswith("eq_"), (step.name, match.group(0))
    assert "bronze." in sql.MOVEMENTS.sql
    assert not any("gold." in statement for statement in _statements().values())


def test_the_placeholder_is_spelled_the_same_everywhere() -> None:
    """The statements write the placeholder out; this keeps them and the constant together."""
    quoted = f"'{sql.SIN_DESCRIPCION}'"
    spelled = sum(statement.count(quoted) for statement in _statements().values())

    assert spelled == 6
    assert quoted in sql.ITEM_MASTER.sql
    assert quoted in sql.REPORT_PLACEHOLDERS


def test_every_silver_table_is_counted_in_the_report() -> None:
    """Eighteen tables and the two conversion views, in a fixed order."""
    assert len(sql.SILVER_TABLES) == len(set(sql.SILVER_TABLES)) == 20
    assert sql.SILVER_TABLES[-2:] == ("td_form", "td_deco")
    assert all(
        statement.startswith("REFRESH MATERIALIZED VIEW")
        for statement in (*sql.REFRESH_VIEWS, *sql.REFRESH_VIEWS_CONCURRENTLY)
    )
