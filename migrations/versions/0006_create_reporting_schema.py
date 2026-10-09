"""Create the reporting schema: flat, read-only views for BI tools such as Power BI.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-08

A BI tool connects to the database directly, with a login of its own. What that
login may read is decided here, once, rather than in the tool.

One schema, read-only, curated
    ``reporting`` holds only views. They restate Gold, and the Silver tables
    Gold reads, as flat tables a BI tool can load and relate: facts by month
    (column H, EQUS BATT, EQUS POLY, the data-quality counts), the frozen
    closes, the contribution of each receipt and invoice line to each driver,
    and the dimensions (drivers, items). Bronze, Silver and Gold stay out of
    reach.

The views read as their owner
    A view reads its tables with the privileges of the role that owns it, so a
    login granted ``SELECT`` on these views needs nothing on ``gold`` or
    ``silver``. For the same reason no view here calls a Gold *function*: a
    function runs with the privileges of whoever queries, and the reader has
    none. The views read Gold *views* instead, and repeat a range function's
    grouping where they need it.

Nothing is calculated twice
    Every number comes from a Gold view: ``equs_mensual`` is
    ``gold.v_resumen_equ`` with a zero for each driver nothing fed, and
    ``aporte_cuenta`` is the same two pairing views, row by row, so the detail
    adds up to the month exactly.

Keys a BI tool can relate
    Power BI relates tables on one column, so every view keyed by a driver
    carries ``cuenta_id`` (``version:cuenta_stats``), and the fiscal year is a
    column (``anio_fiscal``) rather than something each report recomputes.

A role, not a login
    This revision creates the group role ``reporting_reader`` and grants it the
    views. The login a tool uses (``powerbi`` by default) is created by
    ``energy reporting create-reader``, because its password must not live in a
    migration. Roles belong to the whole PostgreSQL cluster rather than to one
    database, so the role is created only if missing, and the downgrade leaves
    it in place while another database still grants it something.

The tool reaches the database through ``reporting-bridge``, an opt-in container
of the compose stack that publishes one port; the rest of the stack still
publishes nothing but Caddy.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REPORTING = "reporting"
READER_ROLE = "reporting_reader"

# The statements are written out rather than assembled from the names above:
# they select, and a query built from strings is what the security linters are
# right to question.

CREATE_READER_ROLE = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'reporting_reader') THEN
        CREATE ROLE reporting_reader NOLOGIN;
    END IF;
END
$$
"""

# Roles are shared by every database of the cluster. While another database
# still grants this one something, PostgreSQL refuses to drop it, and that is
# not a reason to fail this downgrade.
DROP_READER_ROLE = """
DO $$
BEGIN
    DROP ROLE IF EXISTS reporting_reader;
EXCEPTION
    WHEN dependent_objects_still_exist THEN
        RAISE NOTICE 'reporting_reader is still used by another database; it is left in place';
END
$$
"""

# The fiscal year runs from October to September and is named after the year it
# closes in: October 2025 to September 2026 is FY26.
ANIO_FISCAL = """
CREATE FUNCTION reporting.anio_fiscal(p_mes date)
RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT 'FY' || lpad((extract(year FROM p_mes + interval '3 months')::integer % 100)::text,
                          2, '0') $$
"""

# Every driver of every version, with where RESUMEN EQU shows it.
CUENTAS = """
CREATE VIEW reporting.cuentas AS
SELECT factor.version || ':' || factor.cuenta_stats AS cuenta_id,
       factor.version,
       version.nombre AS version_nombre,
       version.vigente_desde,
       version.vigente_hasta,
       factor.cuenta_stats,
       factor.descripcion,
       factor.proceso,
       seccion.planta,
       seccion.area_planta,
       seccion.orden AS orden_proceso,
       fila.fila,
       factor.unidad,
       factor.fuente,
       factor.estandar_por_bat,
       factor.multiplicador,
       factor.peso,
       factor.factor,
       factor.suma_eq
FROM silver.factores_equ AS factor
JOIN silver.equs_version AS version ON version.version = factor.version
LEFT JOIN gold.secciones_reporte AS seccion ON seccion.proceso = factor.proceso
LEFT JOIN gold.filas_resumen_equ AS fila ON fila.cuenta_stats = factor.cuenta_stats
"""

# Every item, with the description of its product line.
ARTICULOS = """
CREATE VIEW reporting.articulos AS
SELECT articulo.numero_articulo,
       articulo.descripcion,
       articulo.descripcion2,
       articulo.unidad_de_medida,
       articulo.linea_de_productos,
       linea.descripcion AS linea_de_productos_descripcion,
       articulo.campo_equs,
       articulo.tipo_de_articulo,
       articulo.grpo,
       articulo.familia_placa,
       articulo.peso_neto,
       articulo.estado
FROM silver.item_master AS articulo
LEFT JOIN silver.linea_de_productos AS linea
       ON linea.linea_de_productos = articulo.linea_de_productos
"""

