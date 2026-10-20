"""The data sources the chat assistant knows of, and whether each has a table yet.

Layer: domain

Responsibility:
    Lists every file of the plant the assistant may be asked about, as
    ``config/chat/sources.yaml`` describes them. A source is *implemented* when
    its data is in the database, read through the views of schema
    ``reporting``; it is *pending* while nothing loads it into a table. The
    assistant answers a question about a pending source with the sentence its
    instructions give for data that has no table yet.

Depends on:
    ``energy.domain.exceptions``, standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from energy.domain.exceptions import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Iterable


class SourceState(StrEnum):
    """Whether a source's data is in the database."""

    IMPLEMENTED = "implemented"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class ReportingView:
    """One view of schema ``reporting`` that holds a source's data.

    Attributes:
        name: the view's name, without the schema.
        description: what one row of it is.
    """

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class DataSource:
    """One file of the plant, and where its data is.

    Attributes:
        key: a short, stable name.
        files: the file names the plant knows it by.
        description: what it holds.
        state: implemented or pending.
        views: the views that hold it; none while pending.
    """

    key: str
    files: tuple[str, ...]
    description: str
    state: SourceState
    views: tuple[ReportingView, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceCatalogue:
    """Every source the assistant knows of, in the order of the file."""

    sources: tuple[DataSource, ...]

    def __post_init__(self) -> None:
        """Check that the catalogue holds together.

        Raises:
            ConfigurationError: listing every problem found: a repeated key, an
                implemented source without views, or a pending one with views.
        """
        problems: list[str] = []
        seen: set[str] = set()
        for source in self.sources:
            if source.key in seen:
                problems.append(f"{source.key}: the key is repeated")
            seen.add(source.key)
            if source.state is SourceState.IMPLEMENTED and not source.views:
                problems.append(f"{source.key}: an implemented source names its views")
            if source.state is SourceState.PENDING and source.views:
                problems.append(f"{source.key}: a pending source has no views yet")
        if problems:
            msg = "the assistant's sources catalogue is not valid"
            raise ConfigurationError(msg, problems=problems)

    @property
    def view_names(self) -> frozenset[str]:
        """Return the names of every view an implemented source names."""
        return frozenset(view.name for source in self.sources for view in source.views)

    def by_state(self, state: SourceState) -> Iterable[DataSource]:
        """Return the sources in one state, in the order of the file."""
        return (source for source in self.sources if source.state is state)


__all__ = ["DataSource", "ReportingView", "SourceCatalogue", "SourceState"]
