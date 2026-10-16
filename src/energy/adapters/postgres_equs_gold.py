"""PostgreSQL implementation of the Gold layer of the equivalent-batteries data.

Layer: adapters

Responsibility:
    Implements :class:`~energy.ports.equs_gold.GoldReader`,
    :class:`~energy.ports.equs_gold.DailyGoldReader` and
    :class:`~energy.ports.equs_gold.GoldMaintenance` against the ``gold`` and
    ``silver`` schemas. The calculation is the views and functions of revisions
    0004 and 0006; this module asks them questions and turns the rows into domain
    records, using the statements of :mod:`energy.adapters.equs_gold_sql`.

    The reader works on whatever it is given: an engine, from which it takes a
    short connection per question, or a connection, which it uses as it is. The
    second is what lets a caller compute a report inside its own transaction,
    for instance to see what a change would do before committing it.

    Maintenance works through the engine. The lock is a session advisory lock
    held on a connection of its own for as long as the block runs; it is taken
    with ``lock_timeout`` set, so waiting for it ends after the given time. Each
    refresh runs in autocommit, so a concurrent refresh holds its locks only as
    long as it takes. A close is one transaction.

Depends on:
    ``energy.ports.equs_gold``, ``energy.domain``, ``energy.core``,
    ``sqlalchemy``.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from energy.adapters import equs_gold_sql as sql
from energy.core.logging import get_logger
from energy.domain.equs_gold import (
    Driver,
    DriverAmount,
    FrozenMonth,
    PolyAmount,
    QualityCount,
    RefreshStep,
    VersionMonth,
)
from energy.domain.equs_series import DailyDriverAmount, ReconciledAmount
from energy.domain.exceptions import CloseRefusedError, LockTimeoutError, RepositoryError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from datetime import date
    from decimal import Decimal

    from sqlalchemy.engine import Engine, Row

    from energy.domain.equs_gold import DateRange

logger = get_logger(__name__)

# SQLSTATE of a lock that was not granted within lock_timeout.
_LOCK_NOT_AVAILABLE: Final = "55P03"


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


def _reason(error: Exception) -> str:
    return str(getattr(error, "orig", None) or error).strip()


class PostgresGoldReader:
    """Answers the read-only questions of the EQUS Gold calculation.

    Args:
        bind: an engine, from which each question takes a short connection, or
            a connection to work on as it is (inside its transaction).
        variation_threshold: the threshold of data-quality check 6, as a
            fraction. ``None`` leaves the database's default of 0.30.
    """

    def __init__(
        self, bind: Engine | Connection, *, variation_threshold: Decimal | None = None
    ) -> None:
        self._bind = bind
        self._threshold = variation_threshold

    @contextmanager
    def _connection(self) -> Iterator[Connection]:
        if isinstance(self._bind, Connection):
            yield self._bind
            return
        with self._bind.connect() as connection:
            yield connection

    def _rows(self, statement: str, **parameters: Any) -> list[Row[Any]]:
        try:
            with self._connection() as connection:
                return list(connection.execute(text(statement), parameters))
        except SQLAlchemyError as exc:
            raise RepositoryError("could not read the EQUS Gold data", reason=_reason(exc)) from exc

    @staticmethod
    def _period(period: DateRange) -> dict[str, date]:
        return {"desde": period.start, "hasta": period.end}

    def versions(self, period: DateRange) -> tuple[VersionMonth, ...]:
        """Return the version of the equation in force in each month of a period."""
        return tuple(
            VersionMonth(row.mes, row.version, row.nombre)
            for row in self._rows(sql.VERSIONS, **self._period(period))
        )

    def drivers(self, versions: Sequence[int]) -> tuple[Driver, ...]:
        """Return every driver of the given versions, with its place in the report."""
        if not versions:
            return ()
        return tuple(
            Driver(
                version=row.version,
                version_name=row.version_name,
                cuenta_stats=row.cuenta_stats,
                descripcion=row.descripcion,
                proceso=row.proceso,
                unidad=row.unidad,
                fuente=row.fuente,
                estandar_por_bat=row.estandar_por_bat,
                multiplicador=row.multiplicador,
                peso=row.peso,
                factor=row.factor,
                suma_eq=row.suma_eq,
                fila=row.fila,
                planta=row.planta,
                area_planta=row.area_planta,
                orden=row.orden,
                peso_proceso=row.peso_proceso,
            )
            for row in self._rows(sql.DRIVERS, versiones=list(versions))
        )

    def amounts(self, period: DateRange, *, closed: bool) -> tuple[DriverAmount, ...]:
        """Return what each driver summed in each month of a period, and its EQ."""
        statement = sql.AMOUNTS_CLOSED if closed else sql.AMOUNTS_LIVE
        return tuple(
            DriverAmount(row.mes, row.version, row.cuenta_stats, row.cantidad, row.equs)
            for row in self._rows(statement, **self._period(period))
        )

    def poly(self, period: DateRange) -> tuple[PolyAmount, ...]:
        """Return EQUS POLY of a period, by month and category."""
        return tuple(
            PolyAmount(
                month=row.mes,
                cuenta=row.cuenta,
                categoria=row.categoria,
                area_planta=row.area_planta,
                piezas=row.piezas,
                piezas_con_equ=row.piezas_con_equ,
                factor_equ=row.factor_equ,
                equs=row.equs,
                fila=row.fila,
            )
            for row in self._rows(sql.POLY, **self._period(period))
        )

    def quality(self, period: DateRange) -> tuple[QualityCount, ...]:
        """Return how many rows each data-quality check returns for a period."""
        try:
            with self._connection() as connection:
                if self._threshold is not None:
                    # Local to the transaction the count runs in.
                    connection.execute(
                        text(sql.SET_VARIATION_THRESHOLD), {"umbral": str(self._threshold)}
                    )
                rows = list(connection.execute(text(sql.QUALITY), self._period(period)))
        except SQLAlchemyError as exc:
            raise RepositoryError(
                "could not run the EQUS data-quality checks", reason=_reason(exc)
            ) from exc
        return tuple(
            QualityCount(row.vista, row.descripcion, row.bloqueante, row.filas) for row in rows
        )

    def closed_months(self, period: DateRange) -> tuple[date, ...]:
        """Return the closed months a period touches."""
        rows = self._rows(sql.CLOSED_MONTHS, desde=period.start.replace(day=1), hasta=period.end)
        return tuple(row.mes for row in rows)

    def closed_processes(self, month: date) -> tuple[tuple[str, Decimal], ...]:
        """Return the frozen EQ of each process of a closed month."""
        return tuple((row.proceso, row.equs) for row in self._rows(sql.CLOSED_PROCESSES, mes=month))

    def previous_closed_month(self, month: date) -> date | None:
        """Return the closest closed month before a month, if any."""
        rows = self._rows(sql.PREVIOUS_CLOSED_MONTH, mes=month)
        return rows[0][0] if rows else None

    def latest_production_month(self) -> date | None:
        """Return the latest month with production receipts, if any."""
        rows = self._rows(sql.LATEST_PRODUCTION_MONTH)
        return rows[0][0] if rows else None

    # -- DailyGoldReader ---------------------------------------------------------
    def daily_amounts(self, period: DateRange) -> tuple[DailyDriverAmount, ...]:
        """Return what each driver summed on each day of a period, and its EQ."""
        return tuple(
            DailyDriverAmount(
                row.fecha_efectiva, row.mes, row.version, row.cuenta_stats, row.cantidad, row.equs
            )
            for row in self._rows(sql.DAILY_AMOUNTS, **self._period(period))
        )

    def daily_reconciliation(self, months: DateRange) -> tuple[ReconciledAmount, ...]:
        """Compare each driver of whole months with the sum of its days."""
        return tuple(
            ReconciledAmount(
                month=row.mes,
                version=row.version,
                cuenta_stats=row.cuenta_stats,
                monthly_cantidad=row.cantidad_mes,
                daily_cantidad=row.cantidad_dias,
                monthly_equs=row.equs_mes,
                daily_equs=row.equs_dias,
                equal=row.coincide,
            )
            for row in self._rows(sql.DAILY_RECONCILIATION, **self._period(months))
        )


class PostgresGoldMaintenance:
    """Refreshes what the EQUS Gold calculation reads, and freezes months.

    Args:
        engine: the SQLAlchemy engine to work through.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def locked(self, timeout_seconds: float) -> Iterator[None]:
        """Hold the refresh lock for the duration of a block.

        Args:
            timeout_seconds: how long to wait for another run to release it.

        Raises:
            LockTimeoutError: if it is not released in time. Nothing is done.
            RepositoryError: if the database cannot be reached.
        """
        try:
            connection = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        except SQLAlchemyError as exc:
            raise RepositoryError("could not reach the database", reason=_reason(exc)) from exc
        try:
            milliseconds = max(1, round(timeout_seconds * 1000))
            started = time.perf_counter()
            try:
                connection.execute(text(sql.SET_LOCK_TIMEOUT), {"timeout": f"{milliseconds}ms"})
                connection.execute(text(sql.TAKE_LOCK), {"key": sql.LOCK_KEY})
                connection.execute(text(sql.SET_LOCK_TIMEOUT), {"timeout": "0"})
            except DBAPIError as exc:
                if _sqlstate(exc) == _LOCK_NOT_AVAILABLE:
                    raise LockTimeoutError(
                        "another refresh or close of EQUS Gold is running and did not finish "
                        "in time; nothing was done",
                        waited_seconds=round(time.perf_counter() - started, 1),
                    ) from exc
                raise RepositoryError(
                    "could not take the refresh lock", reason=_reason(exc)
                ) from exc
            waited = time.perf_counter() - started
            if waited > 1:
                logger.info("refresh lock taken after waiting", seconds=round(waited, 1))
            yield
        finally:
            try:
                connection.execute(text(sql.RELEASE_LOCK), {"key": sql.LOCK_KEY})
            except SQLAlchemyError as exc:  # closing the session releases it anyway
                logger.debug("could not release the refresh lock", reason=_reason(exc))
            connection.close()

    def refresh_views(self, *, concurrently: bool = True) -> tuple[RefreshStep, ...]:
        """Recompute the conversion tables, each in its own autocommit statement.

        Args:
            concurrently: refresh without blocking readers. PostgreSQL refuses
                that for a view that was never populated; the plain refresh then
                runs instead, and the step says so.

        Returns:
            One step per view.

        Raises:
            RepositoryError: if a view cannot be refreshed at all.
        """
        steps: list[RefreshStep] = []
        try:
            with self._engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                for view in sql.MATERIALIZED_VIEWS:
                    started = time.perf_counter()
                    detail = "concurrently" if concurrently else "plain refresh, readers blocked"
                    if concurrently:
                        try:
                            connection.execute(text(sql.REFRESH[(view, True)]))
                        except DBAPIError as exc:
                            logger.debug(
                                "concurrent refresh refused", view=view, reason=_reason(exc)
                            )
                            connection.execute(text(sql.REFRESH[(view, False)]))
                            detail = "plain refresh: a concurrent one was refused"
                    else:
                        connection.execute(text(sql.REFRESH[(view, False)]))
                    steps.append(
                        RefreshStep(f"refresh {view}", time.perf_counter() - started, detail)
                    )
        except SQLAlchemyError as exc:
            raise RepositoryError(
                "could not refresh the conversion tables", reason=_reason(exc)
            ) from exc
        return tuple(steps)

    def analyze(self) -> RefreshStep:
        """Refresh the statistics of the tables the calculation reads."""
        started = time.perf_counter()
        try:
            with self._engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                connection.execute(text(sql.ANALYZE))
        except SQLAlchemyError as exc:
            raise RepositoryError("could not refresh the statistics", reason=_reason(exc)) from exc
        return RefreshStep("analyze", time.perf_counter() - started, "statistics of 9 tables")

    def freeze_month(
        self,
        month: date,
        *,
        closed_by: str,
        reclose: bool,
        accepted_warnings: bool,
        quality: Sequence[QualityCount],
    ) -> FrozenMonth:
        """Freeze the live EQ of a month as its official close, in one transaction.

        Raises:
            CloseRefusedError: if the month is closed and ``reclose`` is false,
                or if it has no production to close. Nothing is written.
            RepositoryError: if the database rejects a statement. Nothing is
                written.
        """
        counts = json.dumps({count.vista: count.filas for count in quality}, sort_keys=True)
        try:
            with self._engine.begin() as connection:
                closed_rows = len(
                    list(connection.execute(text(sql.LOCK_CLOSED_MONTH), {"mes": month}))
                )
                if closed_rows and not reclose:
                    raise CloseRefusedError(
                        "the month is already closed; pass --reclose to replace its close",
                        month=month.isoformat()[:7],
                    )
                live_rows = connection.execute(
                    text(sql.COUNT_LIVE_ROWS), {"mes": month}
                ).scalar_one()
                if not live_rows:
                    raise CloseRefusedError(
                        "the month has nothing to close: no production or invoices in it",
                        month=month.isoformat()[:7],
                    )
                cierre_id = connection.execute(
                    text(sql.RECORD_CLOSE),
                    {
                        "mes": month,
                        "closed_by": closed_by,
                        "recierre": bool(closed_rows),
                        "advertencias_aceptadas": accepted_warnings,
                        "calidad_datos": counts,
                    },
                ).scalar_one()
                replaced = 0
                if closed_rows:
                    replaced = connection.execute(
                        text(sql.KEEP_REPLACED_ROWS), {"cierre_id": cierre_id, "mes": month}
                    ).rowcount
                written = connection.execute(
                    text(sql.FREEZE), {"mes": month, "closed_by": closed_by}
                ).rowcount
                connection.execute(text(sql.DROP_LEFTOVER_ROWS), {"mes": month})
        except SQLAlchemyError as exc:
            raise RepositoryError("could not close the month", reason=_reason(exc)) from exc
        return FrozenMonth(
            month=month,
            cierre_id=cierre_id,
            rows=written,
            reclosed=bool(closed_rows),
            replaced=replaced,
            accepted_warnings=accepted_warnings,
        )


__all__ = ["PostgresGoldMaintenance", "PostgresGoldReader"]