# Column H by month: every driver of the version in force, with 0 for a driver
# nothing fed, for each month with production receipts or invoices. A month no
# version covers has no rows, as the report refuses it.
EQUS_MENSUAL = """
CREATE VIEW reporting.equs_mensual AS
WITH meses AS (
    SELECT DISTINCT date_trunc('month', recibo.fecha_efectiva::timestamp)::date AS mes
    FROM silver.reporte_qad AS recibo
    WHERE recibo.tipo_transaccion = 'RCT-WO'
    UNION
    SELECT DISTINCT date_trunc('month', factura.fecha_efectiva::timestamp)::date
    FROM silver.unit_ship AS factura
)
SELECT meses.mes,
       reporting.anio_fiscal(meses.mes) AS anio_fiscal,
       cuenta.cuenta_id,
       cuenta.version,
       cuenta.cuenta_stats,
       cuenta.descripcion,
       cuenta.proceso,
       cuenta.planta,
       cuenta.area_planta,
       cuenta.fila,
       cuenta.unidad,
       coalesce(resumen.cantidad, 0) AS cantidad,
       cuenta.factor,
       coalesce(resumen.equs, 0) AS equs,
       cuenta.suma_eq
FROM meses
JOIN reporting.cuentas AS cuenta
  ON gold.en_vigor(cuenta.vigente_desde, cuenta.vigente_hasta, meses.mes)
LEFT JOIN gold.v_resumen_equ AS resumen
       ON resumen.mes = meses.mes
      AND resumen.version = cuenta.version
      AND resumen.cuenta_stats = cuenta.cuenta_stats
"""

# Rows 36 to 45 of RESUMEN EQU, by month.
EQUS_BATT_MENSUAL = """
CREATE VIEW reporting.equs_batt_mensual AS
SELECT total.mes,
       reporting.anio_fiscal(total.mes) AS anio_fiscal,
       total.equs_orec,
       total.equs_ensamble,
       total.equs_formacion,
       total.equs_decorado,
       total.equs_shipping,
       total.equs_batt
FROM gold.v_equs_batt_mes AS total
"""

# EQUS POLY by month and category: the grouping of gold.inyeccion_rango_mes,
# over every month.
EQUS_POLY_MENSUAL = """
CREATE VIEW reporting.equs_poly_mensual AS
SELECT recibo.mes,
       reporting.anio_fiscal(recibo.mes) AS anio_fiscal,
       recibo.cuenta,
       recibo.new_equ AS categoria,
       seccion.area_planta,
       fila.fila,
       sum(recibo.cambiar_cantidad_de_ubic) AS piezas,
       coalesce(sum(recibo.cambiar_cantidad_de_ubic) FILTER (WHERE recibo.factor_equ > 0), 0)
           AS piezas_con_equ,
       CASE WHEN min(recibo.factor_equ) = max(recibo.factor_equ)
            THEN min(recibo.factor_equ) END AS factor_equ,
       coalesce(sum(recibo.valor_equ), 0) AS equs
FROM gold.v_reporte_qad AS recibo
LEFT JOIN gold.secciones_reporte AS seccion ON seccion.proceso = 'POLY'
LEFT JOIN gold.filas_resumen_equ AS fila ON fila.cuenta_stats = recibo.cuenta
WHERE recibo.almac = '1010'
GROUP BY recibo.mes, recibo.cuenta, recibo.new_equ, seccion.area_planta, fila.fila
"""

# What each production receipt and invoice line contributed to each driver,
# under the version in force in its month. Summed by month and driver it is
# exactly reporting.equs_mensual.
APORTE_CUENTA = """
CREATE VIEW reporting.aporte_cuenta AS
SELECT 'recibo' AS origen,
       produccion.numero_de_transaccion,
       produccion.numero_de_transaccion::text AS documento,
       produccion.fecha_efectiva,
       produccion.mes,
       reporting.anio_fiscal(produccion.mes) AS anio_fiscal,
       produccion.numero_articulo,
       produccion.version || ':' || produccion.cuenta_stats AS cuenta_id,
       produccion.version,
       produccion.cuenta_stats,
       produccion.proceso,
       produccion.regla_n,
       produccion.valor,
       factor.factor,
       produccion.valor * factor.factor AS equs,
       factor.suma_eq
FROM gold.v_reporte_qad_regla AS produccion
JOIN silver.factores_equ AS factor
  ON factor.version = produccion.version AND factor.cuenta_stats = produccion.cuenta_stats
WHERE produccion.en_vigor
UNION ALL
SELECT 'factura',
       NULL,
       factura.num_fact || '/' || factura.orden || '/' || factura.linea,
       factura.fecha_efectiva,
       factura.mes,
       reporting.anio_fiscal(factura.mes),
       factura.numero_articulo,
       factura.version || ':' || factura.cuenta_stats,
       factura.version,
       factura.cuenta_stats,
       factura.proceso,
       factura.regla_n,
       factura.valor,
       factor.factor,
       factura.valor * factor.factor,
       factor.suma_eq
FROM gold.v_unit_ship_regla AS factura
JOIN silver.factores_equ AS factor
  ON factor.version = factura.version AND factor.cuenta_stats = factura.cuenta_stats
WHERE factura.en_vigor
"""

