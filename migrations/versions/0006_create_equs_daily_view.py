"""Add the daily equivalent batteries of EQUS Gold.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15

``gold.v_resumen_equ`` gives the equivalent batteries (EQ) of each driver in
each month: column H of sheet RESUMEN EQU. ``gold.v_resumen_equ_diario`` gives
the same for each day, so that the EQ of a process can be followed day by day
and, later, set against the energy it used on that day.

The day is the accounting date of each production receipt and invoice line
(*Fecha Efectiva*), the date whose month already decides where a row belongs.
Three things are kept from the monthly view on purpose:

The version is the month's
    A row uses the version of the equation in force in its month: the one whose
    validity contains the month's first day, as ``gold.en_vigor`` decides. The
    daily view reads the two pairing views of revision 0004, which already
    carry that decision, so a version starting in the middle of a month does
    not change the second half of it.

The days add up to the month, exactly
    The view sums the same pairing rows as ``gold.v_cantidad_cuenta``, grouped
    by day instead of by month, and multiplies by the same factor. Every value
    is ``NUMERIC``, which PostgreSQL adds and multiplies without rounding, so
    the days of a month sum to ``v_resumen_equ`` digit for digit.

Rows are sparse
    A driver nothing fed on a day has no row that day, as a driver nothing fed
    in a month has no row in ``v_resumen_equ``.

Every driver is in the view, the informative ones too, so that it reconciles
row for row with the monthly view; ``suma_eq`` says which add to EQUS BATT. Like
the rest of EQUS Gold, the view writes no number of the equation and is not
materialized.

This is a revision of its own rather than a change to 0004 because a database
can only take a rewritten 0004 by going down to 0003, which drops the ``gold``
schema and every closed month with it. Going down from this revision drops the
view alone.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The equivalent batteries of each driver on each day, with the version in
# force in the day's month. The same pairing views and the same factor as
# v_cantidad_cuenta and v_resumen_equ, grouped by the day of each row.
V_RESUMEN_EQU_DIARIO = """
CREATE VIEW gold.v_resumen_equ_diario AS
WITH pareja AS (
    SELECT produccion.fecha_efectiva, produccion.mes, produccion.version,
           produccion.cuenta_stats, produccion.valor
    FROM gold.v_reporte_qad_regla AS produccion
    WHERE produccion.en_vigor
    UNION ALL
    SELECT factura.fecha_efectiva, factura.mes, factura.version,
           factura.cuenta_stats, factura.valor
    FROM gold.v_unit_ship_regla AS factura
    WHERE factura.en_vigor
),
suma AS (
    SELECT pareja.fecha_efectiva, pareja.mes, pareja.version, pareja.cuenta_stats,
           coalesce(sum(pareja.valor), 0) AS cantidad
    FROM pareja
    GROUP BY pareja.fecha_efectiva, pareja.mes, pareja.version, pareja.cuenta_stats
)
SELECT suma.fecha_efectiva,
       suma.mes,
       suma.version,
       factor.proceso,
       suma.cuenta_stats,
       factor.descripcion,
       suma.cantidad,
       factor.factor,
       suma.cantidad * factor.factor AS equs,
       factor.suma_eq
FROM suma
JOIN silver.factores_equ AS factor
  ON factor.version = suma.version AND factor.cuenta_stats = suma.cuenta_stats
"""


def upgrade() -> None:
    """Create the daily view."""
    op.execute(V_RESUMEN_EQU_DIARIO)


def downgrade() -> None:
    """Drop the daily view. Nothing else of Gold is touched, closed months included."""
    op.execute("DROP VIEW IF EXISTS gold.v_resumen_equ_diario")
