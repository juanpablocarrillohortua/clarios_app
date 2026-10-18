"""How a correction compares with the file it corrects (``energy.domain.draft_comparison``).

The rules on counts alone; the matching of real files is in
``test_draft_service.py`` and, against PostgreSQL, in
``tests/integration/test_drafts_end_to_end.py``.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from energy.adapters.yaml_dataset_specs import load_dataset_specs
from energy.domain.dataset_spec import spec_digest
from energy.domain.draft_comparison import (
    ComparisonState,
    CountOnly,
    PreviousFile,
    compare,
    compared_by_row,
)
from energy.domain.draft_rules import IssueSeverity, RuleCount, RuleId

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

PREVIOUS = PreviousFile(uuid4(), "antes.xlsx", datetime(2026, 10, 18, tzinfo=UTC))


def count(rule: RuleId, column: str | None, rows: int, listed: int | None = None) -> RuleCount:
    """Return a count of one rule in one column."""
    return RuleCount(rule, IssueSeverity.ERROR, column, rows, rows if listed is None else listed)


class TestComparedByRow:
    def test_when_one_side_has_none_of_them(self) -> None:
        many = count(RuleId.CELL_PATTERN, "Almac", 45_000, 1_000)

        assert compared_by_row(many, None)
        assert compared_by_row(None, many)

    def test_when_both_sides_list_every_one(self) -> None:
        assert compared_by_row(
            count(RuleId.CELL_PATTERN, "Almac", 3), count(RuleId.CELL_PATTERN, "Almac", 7)
        )

    def test_not_when_either_side_is_capped(self) -> None:
        capped = count(RuleId.CELL_PATTERN, "Almac", 45_000, 1_000)
        whole = count(RuleId.CELL_PATTERN, "Almac", 7)

        assert not compared_by_row(capped, whole)
        assert not compared_by_row(whole, capped)


class TestCompare:
    def test_adds_up_cleared_remaining_and_new_per_rule_and_column(self) -> None:
        before = [count(RuleId.CELL_PATTERN, "Almac", 5), count(RuleId.CELL_REQUIRED, "Línea", 2)]
        after = [count(RuleId.CELL_PATTERN, "Almac", 3), count(RuleId.CELL_RANGE, "Plan", 1)]
        matched = {("cell.pattern", "Almac"): 2}

        result = compare(before, after, matched, PREVIOUS)

        assert result.state is ComparisonState.READY
        # Almac: 2 the same, 3 cleared, 1 new; Línea: 2 cleared; Plan: 1 new.
        assert (result.cleared, result.remaining, result.new) == (5, 2, 2)
        assert result.previous == PREVIOUS

    def test_keeps_capped_groups_out_of_the_totals(self) -> None:
        before = [count(RuleId.CELL_PATTERN, "Almac", 45_000, 1_000)]
        after = [count(RuleId.CELL_PATTERN, "Almac", 12_000, 1_000)]

        result = compare(before, after, {("cell.pattern", "Almac"): 900}, PREVIOUS)

        assert (result.cleared, result.remaining, result.new) == (0, 0, 0)
        assert result.by_count_only == (CountOnly(RuleId.CELL_PATTERN, "Almac", 45_000, 12_000),)

    def test_counts_every_issue_of_a_group_gone_entirely(self) -> None:
        before = [count(RuleId.CELL_PATTERN, "Almac", 45_000, 1_000)]

        result = compare(before, [], {}, PREVIOUS)

        assert (result.cleared, result.remaining, result.new) == (45_000, 0, 0)

    def test_matches_the_files_own_problems_by_rule(self) -> None:
        before = [count(RuleId.FILE_SHEET_MISSING, None, 1)]
        after = [count(RuleId.FILE_SHEET_MISSING, None, 1)]

        result = compare(before, after, {("file.sheet_missing", ""): 1}, PREVIOUS)

        assert (result.cleared, result.remaining, result.new) == (0, 1, 0)


class TestSpecDigest:
    def test_is_the_same_for_the_same_spec(self, repo_root: Path) -> None:
        first = load_dataset_specs(repo_root / "config")
        second = load_dataset_specs(repo_root / "config")

        for spec in first:
            assert spec_digest(spec) == spec_digest(second.get(spec.key))

    def test_changes_with_any_rule(self, repo_root: Path) -> None:
        spec = load_dataset_specs(repo_root / "config").get("lineas_produccion")
        looser = dataclasses.replace(
            spec,
            columns=(dataclasses.replace(spec.columns[0], pattern=None), *spec.columns[1:]),
        )

        assert spec_digest(looser) != spec_digest(spec)
        assert len(spec_digest(spec)) == 64
