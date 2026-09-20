"""Alembic environment configuration.

Resolves the database URL from application settings rather than from a static
config file, so migrations, the API and the pipelines can never disagree about
which database they are pointed at.

Two safety measures worth knowing about:

* ``include_schemas=True`` is required because the platform spreads its tables
  across four schemas (``reference``, ``bronze``, ``silver``, ``gold``). Without
  it Alembic only sees ``public`` and would consider every table missing.
* Autogeneration is guarded. The SQLModel table classes are Phase 2
  placeholders, so ``target_metadata`` is currently empty; left unguarded,
  ``--autogenerate`` would read that as "every table should be dropped" and
  emit a migration that destroys the database. :func:`include_object` refuses to
  consider any object while the metadata is empty.
"""

from __future__ import annotations

import sys
from contextlib import suppress
from logging.config import fileConfig
from pathlib import Path
from typing import TYPE_CHECKING, Any

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# Make `src` importable when Alembic is invoked from the repository root.
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from energy.core.constants import ALL_SCHEMAS  # noqa: E402
from energy.core.settings import get_settings  # noqa: E402

config = context.config

if config.config_file_name is not None:
    # pyproject.toml carries no [loggers] section; Alembic's own default logging
    # is fine and the application configures structlog separately.
    with suppress(KeyError, OSError):
        fileConfig(config.config_file_name)

# Phase 2 replaces the placeholder model modules with real SQLModel tables, at
# which point importing them here registers them on SQLModel.metadata and
# --autogenerate becomes usable.
target_metadata = SQLModel.metadata


def get_url() -> str:
    """Return the database URL for migrations.

    Returns:
        The DSN from application settings, which honours ``DATABASE_URL`` and
        otherwise assembles the URL from the individual ``POSTGRES_*`` parts.
    """
    return get_settings().database.url


def include_object(
    _object: Any,
    name: str | None,
    type_: str,
    reflected: bool,
    _compare_to: Any,
) -> bool:
    """Decide whether an object participates in autogeneration.

    Args:
        _object: the schema object being considered.
        name: its name.
        type_: what kind of object it is (``table``, ``index``, ...).
        reflected: whether it was read from the database rather than the metadata.
        _compare_to: the object it is being compared against.

    Returns:
        ``False`` for everything while ``target_metadata`` is empty, which stops
        autogenerate from proposing to drop the hand-written schema. Also
        excludes TimescaleDB's own internal catalog objects, which must never
        appear in a migration.
    """
    if type_ == "table" and name in {"alembic_version"}:
        return False
    return not (reflected and not target_metadata.tables)


def include_name(name: str | None, type_: str, _parent_names: dict[str, str | None]) -> bool:
    """Restrict reflection to the schemas this application owns.

    TimescaleDB creates internal schemas (``_timescaledb_internal``,
    ``_timescaledb_catalog``, and one chunk schema per hypertable). Reflecting
    them would make every autogenerate run propose dropping the extension's own
    machinery.

    Args:
        name: the schema or object name.
        type_: what kind of name it is.
        _parent_names: the enclosing names.

    Returns:
        ``True`` when the name belongs to an application-owned schema.
    """
    if type_ == "schema":
        return name in (None, "public", *ALL_SCHEMAS)
    return True


def run_migrations_offline() -> None:
    """Run migrations without a DBAPI connection, emitting SQL to stdout.

    Used to produce a reviewable script for a production change window.
    """
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        include_name=include_name,
        version_table_schema=None,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure the context against a live connection and run the migrations.

    Args:
        connection: an open connection to migrate.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        include_name=include_name,
        compare_type=True,
        compare_server_default=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
