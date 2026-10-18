"""What a workbook's archive may hold, and how a lying archive is caught.

``energy.domain.workbook_limits`` judges the archive's directory;
``energy.adapters.zip_package`` reads it and unpacks every entry through a
counter. The limits are made small here, so a "zip bomb" is a few kilobytes.
"""

from __future__ import annotations

import io
import struct
import zipfile
from typing import TYPE_CHECKING

import pytest
from tests.fixtures.workbooks import lines_good

from energy.adapters.zip_package import ZipPackageInspector
from energy.domain.draft_rules import RuleId
from energy.domain.exceptions import UnsupportedFileFormatError
from energy.domain.workbook_limits import (
    ANY_FILE,
    IN_REQUEST,
    PackageEntry,
    PackageLimits,
    package_issue,
)

if TYPE_CHECKING:
    from pathlib import Path

SMALL = PackageLimits(max_entries=20, max_unpacked_bytes=200_000, max_shared_strings_bytes=50_000)
WORKBOOK = (PackageEntry("[Content_Types].xml", 500), PackageEntry("xl/workbook.xml", 500))


def archive(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        for name, data in entries.items():
            package.writestr(name, data)
    return path


def test_the_limits_of_section_6_and_of_the_api() -> None:
    assert ANY_FILE.max_unpacked_bytes == 4 * 1024**3
    assert IN_REQUEST.max_unpacked_bytes == 256 * 1024**2
    assert IN_REQUEST.max_shared_strings_bytes == 64 * 1024**2


def test_a_real_workbook_passes() -> None:
    entries = (*WORKBOOK, PackageEntry("xl/sharedStrings.xml", 40_000))

    assert package_issue(entries, SMALL) is None


@pytest.mark.parametrize(
    ("entries", "rule", "params"),
    [
        (
            WORKBOOK + tuple(PackageEntry(f"xl/media/{n}.bin", 1) for n in range(19)),
            RuleId.FILE_TOO_MANY_PARTS,
            {"found": 21, "limit": 20},
        ),
        (
            (*WORKBOOK, PackageEntry("xl/worksheets/sheet1.xml", 199_500)),
            RuleId.FILE_CONTENT_TOO_LARGE,
            {"found_bytes": 200_500, "limit_bytes": 200_000},
        ),
        (
            (*WORKBOOK, PackageEntry("xl/sharedStrings.xml", 50_001)),
            RuleId.FILE_SHARED_STRINGS_TOO_LARGE,
            {"found_bytes": 50_001, "limit_bytes": 50_000},
        ),
        (
            (*WORKBOOK, PackageEntry("xl/vbaProject.bin", 10)),
            RuleId.FILE_UNREADABLE,
            {"reason": "macros"},
        ),
        (
            (PackageEntry("word/document.xml", 10), PackageEntry("[Content_Types].xml", 10)),
            RuleId.FILE_UNREADABLE,
            {"reason": "not_a_spreadsheet"},
        ),
    ],
)
def test_an_archive_over_a_limit_is_an_error(
    entries: tuple[PackageEntry, ...], rule: RuleId, params: dict[str, object]
) -> None:
    issue = package_issue(entries, SMALL)

    assert issue is not None
    assert (issue.rule, dict(issue.params)) == (rule, params)
    assert (issue.source_row, issue.column_index) == (None, None)


def test_the_inspector_reads_the_directory_of_a_real_workbook(tmp_path: Path) -> None:
    path = tmp_path / "lines.xlsx"
    path.write_bytes(lines_good())
    inspector = ZipPackageInspector()

    names = {entry.name for entry in inspector.entries(path)}
    inspector.verify(path)

    assert {"[Content_Types].xml", "xl/workbook.xml"} <= names


def test_a_file_that_is_no_zip_is_named_so(tmp_path: Path) -> None:
    path = tmp_path / "text.xlsx"
    path.write_bytes(b"PK\x03\x04 then nothing a zip has")

    with pytest.raises(UnsupportedFileFormatError) as caught:
        ZipPackageInspector().entries(path)

    assert caught.value.details["reason"] == "not_a_zip"


def test_a_bomb_is_judged_by_its_directory_before_anything_is_unpacked(tmp_path: Path) -> None:
    # 10 MB of zeros pack into about 10 kB.
    path = archive(tmp_path / "bomb.xlsx", {"xl/worksheets/sheet1.xml": bytes(10_000_000)})

    entries = ZipPackageInspector().entries(path)

    assert path.stat().st_size < 50_000
    issue = package_issue(entries, SMALL)
    assert issue is not None
    assert issue.rule is RuleId.FILE_CONTENT_TOO_LARGE


def test_an_entry_larger_than_its_directory_says_is_caught(tmp_path: Path) -> None:
    data = b"<row>" * 20_000
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("xl/worksheets/sheet1.xml", data)
    raw = bytearray(buffer.getvalue())
    # Make the central directory understate the entry: 100 bytes instead of 100,000.
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<I", raw, central + 24, 100)
    path = tmp_path / "liar.xlsx"
    path.write_bytes(bytes(raw))
    inspector = ZipPackageInspector()

    assert [entry.unpacked_bytes for entry in inspector.entries(path)] == [100]
    with pytest.raises(UnsupportedFileFormatError) as caught:
        inspector.verify(path)

    assert caught.value.details["reason"] == "corrupt"


def test_an_altered_entry_fails_its_checksum(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as package:
        package.writestr("xl/workbook.xml", b"<workbook>original</workbook>")
    path = tmp_path / "altered.xlsx"
    path.write_bytes(buffer.getvalue().replace(b"original", b"altered!"))

    with pytest.raises(UnsupportedFileFormatError):
        ZipPackageInspector().verify(path)
