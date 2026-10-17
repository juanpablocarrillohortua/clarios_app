"""Write the empty template of a dataset.

Layer: ports

Responsibility:
    The contract the API uses to hand a user the file they fill in for a
    dataset. A template holds the dataset's sheet and its header row, exactly
    the headers of the spec, in order, and no data: read back by the Bronze
    rules, its headers give the spec's payload keys. What else it carries, such
    as cell formats that help people type, is the writer's choice, as long as it
    adds no cell.

Depends on:
    ``energy.domain`` only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from energy.domain.dataset_spec import DatasetSpec


class DatasetTemplateWriter(Protocol):
    """Writes the template of a dataset as a workbook file."""

    #: The file's media type, for the HTTP answer.
    media_type: str
    #: The file name's extension, with its dot.
    suffix: str

    def write(self, spec: DatasetSpec) -> bytes:
        """Return the template of one dataset.

        Args:
            spec: the dataset's spec.

        Returns:
            The file's bytes.
        """
        ...


__all__ = ["DatasetTemplateWriter"]
