"""Port for building the Silver layer of the equivalent-batteries data.

Layer: ports

Responsibility:
    Declares the one operation the application needs from wherever the
    equivalent-batteries (EQUS) data lives: turn what Bronze holds into Silver.
    The contract carries two guarantees. A build is all-or-nothing: either every
    Silver table reflects Bronze as it was when the build started, or none
    changed. And it is idempotent: building twice from the same Bronze gives the
    same Silver, row for row. This protocol mentions no SQL and no table.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from energy.domain.equs_silver import SilverBuildReport


@runtime_checkable
class SilverBuildRepository(Protocol):
    """Interface for the store that holds Bronze and Silver."""

    def build(self) -> SilverBuildReport:
        """Rebuild Silver from the Bronze rows of completed runs.

        Snapshot sources (product structures, catalogues, the item master)
        replace what Silver held with their latest completed run. Movements
        (production receipts, invoice lines) accumulate: every completed run
        counts, the newest copy of each wins, and none is ever removed.

        Returns:
            What the build did to the data and what needs attention.

        Raises:
            PipelineError: if Bronze holds no completed run to build from.
                Nothing is changed.
            RepositoryError: if the store rejects a step. Nothing is changed.
        """
        ...


__all__ = ["SilverBuildRepository"]
