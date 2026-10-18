"""PostgreSQL implementation of the reporting login BI tools such as Power BI use.

Layer: adapters

Responsibility:
    Creates, or renews, the login a BI tool connects with, and makes sure it can
    read the views of schema ``reporting`` and nothing else. Revision 0006
    created the views and the group role ``reporting_reader`` that may read
    them; this module only adds a login to that role. It runs in one
    transaction: either the login is ready, or nothing changed.

    The login is never given more than the group grants: it is created without
    any privilege attribute, its transactions are read-only by default, and an
    existing role that holds a privilege or owns anything is refused rather
    than demoted. The password is hashed (SCRAM) by the client library before
    it is sent, so the plain text never reaches the server or its logs.

Depends on:
    ``energy.domain``, ``energy.core``, ``sqlalchemy``, ``psycopg``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import psycopg
from psycopg import sql
from sqlalchemy.exc import SQLAlchemyError

from energy.core.constants import REPORTING_SCHEMA
from energy.core.logging import get_logger
from energy.domain.exceptions import ConfigurationError, RepositoryError
from energy.domain.reporting import ReaderAccess

if TYPE_CHECKING:
    from decimal import Decimal

    from sqlalchemy.engine import Engine

logger = get_logger(__name__)

#: The group role revision 0006 grants the reporting views to.
READER_ROLE: Final = "reporting_reader"

# How many connections one BI login may hold, and how long one query may run.
CONNECTION_LIMIT: Final = 10
STATEMENT_TIMEOUT: Final = "10min"

# The session setting data-quality check 6 reads its threshold from.
VARIATION_SETTING: Final = ("equs", "umbral_variacion")

_PREREQUISITES: Final = (
    "SELECT to_regnamespace(%s) IS NOT NULL, EXISTS (SELECT FROM pg_roles WHERE rolname = %s)"
)

# Whether a role exists, and whether it may do more than read: a privilege
# attribute, being the role this application runs as, or owning a schema or a
# relation in this database.
_EXISTING_ROLE: Final = """
SELECT rol.rolsuper OR rol.rolcreaterole OR rol.rolcreatedb OR rol.rolreplication
           OR rol.rolbypassrls
           OR rol.rolname = current_user
           OR EXISTS (SELECT FROM pg_namespace WHERE nspowner = rol.oid)
           OR EXISTS (SELECT FROM pg_class WHERE relowner = rol.oid)
FROM pg_roles AS rol
WHERE rol.rolname = %s
"""

_READABLE_VIEWS: Final = """
SELECT viewname
FROM pg_views
WHERE schemaname = %s
  AND has_table_privilege(%s, format('%%I.%%I', schemaname, viewname), 'SELECT')
ORDER BY viewname
"""

_CREATE: Final = sql.SQL(
    "CREATE ROLE {user} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT "
    "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {limit} PASSWORD {password}"
)
_ALTER: Final = sql.SQL(
    "ALTER ROLE {user} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT "
    "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT {limit} PASSWORD {password}"
)
_GRANT: Final = sql.SQL("GRANT {group} TO {user}")
_SET: Final = sql.SQL("ALTER ROLE {user} SET {name} = {value}")


class PostgresReportingAccess:
    """Grants a BI tool its read-only login on the reporting schema.

    Args:
        engine: the SQLAlchemy engine to work through. Its role must be allowed
            to create roles, as the owner of the database is.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def ensure_reader(
        self, user: str, password: str, *, variation_threshold: Decimal | None = None
    ) -> ReaderAccess:
        """Create the login, or renew its password and settings.

        Args:
            user: the login.
            password: its password, in plain text; only its hash is sent.
            variation_threshold: the threshold of data-quality check 6, so the
                counts the login reads match those of the command line.
                ``None`` leaves the database's default.

        Returns:
            The login and the views it can read.

        Raises:
            ConfigurationError: if the reporting schema is missing (the database
                is not migrated), or if ``user`` names a role that may do more
                than read. Nothing is changed.
            RepositoryError: if the database refuses. Nothing is changed.
        """
        try:
            with self._engine.begin() as connection:
                driver_connection = connection.connection.driver_connection
                if driver_connection is None:  # pragma: no cover - a live connection has one
                    raise RepositoryError("database connection is closed")
                with driver_connection.cursor() as cursor:
                    self._require_schema(cursor)
                    created = self._check_role(cursor, user)
                    verifier = driver_connection.pgconn.encrypt_password(
                        password.encode(), user.encode(), b"scram-sha-256"
                    )
                    self._write_login(cursor, user, verifier.decode("ascii"), created=created)
                    self._configure(cursor, user, variation_threshold)
                    cursor.execute(_READABLE_VIEWS, (REPORTING_SCHEMA, user))
                    views = tuple(row[0] for row in cursor.fetchall())
                    database = str(driver_connection.info.dbname)
        except (SQLAlchemyError, psycopg.Error) as exc:
            raise RepositoryError(
                "could not create the reporting login", reason=str(exc).strip()
            ) from exc

        logger.info("reporting login ready", user=user, created=created, views=len(views))
        return ReaderAccess(user=user, database=database, created=created, views=views)

    @staticmethod
    def _require_schema(cursor: psycopg.Cursor[Any]) -> None:
        """Refuse when revision 0006 has not run on this database."""
        cursor.execute(_PREREQUISITES, (REPORTING_SCHEMA, READER_ROLE))
        row = cursor.fetchone()
        if row is None or not (row[0] and row[1]):
            raise ConfigurationError(
                "the reporting schema or its reader role is missing; run make db-upgrade first",
                schema=REPORTING_SCHEMA,
                role=READER_ROLE,
            )

    @staticmethod
    def _check_role(cursor: psycopg.Cursor[Any], user: str) -> bool:
        """Return whether the login is new, refusing a role that may do more than read."""
        cursor.execute(_EXISTING_ROLE, (user,))
        row = cursor.fetchone()
        if row is None:
            return True
        if row[0]:
            raise ConfigurationError(
                "REPORTING_DB_USER names a role that may do more than read (a privileged "
                "role, the application's own role, or an owner of database objects); choose "
                "another name",
                user=user,
            )
        return False

    @staticmethod
    def _write_login(
        cursor: psycopg.Cursor[Any], user: str, verifier: str, *, created: bool
    ) -> None:
        """Create or alter the login, and make it a member of the reader group."""
        statement = _CREATE if created else _ALTER
        cursor.execute(
            statement.format(
                user=sql.Identifier(user),
                limit=sql.Literal(CONNECTION_LIMIT),
                password=sql.Literal(verifier),
            )
        )
        cursor.execute(_GRANT.format(group=sql.Identifier(READER_ROLE), user=sql.Identifier(user)))

    @staticmethod
    def _configure(
        cursor: psycopg.Cursor[Any], user: str, variation_threshold: Decimal | None
    ) -> None:
        """Make every session of the login read-only and pointed at the reporting schema."""
        settings: list[tuple[sql.Identifier, str]] = [
            (sql.Identifier("default_transaction_read_only"), "on"),
            (sql.Identifier("search_path"), REPORTING_SCHEMA),
            (sql.Identifier("statement_timeout"), STATEMENT_TIMEOUT),
        ]
        if variation_threshold is not None:
            settings.append((sql.Identifier(*VARIATION_SETTING), str(variation_threshold)))
        for name, value in settings:
            cursor.execute(
                _SET.format(user=sql.Identifier(user), name=name, value=sql.Literal(value))
            )


__all__ = ["CONNECTION_LIMIT", "READER_ROLE", "STATEMENT_TIMEOUT", "PostgresReportingAccess"]
