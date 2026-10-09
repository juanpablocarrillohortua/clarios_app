"""The SQL statements of the equivalent-batteries Gold layer.

Layer: adapters

Responsibility:
    Holds every statement :mod:`energy.adapters.postgres_equs_gold` sends. The
    calculation itself is not here: it lives in the views and functions
    revision 0004 creates, and these statements only ask them questions, take
    the lock, refresh the two conversion tables and freeze a month.

    Each statement is fixed text. Values travel as bound parameters, never as
    text pasted into a statement.

Depends on:
    standard library only.
"""

from __future__ import annotations

from typing import Final

# The advisory lock refreshes and closes share, so two never run at once. Any
# number works, as long as nothing else in the database uses it.
LOCK_KEY: Final = 7_215_590_330

SET_LOCK_TIMEOUT: Final = "SELECT set_config('lock_timeout', :timeout, false)"
TAKE_LOCK: Final = "SELECT pg_advisory_lock(:key)"
RELEASE_LOCK: Final = "SELECT pg_advisory_unlock(:key)"

# The materialized views the Gold calculation reads, in dependency order.
# Nothing in Gold itself is materialized.
MATERIALIZED_VIEWS: Final = ("silver.td_form", "silver.td_deco")

REFRESH: Final = {
    ("silver.td_form", True): "REFRESH MATERIALIZED VIEW CONCURRENTLY silver.td_form",
    ("silver.td_form", False): "REFRESH MATERIALIZED VIEW silver.td_form",
    ("silver.td_deco", True): "REFRESH MATERIALIZED VIEW CONCURRENTLY silver.td_deco",
    ("silver.td_deco", False): "REFRESH MATERIALIZED VIEW silver.td_deco",
}

# The two conversion tables, the tables they are made from, and the tables the
# Gold views read. After a large load their statistics are stale, and the plan
# of every Gold view depends on them.
ANALYZE: Final = (
    "ANALYZE silver.td_form, silver.td_deco, silver.bom_estructura, silver.item_master, "
    "silver.element_deco, silver.reporte_qad, silver.unit_ship, silver.clasif_poly, "
    "silver.lineas_produccion"
)

# The threshold of data-quality check 6, for the current transaction only.
SET_VARIATION_THRESHOLD: Final = "SELECT set_config('equs.umbral_variacion', :umbral, true)"

VERSIONS: Final = "SELECT mes, version, nombre FROM gold.versiones_rango(:desde, :hasta)"

# Every driver of some versions, with where the report shows it. Drivers of one
# process that carry the same weight share it (positive strip and casting
# grids share one, so do the two plate drivers and the two decoration
# drivers), so the weight of a process is the sum of its distinct weights.
DRIVERS: Final = """
WITH peso AS (
    SELECT factor.version, factor.proceso, sum(DISTINCT factor.peso) AS peso_proceso
    FROM silver.factores_equ AS factor
    GROUP BY factor.version, factor.proceso
)
SELECT factor.version,
       version.nombre AS version_name,
       factor.cuenta_stats,
       factor.descripcion,
       factor.proceso,
       factor.unidad,
       factor.fuente,
       factor.estandar_por_bat,
       factor.multiplicador,
       factor.peso,
       factor.factor,
       factor.suma_eq,
       fila.fila,
       seccion.planta,
       seccion.area_planta,
       seccion.orden,
       peso.peso_proceso
FROM silver.factores_equ AS factor
JOIN silver.equs_version AS version ON version.version = factor.version
JOIN gold.secciones_reporte AS seccion ON seccion.proceso = factor.proceso
JOIN peso ON peso.version = factor.version AND peso.proceso = factor.proceso
LEFT JOIN gold.filas_resumen_equ AS fila ON fila.cuenta_stats = factor.cuenta_stats
WHERE factor.version = ANY(:versiones)
ORDER BY factor.version, seccion.orden, fila.fila NULLS LAST, factor.cuenta_stats
"""

AMOUNTS_LIVE: Final = (
    "SELECT mes, version, cuenta_stats, cantidad, equs "
    "FROM gold.resumen_equ_rango_mes(:desde, :hasta)"
)
AMOUNTS_CLOSED: Final = (
    "SELECT mes, version, cuenta_stats, cantidad, equs "
    "FROM gold.resumen_equ_cierre_mes(:desde, :hasta)"
)

POLY: Final = """
SELECT poly.mes, poly.cuenta, poly.categoria, poly.area_planta, poly.piezas,
       poly.piezas_con_equ, poly.factor_equ, poly.equs, fila.fila
FROM gold.inyeccion_rango_mes(:desde, :hasta) AS poly
LEFT JOIN gold.filas_resumen_equ AS fila ON fila.cuenta_stats = poly.cuenta
"""