# Every production receipt, with what the calculation adds to it.
RECIBOS_PRODUCCION = """
CREATE VIEW reporting.recibos_produccion AS
SELECT recibo.numero_de_transaccion,
       recibo.fecha_efectiva,
       recibo.mes,
       reporting.anio_fiscal(recibo.mes) AS anio_fiscal,
       recibo.numero_articulo,
       articulo.descripcion AS articulo_descripcion,
       recibo.linea_de_productos,
       recibo.campo_equs_eq AS campo_equs,
       recibo.almac,
       recibo.linea_produccion,
       linea.planta AS area_planta,
       recibo.cambiar_cantidad_de_ubic AS cantidad,
       recibo.mat_activo,
       recibo.comp_deco,
       recibo.new_equ AS categoria_poly,
       recibo.cuenta AS cuenta_poly,
       recibo.factor_equ AS factor_poly,
       recibo.valor_equ AS equs_poly
FROM gold.v_reporte_qad AS recibo
JOIN silver.item_master AS articulo ON articulo.numero_articulo = recibo.numero_articulo
LEFT JOIN silver.lineas_produccion AS linea ON linea.linea = recibo.linea_produccion
"""

# How many rows each data-quality check returns, by month, 0 included: by the
# date of the receipt, and by month for the monthly check 6, as
# gold.calidad_datos_rango counts them.
CALIDAD_DATOS_MENSUAL = """
CREATE VIEW reporting.calidad_datos_mensual AS
WITH meses AS (
    SELECT DISTINCT date_trunc('month', recibo.fecha_efectiva::timestamp)::date AS mes
    FROM silver.reporte_qad AS recibo
    WHERE recibo.tipo_transaccion = 'RCT-WO'
    UNION
    SELECT DISTINCT date_trunc('month', factura.fecha_efectiva::timestamp)::date
    FROM silver.unit_ship AS factura
),
conteo (mes, vista, filas) AS (
    SELECT date_trunc('month', fila.fecha_efectiva::timestamp)::date, 'dq_sin_regla', count(*)
    FROM gold.dq_sin_regla AS fila GROUP BY 1
    UNION ALL
    SELECT date_trunc('month', fila.fecha_efectiva::timestamp)::date, 'dq_doble_regla', count(*)
    FROM gold.dq_doble_regla AS fila GROUP BY 1
    UNION ALL
    SELECT date_trunc('month', fila.fecha_efectiva::timestamp)::date, 'dq_sin_conversor', count(*)
    FROM gold.dq_sin_conversor AS fila GROUP BY 1
    UNION ALL
    SELECT date_trunc('month', fila.fecha_efectiva::timestamp)::date, 'dq_categoria_poly', count(*)
    FROM gold.dq_categoria_poly AS fila GROUP BY 1
    UNION ALL
    SELECT date_trunc('month', fila.fecha_efectiva::timestamp)::date, 'dq_linea_sin_planta',
           count(*)
    FROM gold.dq_linea_sin_planta AS fila GROUP BY 1
    UNION ALL
    SELECT fila.mes, 'dq_variacion', count(*)
    FROM gold.dq_variacion AS fila GROUP BY 1
)
SELECT meses.mes,
       reporting.anio_fiscal(meses.mes) AS anio_fiscal,
       control.vista,
       control.descripcion,
       control.bloqueante,
       coalesce(conteo.filas, 0) AS filas
FROM meses
CROSS JOIN gold.controles_calidad AS control
LEFT JOIN conteo ON conteo.mes = meses.mes AND conteo.vista = control.vista
"""

# Every close and reclose; `vigente` marks the latest close of each month.
CIERRES = """
CREATE VIEW reporting.cierres AS
SELECT cierre.cierre_id,
       cierre.mes,
       reporting.anio_fiscal(cierre.mes) AS anio_fiscal,
       cierre.cerrado_en,
       cierre.closed_by,
       cierre.recierre,
       cierre.advertencias_aceptadas,
       cierre.cierre_id = max(cierre.cierre_id) OVER (PARTITION BY cierre.mes) AS vigente
FROM gold.cierre_mes AS cierre
"""

