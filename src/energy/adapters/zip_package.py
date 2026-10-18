"""Workbook archive inspector backed by the standard library's ``zipfile``.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.workbook_package.WorkbookPackageInspector`.
    It reads the directory of an ``.xlsx`` archive, which states each entry's
    unpacked size, and it unpacks every entry through a counter, 64 KiB at a
    time, keeping nothing. ``zipfile`` stops an entry at the size its
    directory states and checks its CRC at the end, so an archive that hides a
    larger entry behind a small declared size, or whose bytes were altered,
    fails here rather than inside the workbook reader. Nothing is extracted and
    no entry name is used as a path.

Depends on:
    ``energy.ports.workbook_package``, ``energy.domain``, standard library
    only.
"""

from __future__ import annotations

import zipfile
import zlib
from typing import TYPE_CHECKING, Final

from energy.domain.exceptions import UnsupportedFileFormatError
from energy.domain.workbook_limits import PackageEntry

if TYPE_CHECKING:
    from pathlib import Path

_CHUNK_BYTES: Final = 64 * 1024


class ZipPackageInspector:
    """Reads and verifies the archive of an ``.xlsx`` file."""

    def entries(self, path: Path) -> tuple[PackageEntry, ...]:
        """Return the archive's directory, as it states it.

        Args:
            path: the workbook file.

        Returns:
            One entry per member, with the size it claims to unpack to.

        Raises:
            UnsupportedFileFormatError: ``reason`` ``not_a_zip`` or ``corrupt``.
        """
        if not zipfile.is_zipfile(path):
            raise UnsupportedFileFormatError("the file is not a zip archive", reason="not_a_zip")
        try:
            with zipfile.ZipFile(path) as archive:
                return tuple(
                    PackageEntry(member.filename, member.file_size) for member in archive.infolist()
                )
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
            raise UnsupportedFileFormatError(
                "the archive's directory cannot be read", reason="corrupt", detail=str(exc)
            ) from exc

    def verify(self, path: Path) -> None:
        """Unpack every entry through a counter, keeping nothing.

        Args:
            path: the workbook file.

        Raises:
            UnsupportedFileFormatError: ``reason`` ``corrupt`` if an entry
                unpacks to another size than its directory states, fails its
                checksum, is encrypted, or cannot be unpacked.
        """
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    unpacked = 0
                    with archive.open(member) as entry:
                        while chunk := entry.read(_CHUNK_BYTES):
                            unpacked += len(chunk)
                    if unpacked != member.file_size:
                        raise UnsupportedFileFormatError(
                            "an entry is not the size the archive states",
                            reason="corrupt",
                            entry=member.filename,
                        )
        except (
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            NotImplementedError,
            RuntimeError,
            OSError,
            EOFError,
            ValueError,
            zlib.error,
        ) as exc:
            raise UnsupportedFileFormatError(
                "the archive cannot be unpacked", reason="corrupt", detail=str(exc)
            ) from exc


__all__ = ["ZipPackageInspector"]
