"""What an upload must announce and what its bytes must be (``energy.domain.drafts``)."""

from __future__ import annotations

import pytest

from energy.domain.drafts import (
    MAX_FILE_BYTES,
    MIB,
    XLSX_MEDIA_TYPE,
    UploadGuard,
    check_declared_size,
    clean_filename,
    require_xlsx,
)
from energy.domain.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    IncompleteUploadError,
    InvalidFilenameError,
    LengthRequiredError,
    UnsupportedFileFormatError,
)

ZIP = b"PK\x03\x04"


def test_the_limit_is_what_windows_calls_300_mb() -> None:
    assert MAX_FILE_BYTES == 300 * 1024 * 1024 == 314_572_800
    assert MIB == 1_048_576


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("Facturas octubre.xlsx", "Facturas octubre.xlsx"),
        (r"C:\Users\someone\Desktop\Líneas.xlsx", "Líneas.xlsx"),
        ("../../etc/passwd.xlsx", "passwd.xlsx"),
        ("  espacios.xlsx  ", "espacios.xlsx"),
        ("con\x00control\u202e.xlsx", "concontrol.xlsx"),
        # A decomposed accent (e + combining acute) becomes the composed one.
        ("Le\u0301ase.xlsx", "Léase.xlsx"),
    ],
)
def test_a_file_name_keeps_only_its_last_segment_and_printable_text(raw: str, kept: str) -> None:
    assert clean_filename(raw) == kept


def test_a_long_file_name_is_cut_keeping_its_extension() -> None:
    cleaned = clean_filename("a" * 400 + ".xlsx")

    assert len(cleaned) == 255
    assert cleaned.endswith(".xlsx")


@pytest.mark.parametrize("raw", ["", "   ", "folder/", "..", "\x07"])
def test_a_name_with_nothing_usable_is_refused(raw: str) -> None:
    with pytest.raises(InvalidFilenameError):
        clean_filename(raw)


@pytest.mark.parametrize("media_type", [XLSX_MEDIA_TYPE, "application/octet-stream", ""])
def test_an_xlsx_may_be_declared_as_browsers_declare_it(media_type: str) -> None:
    require_xlsx("libro.XLSX", media_type)


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("libro.xls", XLSX_MEDIA_TYPE),
        ("libro.xlsm", XLSX_MEDIA_TYPE),
        ("libro.csv", "text/csv"),
        ("libro", XLSX_MEDIA_TYPE),
        ("libro.xlsx", "text/plain"),
    ],
)
def test_anything_but_an_xlsx_is_refused_by_name_or_type(filename: str, media_type: str) -> None:
    with pytest.raises(UnsupportedFileFormatError):
        require_xlsx(filename, media_type)


def test_the_declared_size_is_checked_before_a_byte_is_read() -> None:
    assert check_declared_size(MAX_FILE_BYTES) == MAX_FILE_BYTES
    with pytest.raises(LengthRequiredError):
        check_declared_size(None)
    with pytest.raises(EmptyFileError):
        check_declared_size(0)
    with pytest.raises(FileTooLargeError):
        check_declared_size(MAX_FILE_BYTES + 1)


def test_the_guard_lets_a_zip_of_the_announced_size_through() -> None:
    guard = UploadGuard(declared_bytes=10)

    guard.observe(ZIP[:2])
    guard.observe(ZIP[2:] + b"rest!!")
    guard.finish()

    assert guard.received == 10


def test_the_guard_refuses_a_file_that_does_not_start_as_a_zip() -> None:
    guard = UploadGuard(declared_bytes=100)

    with pytest.raises(UnsupportedFileFormatError):
        guard.observe(b"\xd0\xcf\x11\xe0 an encrypted or old Excel file")


def test_the_guard_refuses_the_first_byte_past_the_limit() -> None:
    guard = UploadGuard(declared_bytes=8, limit=8)
    guard.observe(ZIP + b"1234")

    with pytest.raises(FileTooLargeError):
        guard.observe(b"5")


def test_the_guard_refuses_a_body_longer_than_announced() -> None:
    guard = UploadGuard(declared_bytes=6, limit=100)
    guard.observe(ZIP + b"12")

    with pytest.raises(IncompleteUploadError):
        guard.observe(b"3")


@pytest.mark.parametrize("body", [ZIP[:2], ZIP + b"12"])
def test_the_guard_refuses_a_body_shorter_than_announced(body: bytes) -> None:
    guard = UploadGuard(declared_bytes=10)
    guard.observe(body)

    with pytest.raises((IncompleteUploadError, UnsupportedFileFormatError)):
        guard.finish()
