"""Regression tests for configuration parsing.

Layer: tests (unit)

These cover configuration values that arrive malformed from the environment
rather than from a developer's intent. The Dash prefix case is not hypothetical:
it broke `make up` on Windows, and the failure surfaced as an opaque Dash
configuration error several layers away from its cause.
"""

from __future__ import annotations

import pytest

from energy.core.settings import ArchiveSettings, DashSettings

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("/", "/"),
        ("/dash/", "/dash/"),
        # Missing leading or trailing separators are completed rather than rejected.
        ("dash", "/dash/"),
        ("/dash", "/dash/"),
        # MSYS on Windows rewrites an exported `/` into the Git installation
        # root before it reaches the container. Dash then refuses to start.
        ("D:/git/Git/", "/"),
        (r"C:\Program Files\Git" + "\\", "/"),
        # Empty or whitespace-only means "unset", not "a prefix called nothing".
        ("", "/"),
        ("   ", "/"),
    ],
)
def test_dash_base_pathname_is_normalised(
    monkeypatch: pytest.MonkeyPatch, supplied: str, expected: str
) -> None:
    """A Dash prefix is always a URL path that starts and ends with `/`."""
    monkeypatch.setenv("DASH_URL_BASE_PATHNAME", supplied)

    assert DashSettings().url_base_pathname == expected


def test_dash_app_builds_with_a_mangled_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Dash app starts even when the environment supplies a filesystem path.

    This is the end-to-end form of the bug: without normalisation the container
    crash-loops and `docker compose up --wait` never returns.
    """
    from energy.core.settings import get_settings  # noqa: PLC0415
    from energy.dashboards.app import create_dash_app  # noqa: PLC0415

    monkeypatch.setenv("DASH_URL_BASE_PATHNAME", "D:/git/Git/")
    get_settings.cache_clear()
    try:
        app = create_dash_app()
        assert app.config.routes_pathname_prefix == "/"
    finally:
        get_settings.cache_clear()


def test_archive_defaults_to_minio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without configuration, originals are archived in object storage."""
    monkeypatch.delenv("ARCHIVE_BACKEND", raising=False)
    monkeypatch.delenv("ARCHIVE_DIR", raising=False)

    settings = ArchiveSettings(_env_file=None)

    assert settings.backend == "minio"
    assert settings.local_dir.as_posix() == "data/bronze_archive"


def test_archive_backend_is_read_case_insensitively(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ARCHIVE_BACKEND=Local` selects the directory backend."""
    monkeypatch.setenv("ARCHIVE_BACKEND", " Local ")
    monkeypatch.setenv("ARCHIVE_DIR", "somewhere/else")

    settings = ArchiveSettings(_env_file=None)

    assert settings.backend == "local"
    assert settings.local_dir.as_posix() == "somewhere/else"


def test_archive_backend_rejects_an_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in the backend fails at start-up rather than at the first load."""
    from pydantic import ValidationError  # noqa: PLC0415

    monkeypatch.setenv("ARCHIVE_BACKEND", "s3")

    with pytest.raises(ValidationError):
        ArchiveSettings(_env_file=None)
