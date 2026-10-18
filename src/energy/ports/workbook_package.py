"""Port for looking inside a workbook's archive before it is read.

Layer: ports

Responsibility:
    An ``.xlsx`` is a zip archive. Before a workbook reader unpacks it, the
    application reads the archive's directory, to apply the limits of
    :mod:`energy.domain.workbook_limits`, and then has every entry unpacked
    through a counter, so a directory that lies about its sizes is caught
    before the reader trusts it. Nothing is extracted to disk and no entry name
    becomes a path.

Depends on:
    ``energy.domain``, standard library typing only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from energy.domain.workbook_limits import PackageEntry


@runtime_checkable
class WorkbookPackageInspector(Protocol):
    """Interface for reading and verifying a workbook's archive."""

    def entries(self, path: Path) -> tuple[PackageEntry, ...]:
        """Return the archive's directory, as it states it.

        Args:
            path: the workbook file.

        Returns:
            One entry per member of the archive, with the size it claims to
            unpack to.

        Raises:
            UnsupportedFileFormatError: with ``reason`` ``not_a_zip`` if the
                file is not a zip archive, or ``corrupt`` if its directory
                cannot be read.
        """
        ...

    def verify(self, path: Path) -> None:
        """Unpack every entry through a counter, keeping nothing.

        Call it only once :meth:`entries` passed the limits: it unpacks
        everything the directory declares.

        Args:
            path: the workbook file.

        Raises:
            UnsupportedFileFormatError: with ``reason`` ``corrupt`` if an
                entry unpacks to another size than the directory says, fails
                its checksum, or cannot be unpacked.
        """
        ...


__all__ = ["WorkbookPackageInspector"]
