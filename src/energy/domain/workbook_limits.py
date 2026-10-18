"""What an ``.xlsx`` package may hold before anything unpacks it.

Layer: domain

Responsibility:
    An ``.xlsx`` file is a zip archive of XML parts. A small file can unpack to
    gigabytes (a "zip bomb"), and the reader holds a workbook's shared strings
    and the sheet it reads in memory. These limits are checked against the
    archive's own directory, before any part is unpacked:

    * how many entries the archive has;
    * how much its entries unpack to, together;
    * how much ``xl/sharedStrings.xml`` unpacks to.

    The archive must also be a spreadsheet (it has ``xl/workbook.xml``) and
    hold no macros (``xl/vbaProject.bin``). Every problem becomes a ``file.*``
    issue and stops the check of that file.

    The directory states sizes the file itself could lie about. The adapter
    that reads it (``energy.adapters.zip_package``) also unpacks every entry
    through a counter, so a directory that understates an entry is caught
    before the reader opens the file.

    The module is pure. It reads no file.

Depends on:
    ``energy.domain.draft_rules``, standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from energy.domain.draft_rules import Issue, IssueSeverity, RuleId
from energy.domain.drafts import MIB

if TYPE_CHECKING:
    from collections.abc import Sequence

GIB: Final = 1024 * MIB

CONTENT_TYPES_PART: Final = "[Content_Types].xml"
WORKBOOK_PART: Final = "xl/workbook.xml"
SHARED_STRINGS_PART: Final = "xl/sharedStrings.xml"
MACROS_PART: Final = "xl/vbaProject.bin"


@dataclass(frozen=True, slots=True)
class PackageLimits:
    """The most a workbook's archive may hold.

    Attributes:
        max_entries: entries in the archive.
        max_unpacked_bytes: what every entry unpacks to, together.
        max_shared_strings_bytes: what ``xl/sharedStrings.xml`` unpacks to.
    """

    max_entries: int
    max_unpacked_bytes: int
    max_shared_strings_bytes: int


#: Any file: what docs/frontend-plan.md §6 allows a 300 MB workbook.
ANY_FILE: Final = PackageLimits(
    max_entries=10_000, max_unpacked_bytes=4 * GIB, max_shared_strings_bytes=1 * GIB
)
#: A file the API validates inside the request, at most 5 MiB. A real export
#: of that size unpacks to about 40 MiB.
IN_REQUEST: Final = PackageLimits(
    max_entries=10_000, max_unpacked_bytes=256 * MIB, max_shared_strings_bytes=64 * MIB
)


@dataclass(frozen=True, slots=True)
class PackageEntry:
    """One entry of the archive's directory.

    Attributes:
        name: the entry's path inside the archive.
        unpacked_bytes: the size the directory says it unpacks to.
    """

    name: str
    unpacked_bytes: int


def package_issue(entries: Sequence[PackageEntry], limits: PackageLimits) -> Issue | None:
    """Return the first problem of a workbook's archive, or ``None``.

    Args:
        entries: the archive's directory.
        limits: what the archive may hold.

    Returns:
        A ``file.*`` error issue, or ``None`` when the archive is acceptable.
    """
    names = {entry.name for entry in entries}
    if len(entries) > limits.max_entries:
        return _issue(RuleId.FILE_TOO_MANY_PARTS, found=len(entries), limit=limits.max_entries)
    unpacked = sum(entry.unpacked_bytes for entry in entries)
    if unpacked > limits.max_unpacked_bytes:
        return _issue(
            RuleId.FILE_CONTENT_TOO_LARGE,
            found_bytes=unpacked,
            limit_bytes=limits.max_unpacked_bytes,
        )
    shared = sum(entry.unpacked_bytes for entry in entries if entry.name == SHARED_STRINGS_PART)
    if shared > limits.max_shared_strings_bytes:
        return _issue(
            RuleId.FILE_SHARED_STRINGS_TOO_LARGE,
            found_bytes=shared,
            limit_bytes=limits.max_shared_strings_bytes,
        )
    if MACROS_PART in names:
        return unreadable("macros")
    if CONTENT_TYPES_PART not in names or WORKBOOK_PART not in names:
        return unreadable("not_a_spreadsheet")
    return None


def unreadable(reason: str) -> Issue:
    """Return the issue of a file that cannot be read as an ``.xlsx``.

    Args:
        reason: ``not_a_zip``, ``corrupt``, ``not_a_spreadsheet`` or ``macros``.
    """
    return _issue(RuleId.FILE_UNREADABLE, reason=reason)


def _issue(rule: RuleId, **params: str | int) -> Issue:
    """Return a file-level error issue."""
    return Issue(rule, IssueSeverity.ERROR, None, None, None, None, params)


__all__ = [
    "ANY_FILE",
    "CONTENT_TYPES_PART",
    "GIB",
    "IN_REQUEST",
    "MACROS_PART",
    "SHARED_STRINGS_PART",
    "WORKBOOK_PART",
    "PackageEntry",
    "PackageLimits",
    "package_issue",
    "unreadable",
]
