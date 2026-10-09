"""The read-only login BI tools use to read the reporting schema, as plain data.

Layer: domain

Responsibility:
    A BI tool such as Power BI connects to the database with a login of its
    own, which may read the views of schema ``reporting`` and nothing else.
    This module holds what creating that login returns, and the text that tells
    a person how to connect with it. Creating the login is the adapter's job;
    which views exist is the migration's.

Depends on:
    standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Bind addresses that mean "every interface": a client cannot connect to them.
# They are compared here, never bound.
_ANY_ADDRESS: Final = frozenset({"0.0.0.0", "::", ""})  # noqa: S104  # nosec B104


@dataclass(frozen=True, slots=True)
class ReaderAccess:
    """The login a BI tool reads the reporting schema with.

    Attributes:
        user: the login.
        database: the database whose reporting schema it reads.
        created: ``True`` when the login was created, ``False`` when it existed
            and only its password and settings were renewed.
        views: the views it can read, in alphabetical order.
    """

    user: str
    database: str
    created: bool
    views: tuple[str, ...]


def describe_reader(access: ReaderAccess, *, host: str, port: int) -> list[str]:
    """Return what to type into a BI tool to connect, as lines of text.

    Args:
        access: the login.
        host: the address the bridge listens on.
        port: the port the bridge publishes.

    Returns:
        The lines: what was done, then the connection details.
    """
    action = "Created" if access.created else "Updated"
    server = f"<this machine's address>:{port}" if host in _ANY_ADDRESS else f"{host}:{port}"
    return [
        f"{action} the reporting login {access.user!r}: it reads the "
        f"{len(access.views)} views of schema reporting, and nothing else.",
        "",
        "Connect Power BI Desktop with Get data > PostgreSQL database:",
        f"  Server     {server}",
        f"  Database   {access.database}",
        "  Mode       Import",
        f"  User       {access.user}   (Database credentials)",
        "  Password   REPORTING_DB_PASSWORD from .env",
        "",
        "The port is open only while the bridge runs: make powerbi opens it,",
        "make powerbi-down closes it.",
    ]


__all__ = ["ReaderAccess", "describe_reader"]
