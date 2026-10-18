"""What a corrected file cleared, kept and added, against the file it corrects.

Layer: domain

Responsibility:
    A person fixes a file in Excel and uploads it again as a correction of
    the draft it came from (increment 6). This module says how the two
    drafts' issues compare.

    Two issues are the same issue when they break the same rule in the same
    column of the same row. A row is known by its identity
    (:func:`energy.domain.draft_rules.row_identity`): its natural key,
    loosened, so a row that moved keeps its identity. A file's or a header's
    problem has no row: it is matched by rule and column alone.

    The lists of issues are capped (the first 1,000 of a rule in a column);
    the counts are not. A rule and column is compared row by row only when
    both drafts list every one of its issues, or when one draft has none of
    them, in which case every issue is cleared, or every one is new. Any
    other rule and column is compared by count alone, and never folded into
    the three totals, so the totals are always exact.

    The module is pure.

Depends on:
    ``energy.domain.draft_rules``, ``energy.domain.drafts``, standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime
    from uuid import UUID

    from energy.domain.draft_rules import RuleCount, RuleId

type GroupKey = tuple[str, str]
"""A rule and a column, as ``(rule ID, column key or "")``."""


class IssueChange(StrEnum):
    """What became of an issue of a corrected file."""

    NEW = "new"
    REMAINING = "remaining"


class ComparisonState(StrEnum):
    """Whether two drafts could be compared, and if not, why."""

    READY = "ready"
    NOT_VALIDATED = "not_validated"
    PREVIOUS_NOT_VALIDATED = "previous_not_validated"
    PREVIOUS_GONE = "previous_gone"
    RULES_CHANGED = "rules_changed"


@dataclass(frozen=True, slots=True)
class PreviousFile:
    """The file a correction is compared with.

    Attributes:
        draft_id: its draft.
        filename: its name, as data.
        validated_at: when it was last validated; ``None`` if never.
    """

    draft_id: UUID
    filename: str
    validated_at: datetime | None


@dataclass(frozen=True, slots=True)
class CountOnly:
    """A rule and column too large on both sides to compare row by row.

    Attributes:
        rule: the rule.
        column_key: the column; ``None`` for the file as a whole.
        before: its issues in the earlier file.
        after: its issues in the correction.
    """

    rule: RuleId
    column_key: str | None
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class Comparison:
    """How a correction compares with the file it corrects.

    Attributes:
        state: whether the comparison could be made.
        previous: the earlier file, while it is still kept.
        previous_revalidated: the earlier file was validated again after the
            correction, as happens when the spec changed in between, so both
            are judged by the same rules.
        cleared: issues of the earlier file the correction no longer has.
        remaining: issues of the earlier file the correction still has.
        new: issues of the correction the earlier file did not have.
        by_count_only: rules and columns compared by count alone.
    """

    state: ComparisonState
    previous: PreviousFile | None
    previous_revalidated: bool = False
    cleared: int = 0
    remaining: int = 0
    new: int = 0
    by_count_only: tuple[CountOnly, ...] = ()


def group_key(rule: str, column_key: str | None) -> GroupKey:
    """Return the key of a rule and column, as both drafts' counts are joined."""
    return (rule, column_key or "")


def compared_by_row(before: RuleCount | None, after: RuleCount | None) -> bool:
    """Return whether a rule and column can be compared issue by issue.

    True when one side has none of its issues, or both list all of them.
    """
    if before is None or after is None:
        return True
    return before.rows == before.listed and after.rows == after.listed


def compare(
    before: Iterable[RuleCount],
    after: Iterable[RuleCount],
    matched: Mapping[GroupKey, int],
    previous: PreviousFile,
    *,
    previous_revalidated: bool = False,
) -> Comparison:
    """Return how a correction compares with the file it corrects.

    Args:
        before: the earlier draft's counts.
        after: the correction's counts.
        matched: for each rule and column compared row by row, how many of
            the correction's issues have the same issue in the earlier file.
        previous: the earlier file.
        previous_revalidated: whether the earlier file was validated again
            after the correction.
    """
    earlier = {group_key(count.rule, count.column_key): count for count in before}
    later = {group_key(count.rule, count.column_key): count for count in after}
    cleared = remaining = new = 0
    by_count: list[CountOnly] = []
    for key in sorted(earlier.keys() | later.keys()):
        old, now = earlier.get(key), later.get(key)
        if old is not None and now is not None and not compared_by_row(old, now):
            by_count.append(CountOnly(now.rule, now.column_key, old.rows, now.rows))
            continue
        same = matched.get(key, 0) if old is not None and now is not None else 0
        remaining += same
        cleared += (old.rows if old else 0) - same
        new += (now.rows if now else 0) - same
    return Comparison(
        state=ComparisonState.READY,
        previous=previous,
        previous_revalidated=previous_revalidated,
        cleared=cleared,
        remaining=remaining,
        new=new,
        by_count_only=tuple(by_count),
    )


__all__ = [
    "Comparison",
    "ComparisonState",
    "CountOnly",
    "GroupKey",
    "IssueChange",
    "PreviousFile",
    "compare",
    "compared_by_row",
    "group_key",
]