QUALITY: Final = (
    "SELECT vista, descripcion, bloqueante, filas FROM gold.calidad_datos_rango(:desde, :hasta)"
)

CLOSED_MONTHS: Final = (
    "SELECT DISTINCT mes FROM gold.resumen_equ WHERE mes BETWEEN :desde AND :hasta ORDER BY mes"
)

CLOSED_PROCESSES: Final = """
SELECT factor.proceso, sum(cierre.equs) AS equs
FROM gold.resumen_equ AS cierre
JOIN silver.factores_equ AS factor
  ON factor.version = cierre.version AND factor.cuenta_stats = cierre.cuenta_stats
JOIN gold.secciones_reporte AS seccion ON seccion.proceso = factor.proceso
WHERE cierre.mes = :mes AND factor.suma_eq
GROUP BY factor.proceso, seccion.orden
ORDER BY seccion.orden
"""

PREVIOUS_CLOSED_MONTH: Final = "SELECT max(mes) FROM gold.resumen_equ WHERE mes < :mes"

LATEST_PRODUCTION_MONTH: Final = (
    "SELECT date_trunc('month', max(fecha_efectiva)::timestamp)::date "
    "FROM silver.reporte_qad WHERE tipo_transaccion = 'RCT-WO'"
)

# -- The close, in one transaction --------------------------------------------
# The rows of a closed month, locked so that two closes of it cannot interleave.
LOCK_CLOSED_MONTH: Final = "SELECT 1 FROM gold.resumen_equ WHERE mes = :mes FOR UPDATE"

COUNT_LIVE_ROWS: Final = "SELECT count(*) FROM gold.v_resumen_equ WHERE mes = :mes"

RECORD_CLOSE: Final = """
INSERT INTO gold.cierre_mes (mes, closed_by, recierre, advertencias_aceptadas, calidad_datos)
VALUES (:mes, :closed_by, :recierre, :advertencias_aceptadas, CAST(:calidad_datos AS jsonb))
RETURNING cierre_id
"""

KEEP_REPLACED_ROWS: Final = """
INSERT INTO gold.resumen_equ_historial
    (cierre_id, mes, version, cuenta_stats, cantidad, equs, fecha_calculo, closed_by)
SELECT :cierre_id, cierre.mes, cierre.version, cierre.cuenta_stats, cierre.cantidad,
       cierre.equs, cierre.fecha_calculo, cierre.closed_by
FROM gold.resumen_equ AS cierre
WHERE cierre.mes = :mes
"""

# Every row written by this close carries the transaction's now(), so a row of
# the month that does not is one the earlier close had and this one has not.
FREEZE: Final = """
INSERT INTO gold.resumen_equ (mes, version, cuenta_stats, cantidad, equs, fecha_calculo, closed_by)
SELECT vivo.mes, vivo.version, vivo.cuenta_stats, vivo.cantidad, vivo.equs, now(), :closed_by
FROM gold.v_resumen_equ AS vivo
WHERE vivo.mes = :mes
ON CONFLICT (mes, version, cuenta_stats) DO UPDATE
SET cantidad = EXCLUDED.cantidad,
    equs = EXCLUDED.equs,
    fecha_calculo = EXCLUDED.fecha_calculo,
    closed_by = EXCLUDED.closed_by
"""
DROP_LEFTOVER_ROWS: Final = (
    "DELETE FROM gold.resumen_equ WHERE mes = :mes AND fecha_calculo <> now()"
)

__all__ = [
    "AMOUNTS_CLOSED",
    "AMOUNTS_LIVE",
    "ANALYZE",
    "CLOSED_MONTHS",
    "CLOSED_PROCESSES",
    "COUNT_LIVE_ROWS",
    "DRIVERS",
    "DROP_LEFTOVER_ROWS",
    "FREEZE",
    "KEEP_REPLACED_ROWS",
    "LATEST_PRODUCTION_MONTH",
    "LOCK_CLOSED_MONTH",
    "LOCK_KEY",
    "MATERIALIZED_VIEWS",
    "POLY",
    "PREVIOUS_CLOSED_MONTH",
    "QUALITY",
    "RECORD_CLOSE",
    "REFRESH",
    "RELEASE_LOCK",
    "SET_LOCK_TIMEOUT",
    "SET_VARIATION_THRESHOLD",
    "TAKE_LOCK",
    "VERSIONS",
]
