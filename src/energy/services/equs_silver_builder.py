"""Application service building the Silver layer of the equivalent-batteries data.

Layer: services

Responsibility:
    Owns the use case "bring Silver up to date with Bronze" for the
    equivalent-batteries (EQUS) data. The rules of the build - which Bronze rows
    count, how a link is recognised as a repeat, how an item is described - are
    set operations and live with the store that runs them, behind
    :class:`~energy.ports.equs_silver.SilverBuildRepository`. What belongs here
    is the use case around them: one build is one unit of work that either
    happens or does not, and its report is returned to whoever asked, so that a
    command line and a scheduled flow see exactly the same thing.

Depends on:
    ``energy.ports.equs_silver``, ``energy.domain``, ``energy.core``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from energy.core.logging import get_logger

if TYPE_CHECKING:
    from energy.domain.equs_silver import SilverBuildReport
    from energy.ports.equs_silver import SilverBuildRepository

logger = get_logger(__name__)


class EqusSilverBuilder:
    """Builds the EQUS Silver layer from Bronze.

    Args:
        repository: the store that holds Bronze and Silver.
    """

    def __init__(self, repository: SilverBuildRepository) -> None:
        self._repository = repository

    def build(self) -> SilverBuildReport:
        """Rebuild Silver from the Bronze rows of completed runs.

        Running it again without new Bronze rows changes nothing.

        Returns:
            What the build did to the data and what needs attention.

        Raises:
            PipelineError: if Bronze holds no completed run. Nothing is changed.
            RepositoryError: if a step fails. Nothing is changed.
        """
        report = self._repository.build()
        attention = (
            len(report.items_without_description)
            + len(report.poly_factor_mismatches)
            + len(report.formed_without_paste)
            + len(report.formed_with_several_assembled)
            + len(report.decorated_without_pieces)
            + len(report.na_with_poly_category)
            + sum(1 for line in report.lines_without_plant if line.receipts)
        )
        # Debug, not info: the command prints the report itself.
        logger.debug(
            "equs silver built",
            seconds=round(report.seconds, 1),
            receipts=report.rows.get("reporte_qad", 0),
            invoice_lines=report.rows.get("unit_ship", 0),
            links=report.rows.get("bom_estructura", 0),
            findings=attention,
        )
        return report


__all__ = ["EqusSilverBuilder"]