# The data-quality counts each close saw, one row per check.
CIERRES_CALIDAD = """
CREATE VIEW reporting.cierres_calidad AS
SELECT cierre.cierre_id,
       cierre.mes,
       conteo.key AS vista,
       control.descripcion,
       control.bloqueante,
       conteo.value::bigint AS filas
FROM gold.cierre_mes AS cierre
CROSS JOIN LATERAL jsonb_each_text(cierre.calidad_datos) AS conteo
LEFT JOIN gold.controles_calidad AS control ON control.vista = conteo.key
"""

# The official close of each closed month, driver by driver.
EQUS_CERRADO = """
CREATE VIEW reporting.equs_cerrado AS
SELECT cierre.mes,
       reporting.anio_fiscal(cierre.mes) AS anio_fiscal,
       cuenta.cuenta_id,
       cierre.version,
       cierre.cuenta_stats,
       cuenta.descripcion,
       cuenta.proceso,
       cuenta.planta,
       cuenta.area_planta,
       cuenta.fila,
       cuenta.unidad,
       cierre.cantidad,
       cuenta.factor,
       cierre.equs,
       cuenta.suma_eq,
       cierre.fecha_calculo,
       cierre.closed_by
FROM gold.resumen_equ AS cierre
JOIN reporting.cuentas AS cuenta
  ON cuenta.version = cierre.version AND cuenta.cuenta_stats = cierre.cuenta_stats
"""

# The rows a reclose replaced, under the reclose that replaced them.
EQUS_CERRADO_HISTORIAL = """
CREATE VIEW reporting.equs_cerrado_historial AS
SELECT historial.cierre_id,
       historial.mes,
       reporting.anio_fiscal(historial.mes) AS anio_fiscal,
       cuenta.cuenta_id,
       historial.version,
       historial.cuenta_stats,
       cuenta.proceso,
       historial.cantidad,
       cuenta.factor,
       historial.equs,
       cuenta.suma_eq,
       historial.fecha_calculo,
       historial.closed_by
FROM gold.resumen_equ_historial AS historial
JOIN reporting.cuentas AS cuenta
  ON cuenta.version = historial.version AND cuenta.cuenta_stats = historial.cuenta_stats
"""

# (statement, view, description), in dependency order.
VIEWS: tuple[tuple[str, str, str], ...] = (
    (CUENTAS, "cuentas", "Every driver (cuenta STATS) of every version, with its factor and place"),
    (ARTICULOS, "articulos", "Every item, with its product line and Campo Equs"),
    (EQUS_MENSUAL, "equs_mensual", "Column H of RESUMEN EQU by month and driver, 0 included"),
    (EQUS_BATT_MENSUAL, "equs_batt_mensual", "EQUS BATT and the EQ of each process, by month"),
    (EQUS_POLY_MENSUAL, "equs_poly_mensual", "EQUS POLY by month and plastic category"),
    (
        APORTE_CUENTA,
        "aporte_cuenta",
        "What each receipt and invoice line contributed to each driver",
    ),
    (RECIBOS_PRODUCCION, "recibos_produccion", "Every production receipt, with its conversions"),
    (
        CALIDAD_DATOS_MENSUAL,
        "calidad_datos_mensual",
        "Rows each data-quality check returns, by month",
    ),
    (CIERRES, "cierres", "Every monthly close and reclose"),
    (CIERRES_CALIDAD, "cierres_calidad", "The data-quality counts each close saw"),
    (EQUS_CERRADO, "equs_cerrado", "The official close of each closed month, by driver"),
    (
        EQUS_CERRADO_HISTORIAL,
        "equs_cerrado_historial",
        "The closed rows a reclose replaced",
    ),
)


def upgrade() -> None:
    """Create the reporting schema, its views, and the role that may read them."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {REPORTING}")
    op.execute(ANIO_FISCAL)
    for statement, view, description in VIEWS:
        op.execute(statement)
        op.execute(f"COMMENT ON VIEW {REPORTING}.{view} IS '{description}'")

    op.execute(CREATE_READER_ROLE)
    op.execute(f"GRANT USAGE ON SCHEMA {REPORTING} TO {READER_ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {REPORTING} TO {READER_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {REPORTING}.anio_fiscal(date) TO {READER_ROLE}")


def downgrade() -> None:
    """Drop the reporting schema, and the reader role unless another database uses it.

    A login created by ``energy reporting create-reader`` belongs to the cluster
    and stays: it simply has nothing left to read.
    """
    op.execute(f"DROP SCHEMA IF EXISTS {REPORTING} CASCADE")
    op.execute(DROP_READER_ROLE)
