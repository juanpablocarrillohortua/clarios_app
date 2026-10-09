"""Tests for the reporting login Power BI uses: its settings, its text, its command.

Layer: tests (unit)

Creating the login needs a database and is tested in
``tests/integration/test_equs_gold_rules.py``. Here the flow is replaced, so
these tests check what the command line owns: what it prints and the exit code a
shell sees.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

import energy.__main__ as cli
from energy.core.settings import ReportingSettings, get_settings
from energy.domain.exceptions import ConfigurationError, RepositoryError
from energy.domain.reporting import ReaderAccess, describe_reader

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

runner = CliRunner()

ACCESS = ReaderAccess(
    user="powerbi", database="energy", created=True, views=("cuentas", "equs_mensual")
)

_VARIABLES = (
    "REPORTING_DB_USER",
    "REPORTING_DB_PASSWORD",
    "REPORTING_BIND_ADDRESS",
    "REPORTING_PORT",
)


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No REPORTING_* variable from the shell, and fresh settings around the test."""
    for name in _VARIABLES:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# -- The text -----------------------------------------------------------------------
def test_the_text_says_what_to_type_into_power_bi() -> None:
    """Server, database and user, as Power BI's PostgreSQL dialog asks for them."""
    lines = describe_reader(ACCESS, host="127.0.0.1", port=5433)
    text = "\n".join(lines)
    assert lines[0].startswith("Created the reporting login 'powerbi'")
    assert "2 views of schema reporting" in lines[0]
    assert "Server     127.0.0.1:5433" in text
    assert "Database   energy" in text
    assert "User       powerbi" in text
    assert "REPORTING_DB_PASSWORD" in text


def test_an_existing_login_is_updated() -> None:
    """A second run renews the login rather than creating it."""
    renewed = ReaderAccess(user="powerbi", database="energy", created=False, views=())
    assert describe_reader(renewed, host="127.0.0.1", port=5433)[0].startswith("Updated")


@pytest.mark.parametrize("host", ["0.0.0.0", "::", ""])  # noqa: S104
def test_an_address_that_means_every_interface_is_not_printed_as_the_server(host: str) -> None:
    """A client cannot connect to 0.0.0.0; the text asks for this machine's address."""
    text = "\n".join(describe_reader(ACCESS, host=host, port=5433))
    assert "Server     <this machine's address>:5433" in text


# -- The settings -------------------------------------------------------------------
@pytest.mark.usefixtures("clean_environment")
def test_the_defaults_keep_the_port_on_this_machine() -> None:
    """User powerbi, no password, 127.0.0.1:5433."""
    settings = ReportingSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.db_user == "powerbi"
    assert settings.db_password is None
    assert (settings.bind_address, settings.port) == ("127.0.0.1", 5433)


@pytest.mark.usefixtures("clean_environment")
@pytest.mark.parametrize("name", ["PowerBI", "power-bi", "1reader", "pg_reader", "a" * 64, ""])
def test_a_role_name_that_needs_quoting_is_rejected(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plain lower-case names only, and never PostgreSQL's own pg_ prefix."""
    monkeypatch.setenv("REPORTING_DB_USER", name)
    with pytest.raises(ValidationError, match="REPORTING_DB_USER"):
        ReportingSettings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.usefixtures("clean_environment")
def test_the_port_must_be_a_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """1 to 65535."""
    monkeypatch.setenv("REPORTING_PORT", "70000")
    with pytest.raises(ValidationError):
        ReportingSettings(_env_file=None)  # type: ignore[call-arg]


# -- The command --------------------------------------------------------------------
@pytest.mark.usefixtures("clean_environment")
def test_create_reader_prints_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exit 0, and the server comes from the bridge settings."""
    monkeypatch.setenv("REPORTING_BIND_ADDRESS", "127.0.0.1")
    monkeypatch.setenv("REPORTING_PORT", "6543")
    monkeypatch.setattr(cli, "create_reader", lambda: ACCESS)

    result = runner.invoke(cli.build_app(), ["reporting", "create-reader"])

    assert result.exit_code == 0, result.output
    assert "Server     127.0.0.1:6543" in result.stdout


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ConfigurationError("REPORTING_DB_PASSWORD is not set"), 1),
        (RepositoryError("could not create the reporting login"), 2),
    ],
)
def test_create_reader_exit_codes(
    error: Exception, code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 for a configuration problem, 2 for the database, as the Gold commands."""

    def refuse() -> ReaderAccess:
        raise error

    monkeypatch.setattr(cli, "create_reader", refuse)

    result = runner.invoke(cli.build_app(), ["reporting", "create-reader"])

    assert result.exit_code == code
    assert "error:" in result.stderr


def test_the_reporting_group_lists_its_command() -> None:
    """``energy reporting --help`` names create-reader."""
    result = runner.invoke(cli.build_app(), ["reporting", "--help"])
    assert result.exit_code == 0
    assert "create-reader" in result.stdout
