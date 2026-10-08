"""Create the Gold schema: the energy KPI tables, and EQUS Gold.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18

Gold holds two families of objects. The first, described next, belongs to the
energy-metering platform. The second, described after it, is the Gold layer of
the equivalent-batteries (EQUS) calculation.

Gold is a published layer. It is rewritten per period by the KPI pipeline rather
than mutated row by row, so every row records the ``run_id`` that produced it
and the ``computed_at`` instant - a figure in a report can always be traced to
the run that computed it.

The column that makes this schema trustworthy is ``coverage``: the fraction of
each period actually backed by reliable Silver readings. It is ``NOT NULL`` with
a ``[0, 1]`` check on every table here. A KPI without its coverage is a number
without an error bar, and consumers will read it as certain.

EQUS Gold
=========

The equivalent batteries (EQ) of a driver are
``quantity x (multiplier / standard per battery) x weight``. Silver holds the
quantities, the two conversion tables and the equation as data
(``factores_equ``, ``instrucc``, ``equs_version``). Gold applies the equation.
Names are Spanish, as in Silver, because they mirror the Excel the business
recognises (``v_resumen_equ`` is column H of sheet RESUMEN EQU).

Four decisions are encoded here:

The equation is applied, never written
    The views filter production and invoice rows with the rules of
    ``silver.instrucc``, sum what ``silver.factores_equ`` says to sum and
    multiply by its factor. No factor, weight, Campo Equs value or product line
    appears in a view. The only literals are the transaction type RCT-WO, the
    channel Venta, warehouse 1010 for the plastic-injection indicator, the
    product-line range of data-quality check 1, and the four values of
    ``factores_equ.fuente``. A new rule is a new version in Silver; Gold needs
    no change.

One definition of everything that could diverge
    ``gold.en_vigor`` is the only definition of "the version in force in a
    month". ``v_reporte_qad_regla`` and ``v_unit_ship_regla`` pair each
    production row and each invoice line with the rules it matches; the
    monthly views and the date-range functions both sum those two views, so a
    month and a range covering that month cannot disagree.

Views, not tables
    Every calculation reads Silver live, so a new load is visible as soon as
    Silver is rebuilt. Nothing here is materialized: a whole fiscal year
    computes in a fraction of a second. The only materialized objects the
    calculation depends on are ``silver.td_form`` and ``silver.td_deco``.

The close is frozen
    ``resumen_equ`` holds the official number of each closed month, as the
    workbook's "ESTA SE COPIA" column did. Nothing recomputes it implicitly: it
    changes only through a reclose, which keeps the replaced values in
    ``resumen_equ_historial`` and records itself in ``cierre_mes``.

Seven questions about the rules are open with the business (EQUS_CONTEXT
section 10). Gold decides none of them: it applies whatever Silver holds.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCE = "reference"
GOLD = "gold"
SILVER = "silver"

GRANULARITIES = ("hour", "day", "week", "month")

# -----------------------------------------------------------------------------
# EQUS: the layout of sheet RESUMEN EQU, as data
# -----------------------------------------------------------------------------
# (proceso, planta, area_planta, orden)
#
# Column F of RESUMEN EQU groups the drivers under a plant label: OREC,
# ENSAMBLE, F&A (formation and decoration together, as rows 40, 44 and 45 do)
# and DESPACHOS. `area_planta` is the plant area of sheet Comparativo lineas
# (`silver.planta`); shipping is not one, so it has none. The informative
# drivers form their own section, and EQUS POLY (the plastic-injection
# indicator, which is not a process of `factores_equ`) is reported under the
# area Componentes.
SECCIONES: tuple[tuple[str, str, str | None, int], ...] = (
    ("OREC", "OREC", "OREC", 1),
    ("ENSAMBLE", "ENSAMBLE", "Ensamble", 2),
    ("FORMACION", "F&A", "F&A", 3),
    ("DECORADO", "F&A", "F&A", 4),
    ("SHIPPING", "DESPACHOS", None, 5),
    ("INFO", "INFORMATIVOS", None, 6),
    ("POLY", "EQUS POLY", "Componentes", 7),
)

# The plant areas the sections point at, in the order they first appear.
AREAS_PLANTA: tuple[str, ...] = tuple(
    dict.fromkeys(area for _, _, area, _ in SECCIONES if area is not None)
)

# The processes of `silver.factores_equ` (its check `ck_factores_equ_proceso`),
# plus POLY.
PROCESOS_SECCION: tuple[str, ...] = tuple(proceso for proceso, _, _, _ in SECCIONES)

# (cuenta_stats, row of RESUMEN EQU)
#
# The order in which the workbook lists the drivers (rows 2 to 18) and the
# plastic-injection accounts (rows 20 to 30). Row 17 is blank, row 19 a header.
FILAS_RESUMEN_EQU: tuple[tuple[str, int], ...] = (
    ("OXPROD_KG", 2),
    ("GRIDS_REG_POS", 3),
    ("GRIDS_CAST", 4),
    ("GRIDS_NEG", 5),
    ("PLATEPRD_SLI", 6),
    ("PLATEPRD_TRUCK", 7),
    ("COSPRODU_SLI", 8),
    ("COSPRODU_TRUCK", 9),
    ("POS_ACT_MTL", 10),
    ("15BATPRD", 11),
    ("FORM_PRD_SLI", 12),
    ("FORM_PRD_TRUCK", 13),
    ("COMP_DEC", 14),
    ("CARTON_BATT", 15),
    ("UNITSHIP", 16),
    ("LEADPROD", 18),
    ("CONTPROD_CAR", 20),
    ("CONTPROD_TRUCK", 21),
    ("CONTPROD_Grp31", 22),
    ("COVRPROD_SLI1", 23),
    ("COVRPROD_TRUCK", 24),
    ("ASSEM_COVER_AUTO", 25),
    ("HANDPROD", 26),
    ("TOPLIDPROD", 27),
    ("VENTPROD", 28),
    ("Small_Comp_Prod", 29),
    ("Med_Comp_Prod", 30),
)

# (view, description, blocking, order)
#
# The data-quality checks of EQUS_CONTEXT section 8. A blocking check with rows
# stops the monthly close unless the person closing accepts the warnings.
CONTROLES_CALIDAD: tuple[tuple[str, str, bool, int], ...] = (
    (
        "dq_sin_regla",
        "Production rows of product lines 2101 to 3104 that match no rule of the version in force",
        True,
        1,
    ),
    (
        "dq_doble_regla",
        "Production rows that match two or more rules of the same process",
        True,
        2,
    ),
    (
        "dq_sin_conversor",
        "Formed batteries produced without paste, or decorated batteries produced "
        "without decoration pieces",
        True,
        3,
    ),
    (
        "dq_categoria_poly",
        "Production rows whose Campo Equs differs from the Clasif_Poly category of the item",
        False,
        4,
    ),
    (
        "dq_linea_sin_planta",
        "Production receipts on production lines that have no plant area",
        False,
        5,
    ),
    (
        "dq_variacion",
        "Drivers whose monthly quantity moved more than the threshold against the previous month",
        False,
        6,
    ),
)

# -----------------------------------------------------------------------------
# EQUS: statements
# -----------------------------------------------------------------------------
# The schemas are written out in these statements rather than interpolated:
# they select, and a query assembled from strings is what the security linters
# are right to question.

# The version of the equation in force in a month: the one whose validity
# contains the first day of the month. Every object below asks this function,
# so the rule exists once. Plain SQL and immutable, so the planner inlines it.
EN_VIGOR = """
CREATE FUNCTION gold.en_vigor(p_desde date, p_hasta date, p_mes date)
RETURNS boolean
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT p_desde <= p_mes AND (p_hasta IS NULL OR p_hasta >= p_mes) $$
"""

# Sheet REPORTE QAD of the workbook: every production receipt with the columns
# the workbook calculates beside it (AZ, AY, BE, BG, BF). The Campo Equs is the
# one the receipt carried when it was exported; the item master's only fills a
# receipt that carried none, and "N/A" is a value, not a gap. The two
# conversions only exist for formed (td_form) and decorated (td_deco) batteries,
# so the other receipts get none. The plastic-injection columns belong to
# warehouse 1010 only.
V_REPORTE_QAD = """
CREATE VIEW gold.v_reporte_qad AS
SELECT recibo.*,
       date_trunc('month', recibo.fecha_efectiva::timestamp)::date AS mes,
       articulo.linea_de_productos,
       coalesce(recibo.campo_equs, articulo.campo_equs) AS campo_equs_eq,
       recibo.cambiar_cantidad_de_ubic * formada.pasta_pos AS mat_activo,
       recibo.cambiar_cantidad_de_ubic * decorada.comp_deco AS comp_deco,
       poly.campo_equs AS new_equ,
       poly.cuenta_stats AS cuenta,
       poly.factor_equ,
       recibo.cambiar_cantidad_de_ubic * poly.factor_equ AS valor_equ
FROM silver.reporte_qad AS recibo
JOIN silver.item_master AS articulo ON articulo.numero_articulo = recibo.numero_articulo
LEFT JOIN silver.td_form AS formada ON formada.articulo_matriz = recibo.numero_articulo
LEFT JOIN silver.td_deco AS decorada ON decorada.articulo_matriz = recibo.numero_articulo
LEFT JOIN silver.clasif_poly AS poly
       ON poly.numero_articulo = recibo.numero_articulo AND recibo.almac = '1010'
WHERE recibo.tipo_transaccion = 'RCT-WO'
"""

# Each receipt paired with every rule of `instrucc` it matches, of every
# version. Within a rule every condition that is not empty must hold; several
# rules of a driver add up. `valor` is what the driver sums: the quantity, or
# the quantity converted to kg of positive paste or to decoration pieces, as
# `fuente` says, with the rule's sign. `en_vigor` says whether the rule's
# version is the one in force in the receipt's month.
V_REPORTE_QAD_REGLA = """
CREATE VIEW gold.v_reporte_qad_regla AS
SELECT recibo.numero_de_transaccion,
       recibo.fecha_efectiva,
       recibo.mes,
       recibo.numero_articulo,
       recibo.almac,
       recibo.linea_produccion,
       recibo.linea_de_productos,
       recibo.campo_equs_eq,
       recibo.cambiar_cantidad_de_ubic AS cantidad,
       recibo.mat_activo,
       recibo.comp_deco,
       regla.version,
       regla.cuenta_stats,
       regla.regla_n,
       regla.signo,
       factor.proceso,
       factor.fuente,
       gold.en_vigor(version.vigente_desde, version.vigente_hasta, recibo.mes) AS en_vigor,
       regla.signo * CASE factor.fuente
           WHEN 'MAT_ACTIVO' THEN recibo.mat_activo
           WHEN 'COMP_DECO' THEN recibo.comp_deco
           ELSE recibo.cambiar_cantidad_de_ubic
       END AS valor
FROM gold.v_reporte_qad AS recibo
JOIN silver.instrucc AS regla
  ON (regla.linea_de_productos IS NULL OR regla.linea_de_productos = recibo.linea_de_productos)
 AND (regla.campo_equs IS NULL OR regla.campo_equs = recibo.campo_equs_eq)
 AND (regla.campo_equs_excluir IS NULL
      OR recibo.campo_equs_eq IS DISTINCT FROM regla.campo_equs_excluir)
 AND (regla.prefijo_linea IS NULL OR starts_with(recibo.linea_produccion, regla.prefijo_linea))
JOIN silver.factores_equ AS factor
  ON factor.version = regla.version AND factor.cuenta_stats = regla.cuenta_stats
JOIN silver.equs_version AS version ON version.version = regla.version
WHERE factor.fuente <> 'FACTURACION'
"""

# The same pairing for the invoice lines, which feed the drivers whose
# `fuente` is FACTURACION. Only sales count (channel Venta, in any case). The
# product line and Campo Equs come through the item master, as for receipts.
# An invoice has no production line, so a rule that asks for one never matches.
# Credit notes are negative lines and stay in: shipping is net, as in the
# workbook (open question 6 of EQUS_CONTEXT section 10).
V_UNIT_SHIP_REGLA = """
CREATE VIEW gold.v_unit_ship_regla AS
WITH factura AS (
    SELECT linea.*,
           date_trunc('month', linea.fecha_efectiva::timestamp)::date AS mes,
           articulo.linea_de_productos,
           coalesce(linea.campo_equs, articulo.campo_equs) AS campo_equs_eq
    FROM silver.unit_ship AS linea
    JOIN silver.item_master AS articulo ON articulo.numero_articulo = linea.numero_articulo
    WHERE lower(linea.canal) = lower('Venta')
)
SELECT factura.num_fact,
       factura.orden,
       factura.linea,
       factura.fecha_efectiva,
       factura.mes,
       factura.numero_articulo,
       factura.linea_de_productos,
       factura.campo_equs_eq,
       factura.cantidad_fac AS cantidad,
       regla.version,
       regla.cuenta_stats,
       regla.regla_n,
       regla.signo,
       factor.proceso,
       factor.fuente,
       gold.en_vigor(version.vigente_desde, version.vigente_hasta, factura.mes) AS en_vigor,
       regla.signo * factura.cantidad_fac AS valor
FROM factura
JOIN silver.instrucc AS regla
  ON (regla.linea_de_productos IS NULL OR regla.linea_de_productos = factura.linea_de_productos)
 AND (regla.campo_equs IS NULL OR regla.campo_equs = factura.campo_equs_eq)
 AND (regla.campo_equs_excluir IS NULL
      OR factura.campo_equs_eq IS DISTINCT FROM regla.campo_equs_excluir)
 AND regla.prefijo_linea IS NULL
JOIN silver.factores_equ AS factor
  ON factor.version = regla.version AND factor.cuenta_stats = regla.cuenta_stats
JOIN silver.equs_version AS version ON version.version = regla.version
WHERE factor.fuente = 'FACTURACION'
"""

# Pivot LINEAS DE PDCTO, by month: what each product line and Campo Equs
# produced. Useful to reconcile a driver with the workbook.
LINEAS_DE_PDCTO = """
CREATE VIEW gold.lineas_de_pdcto AS
SELECT recibo.mes,
       recibo.linea_de_productos,
       linea.descripcion,
       recibo.campo_equs_eq AS campo_equs,
       sum(recibo.cambiar_cantidad_de_ubic) AS cantidad,
       sum(recibo.mat_activo) AS mat_activo,
       sum(recibo.comp_deco) AS comp_deco
FROM gold.v_reporte_qad AS recibo
LEFT JOIN silver.linea_de_productos AS linea
       ON linea.linea_de_productos = recibo.linea_de_productos
GROUP BY recibo.mes, recibo.linea_de_productos, linea.descripcion, recibo.campo_equs_eq
"""

# Q_k: what each driver sums in each month, for every version. A driver with no
# rule, or whose rules matched nothing that month, has no row.
V_CANTIDAD_CUENTA = """
CREATE VIEW gold.v_cantidad_cuenta AS
SELECT pareja.version,
       pareja.cuenta_stats,
       pareja.mes,
       coalesce(sum(pareja.valor), 0) AS cantidad,
       pareja.en_vigor
FROM (
    SELECT version, cuenta_stats, mes, en_vigor, valor FROM gold.v_reporte_qad_regla
    UNION ALL
    SELECT version, cuenta_stats, mes, en_vigor, valor FROM gold.v_unit_ship_regla
) AS pareja
GROUP BY pareja.version, pareja.cuenta_stats, pareja.mes, pareja.en_vigor
"""

# Column H of RESUMEN EQU: the equivalent batteries of each driver in each
# month, with the version in force that month.
V_RESUMEN_EQU = """
CREATE VIEW gold.v_resumen_equ AS
SELECT cantidad.mes,
       cantidad.version,
       factor.proceso,
       cantidad.cuenta_stats,
       factor.descripcion,
       cantidad.cantidad,
       factor.factor,
       cantidad.cantidad * factor.factor AS equs,
       factor.suma_eq
FROM gold.v_cantidad_cuenta AS cantidad
JOIN silver.factores_equ AS factor
  ON factor.version = cantidad.version AND factor.cuenta_stats = cantidad.cuenta_stats
WHERE cantidad.en_vigor
"""

# Rows 36 to 45 of RESUMEN EQU: the equivalent batteries of each process and
# EQUS BATT, the sum of every driver that adds to the total.
V_EQUS_BATT_MES = """
CREATE VIEW gold.v_equs_batt_mes AS
SELECT resumen.mes,
       coalesce(sum(resumen.equs) FILTER (WHERE resumen.proceso = 'OREC'), 0) AS equs_orec,
       coalesce(sum(resumen.equs) FILTER (WHERE resumen.proceso = 'ENSAMBLE'), 0)
           AS equs_ensamble,
       coalesce(sum(resumen.equs) FILTER (WHERE resumen.proceso = 'FORMACION'), 0)
           AS equs_formacion,
       coalesce(sum(resumen.equs) FILTER (WHERE resumen.proceso = 'DECORADO'), 0)
           AS equs_decorado,
       coalesce(sum(resumen.equs) FILTER (WHERE resumen.proceso = 'SHIPPING'), 0)
           AS equs_shipping,
       coalesce(sum(resumen.equs), 0) AS equs_batt
FROM gold.v_resumen_equ AS resumen
WHERE resumen.suma_eq
GROUP BY resumen.mes
"""

# Pivot INYECCION: EQUS POLY, the plastic-injection indicator of warehouse
# 1010, by month and category. It does not add to EQUS BATT. An item Clasif_Poly
# does not list has no category and no EQ, and its pieces still count.
INYECCION = """
CREATE VIEW gold.inyeccion AS
SELECT recibo.mes,
       recibo.cuenta,
       recibo.new_equ,
       sum(recibo.cambiar_cantidad_de_ubic) AS piezas,
       coalesce(sum(recibo.valor_equ), 0) AS valor_equ
FROM gold.v_reporte_qad AS recibo
WHERE recibo.almac = '1010'
GROUP BY recibo.mes, recibo.cuenta, recibo.new_equ
"""

# Conversion cost per equivalent battery, by month and process. The cost is
# summed over the plant areas. Null when there is no cost or no EQ.
V_COSTO_POR_EQ = """
CREATE VIEW gold.v_costo_por_eq AS
WITH equivalentes AS (
    SELECT resumen.mes, resumen.proceso, sum(resumen.equs) AS equs
    FROM gold.v_resumen_equ AS resumen
    WHERE resumen.suma_eq
    GROUP BY resumen.mes, resumen.proceso
),
costo AS (
    SELECT conversion.mes, conversion.proceso, sum(conversion.costo_cop) AS costo_cop
    FROM silver.costo_conversion AS conversion
    GROUP BY conversion.mes, conversion.proceso
)
SELECT coalesce(equivalentes.mes, costo.mes) AS mes,
       coalesce(equivalentes.proceso, costo.proceso) AS proceso,
       equivalentes.equs,
       costo.costo_cop,
       costo.costo_cop / nullif(equivalentes.equs, 0) AS cop_por_eq
FROM equivalentes
FULL JOIN costo ON costo.mes = equivalentes.mes AND costo.proceso = equivalentes.proceso
"""

# Data-quality check 1: production of the process levels (product lines 2101
# to 3104) that no rule of the version in force takes, which is how a new or
# unclassified Campo Equs shows up. The range is the one literal the checks use.
# The receipts that some rule takes are listed once and joined, rather than
# looked up receipt by receipt with NOT EXISTS: the planner answers that one
# with a nested loop over the pairing view, which took a minute for a month.
DQ_SIN_REGLA = """
CREATE VIEW gold.dq_sin_regla AS
SELECT recibo.fecha_efectiva,
       recibo.mes,
       recibo.numero_de_transaccion,
       recibo.numero_articulo,
       articulo.descripcion,
       recibo.linea_de_productos,
       recibo.campo_equs_eq,
       recibo.linea_produccion,
       recibo.cambiar_cantidad_de_ubic AS cantidad
FROM gold.v_reporte_qad AS recibo
JOIN silver.item_master AS articulo ON articulo.numero_articulo = recibo.numero_articulo
LEFT JOIN (
    SELECT pareja.numero_de_transaccion
    FROM gold.v_reporte_qad_regla AS pareja
    WHERE pareja.en_vigor
    GROUP BY pareja.numero_de_transaccion
) AS emparejado ON emparejado.numero_de_transaccion = recibo.numero_de_transaccion
WHERE recibo.linea_de_productos ~ '^[0-9]{4}$'
  AND recibo.linea_de_productos BETWEEN '2101' AND '3104'
  AND emparejado.numero_de_transaccion IS NULL
"""

# Data-quality check 2: a receipt taken by two rules of one process is counted
# twice in that process.
DQ_DOBLE_REGLA = """
CREATE VIEW gold.dq_doble_regla AS
SELECT pareja.fecha_efectiva,
       pareja.mes,
       pareja.numero_de_transaccion,
       pareja.numero_articulo,
       pareja.linea_de_productos,
       pareja.campo_equs_eq,
       pareja.linea_produccion,
       pareja.cantidad,
       pareja.version,
       pareja.proceso,
       string_agg(pareja.cuenta_stats || ' #' || pareja.regla_n, ', '
                  ORDER BY pareja.cuenta_stats, pareja.regla_n) AS reglas
FROM gold.v_reporte_qad_regla AS pareja
WHERE pareja.en_vigor
GROUP BY pareja.fecha_efectiva, pareja.mes, pareja.numero_de_transaccion,
         pareja.numero_articulo, pareja.linea_de_productos, pareja.campo_equs_eq,
         pareja.linea_produccion, pareja.cantidad, pareja.version, pareja.proceso
HAVING count(*) > 1
"""

# Data-quality check 3: a receipt of a driver that sums kg of paste or
# decoration pieces, whose item has none. It would add nothing to its driver.
DQ_SIN_CONVERSOR = """
CREATE VIEW gold.dq_sin_conversor AS
SELECT pareja.fecha_efectiva,
       pareja.mes,
       pareja.numero_de_transaccion,
       pareja.numero_articulo,
       articulo.descripcion,
       pareja.linea_de_productos,
       pareja.cantidad,
       pareja.version,
       pareja.cuenta_stats,
       pareja.fuente
FROM gold.v_reporte_qad_regla AS pareja
JOIN silver.item_master AS articulo ON articulo.numero_articulo = pareja.numero_articulo
WHERE pareja.en_vigor
  AND (   (pareja.fuente = 'MAT_ACTIVO' AND coalesce(pareja.mat_activo, 0) = 0)
       OR (pareja.fuente = 'COMP_DECO' AND coalesce(pareja.comp_deco, 0) = 0))
"""

# Data-quality check 4: receipts whose Campo Equs is not the category
# Clasif_Poly gives their item, for the items whose category carries EQ. In
# the FY26 data these are the covers and handles QAD marks "N/A" (open
# question 7 of EQUS_CONTEXT section 10). No category is written here: the two
# columns are compared.
DQ_CATEGORIA_POLY = """
CREATE VIEW gold.dq_categoria_poly AS
SELECT recibo.fecha_efectiva,
       recibo.mes,
       recibo.numero_de_transaccion,
       recibo.numero_articulo,
       articulo.descripcion,
       recibo.almac,
       recibo.linea_de_productos,
       recibo.campo_equs_eq AS campo_equs_qad,
       poly.campo_equs AS categoria_poly,
       poly.cuenta_stats AS cuenta,
       poly.factor_equ,
       recibo.cambiar_cantidad_de_ubic AS cantidad
FROM gold.v_reporte_qad AS recibo
JOIN silver.clasif_poly AS poly ON poly.numero_articulo = recibo.numero_articulo
JOIN silver.item_master AS articulo ON articulo.numero_articulo = recibo.numero_articulo
WHERE poly.factor_equ > 0
  AND recibo.campo_equs_eq IS DISTINCT FROM poly.campo_equs
"""

# Data-quality check 5: receipts reported by a production line that no plant
# area owns (open question 4 of EQUS_CONTEXT section 10). Receipts QAD records
# without a line are not counted here.
DQ_LINEA_SIN_PLANTA = """
CREATE VIEW gold.dq_linea_sin_planta AS
SELECT recibo.fecha_efectiva,
       recibo.mes,
       recibo.numero_de_transaccion,
       recibo.linea_produccion,
       linea.descripcion AS descripcion_linea,
       linea.almac AS almac_linea,
       recibo.numero_articulo,
       recibo.cambiar_cantidad_de_ubic AS cantidad
FROM gold.v_reporte_qad AS recibo
JOIN silver.lineas_produccion AS linea ON linea.linea = recibo.linea_produccion
WHERE linea.planta IS NULL
"""

# Data-quality check 6: a driver whose quantity moved more than the threshold
# against the month before, both months having production. A driver that
# starts or stops counts as moving. The threshold is the session setting
# `equs.umbral_variacion` (a fraction), 0.30 when it is not set; the command
# line sets it from EQUS_VARIATION_THRESHOLD.
DQ_VARIACION = """
CREATE VIEW gold.dq_variacion AS
WITH umbral AS (
    SELECT coalesce(nullif(current_setting('equs.umbral_variacion', true), '')::numeric, 0.30)
           AS valor
),
serie AS (
    SELECT resumen.mes, resumen.cuenta_stats, min(resumen.proceso) AS proceso,
           sum(resumen.cantidad) AS cantidad, sum(resumen.equs) AS equs
    FROM gold.v_resumen_equ AS resumen
    GROUP BY resumen.mes, resumen.cuenta_stats
),
conductor AS (
    SELECT DISTINCT ON (serie.cuenta_stats) serie.cuenta_stats, serie.proceso
    FROM serie
    ORDER BY serie.cuenta_stats, serie.mes DESC
),
meses AS (
    SELECT DISTINCT serie.mes FROM serie
),
pares AS (
    SELECT actual.mes, anterior.mes AS mes_anterior
    FROM meses AS actual
    JOIN meses AS anterior
      ON anterior.mes = (actual.mes::timestamp - interval '1 month')::date
),
comparacion AS (
    SELECT pares.mes,
           pares.mes_anterior,
           conductor.cuenta_stats,
           conductor.proceso,
           coalesce(actual.cantidad, 0) AS cantidad,
           coalesce(anterior.cantidad, 0) AS cantidad_anterior,
           coalesce(actual.equs, 0) AS equs,
           coalesce(anterior.equs, 0) AS equs_anterior
    FROM pares
    CROSS JOIN conductor
    LEFT JOIN serie AS actual
           ON actual.mes = pares.mes AND actual.cuenta_stats = conductor.cuenta_stats
    LEFT JOIN serie AS anterior
           ON anterior.mes = pares.mes_anterior AND anterior.cuenta_stats = conductor.cuenta_stats
)
SELECT comparacion.mes,
       comparacion.mes_anterior,
       comparacion.cuenta_stats,
       comparacion.proceso,
       comparacion.cantidad,
       comparacion.cantidad_anterior,
       (comparacion.cantidad - comparacion.cantidad_anterior)
           / nullif(abs(comparacion.cantidad_anterior), 0) AS variacion,
       comparacion.equs,
       comparacion.equs_anterior,
       umbral.valor AS umbral
FROM comparacion
CROSS JOIN umbral
WHERE (comparacion.cantidad_anterior = 0 AND comparacion.cantidad <> 0)
   OR (comparacion.cantidad_anterior <> 0
       AND abs(comparacion.cantidad - comparacion.cantidad_anterior)
           > umbral.valor * abs(comparacion.cantidad_anterior))
"""

# For each month a date range touches, the version in force; null where no
# version covers the month, and two rows where two overlap.
VERSIONES_RANGO = """
CREATE FUNCTION gold.versiones_rango(p_desde date, p_hasta date)
RETURNS TABLE (mes date, version integer, nombre text)
LANGUAGE sql STABLE
AS $$
    SELECT serie.mes::date, vigente.version, vigente.nombre
    FROM generate_series(date_trunc('month', p_desde::timestamp),
                         date_trunc('month', p_hasta::timestamp),
                         interval '1 month') AS serie (mes)
    LEFT JOIN silver.equs_version AS vigente
           ON gold.en_vigor(vigente.vigente_desde, vigente.vigente_hasta, serie.mes::date)
    ORDER BY 1, 2
$$
"""

# The equivalent batteries of any date range, both ends included, by month.
# The same two pairing views as v_cantidad_cuenta, filtered by the date of each
# row instead of grouped by its month, so a whole month gives exactly what
# v_resumen_equ gives. Each row uses the version in force in its own month.
# Every driver of that version is returned, with 0 when nothing matched.
RESUMEN_EQU_RANGO_MES = """
CREATE FUNCTION gold.resumen_equ_rango_mes(p_desde date, p_hasta date)
RETURNS TABLE (mes date, version integer, cuenta_stats text, cantidad numeric, equs numeric)
LANGUAGE sql STABLE
AS $$
    WITH vigente AS (
        SELECT rango.mes, rango.version
        FROM gold.versiones_rango(p_desde, p_hasta) AS rango
        WHERE rango.version IS NOT NULL
    ),
    pareja AS (
        SELECT produccion.mes, produccion.version, produccion.cuenta_stats, produccion.valor
        FROM gold.v_reporte_qad_regla AS produccion
        WHERE produccion.en_vigor AND produccion.fecha_efectiva BETWEEN p_desde AND p_hasta
        UNION ALL
        SELECT factura.mes, factura.version, factura.cuenta_stats, factura.valor
        FROM gold.v_unit_ship_regla AS factura
        WHERE factura.en_vigor AND factura.fecha_efectiva BETWEEN p_desde AND p_hasta
    ),
    suma AS (
        SELECT pareja.mes, pareja.version, pareja.cuenta_stats,
               coalesce(sum(pareja.valor), 0) AS cantidad
        FROM pareja
        GROUP BY pareja.mes, pareja.version, pareja.cuenta_stats
    )
    SELECT vigente.mes,
           factor.version,
           factor.cuenta_stats,
           coalesce(suma.cantidad, 0),
           coalesce(suma.cantidad, 0) * factor.factor
    FROM vigente
    JOIN silver.factores_equ AS factor ON factor.version = vigente.version
    LEFT JOIN suma
           ON suma.mes = vigente.mes
          AND suma.version = factor.version
          AND suma.cuenta_stats = factor.cuenta_stats
    ORDER BY 1, 2, 3
$$
"""

# The same for the whole range: one row per version and driver.
RESUMEN_EQU_RANGO = """
CREATE FUNCTION gold.resumen_equ_rango(p_desde date, p_hasta date)
RETURNS TABLE (version integer, cuenta_stats text, cantidad numeric, equs numeric)
LANGUAGE sql STABLE
AS $$
    SELECT por_mes.version, por_mes.cuenta_stats, sum(por_mes.cantidad), sum(por_mes.equs)
    FROM gold.resumen_equ_rango_mes(p_desde, p_hasta) AS por_mes
    GROUP BY por_mes.version, por_mes.cuenta_stats
    ORDER BY 1, 2
$$
"""

# The frozen close of the closed months a range touches, in the shape of
# resumen_equ_rango_mes: every driver of the version the month was closed
# with, 0 where the close holds no row. The caller checks that the range covers
# whole, closed months.
RESUMEN_EQU_CIERRE_MES = """
CREATE FUNCTION gold.resumen_equ_cierre_mes(p_desde date, p_hasta date)
RETURNS TABLE (mes date, version integer, cuenta_stats text, cantidad numeric, equs numeric)
LANGUAGE sql STABLE
AS $$
    WITH cerrado AS (
        SELECT DISTINCT cierre.mes, cierre.version
        FROM gold.resumen_equ AS cierre
        WHERE cierre.mes BETWEEN date_trunc('month', p_desde::timestamp)::date AND p_hasta
    )
    SELECT cerrado.mes,
           factor.version,
           factor.cuenta_stats,
           coalesce(cierre.cantidad, 0),
           coalesce(cierre.equs, 0)
    FROM cerrado
    JOIN silver.factores_equ AS factor ON factor.version = cerrado.version
    LEFT JOIN gold.resumen_equ AS cierre
           ON cierre.mes = cerrado.mes
          AND cierre.version = factor.version
          AND cierre.cuenta_stats = factor.cuenta_stats
    ORDER BY 1, 2, 3
$$
"""

# EQUS POLY for any date range, by month and category: the pieces, the pieces
# of categories that carry EQ (the workbook's D32 leaves the others out), the
# factor when the whole category shares one, and the EQ. The plant area comes
# from the layout table.
INYECCION_RANGO_MES = """
CREATE FUNCTION gold.inyeccion_rango_mes(p_desde date, p_hasta date)
RETURNS TABLE (mes date, cuenta text, categoria text, area_planta text, piezas numeric,
               piezas_con_equ numeric, factor_equ numeric, equs numeric)
LANGUAGE sql STABLE
AS $$
    SELECT recibo.mes,
           recibo.cuenta,
           recibo.new_equ,
           (SELECT seccion.area_planta FROM gold.secciones_reporte AS seccion
            WHERE seccion.proceso = 'POLY'),
           sum(recibo.cambiar_cantidad_de_ubic),
           coalesce(sum(recibo.cambiar_cantidad_de_ubic) FILTER (WHERE recibo.factor_equ > 0), 0),
           CASE WHEN min(recibo.factor_equ) = max(recibo.factor_equ)
                THEN min(recibo.factor_equ) END,
           coalesce(sum(recibo.valor_equ), 0)
    FROM gold.v_reporte_qad AS recibo
    WHERE recibo.almac = '1010' AND recibo.fecha_efectiva BETWEEN p_desde AND p_hasta
    GROUP BY recibo.mes, recibo.cuenta, recibo.new_equ
    ORDER BY 1, 2, 3
$$
"""

INYECCION_RANGO = """
CREATE FUNCTION gold.inyeccion_rango(p_desde date, p_hasta date)
RETURNS TABLE (cuenta text, categoria text, area_planta text, piezas numeric,
               piezas_con_equ numeric, factor_equ numeric, equs numeric)
LANGUAGE sql STABLE
AS $$
    SELECT por_mes.cuenta,
           por_mes.categoria,
           min(por_mes.area_planta),
           sum(por_mes.piezas),
           sum(por_mes.piezas_con_equ),
           CASE WHEN count(DISTINCT por_mes.factor_equ) = 1
                 AND count(por_mes.factor_equ) = count(*)
                THEN min(por_mes.factor_equ) END,
           sum(por_mes.equs)
    FROM gold.inyeccion_rango_mes(p_desde, p_hasta) AS por_mes
    GROUP BY por_mes.cuenta, por_mes.categoria
    ORDER BY 1, 2
$$
"""

# How many rows each data-quality check returns for a date range, both ends
# included: by the date of the receipt, or by month for the monthly check 6.
CALIDAD_DATOS_RANGO = """
CREATE FUNCTION gold.calidad_datos_rango(p_desde date, p_hasta date)
RETURNS TABLE (vista text, descripcion text, bloqueante boolean, filas bigint)
LANGUAGE sql STABLE
AS $$
    WITH conteo (vista, filas) AS (
        SELECT 'dq_sin_regla', count(*) FROM gold.dq_sin_regla AS fila
        WHERE fila.fecha_efectiva BETWEEN p_desde AND p_hasta
        UNION ALL
        SELECT 'dq_doble_regla', count(*) FROM gold.dq_doble_regla AS fila
        WHERE fila.fecha_efectiva BETWEEN p_desde AND p_hasta
        UNION ALL
        SELECT 'dq_sin_conversor', count(*) FROM gold.dq_sin_conversor AS fila
        WHERE fila.fecha_efectiva BETWEEN p_desde AND p_hasta
        UNION ALL
        SELECT 'dq_categoria_poly', count(*) FROM gold.dq_categoria_poly AS fila
        WHERE fila.fecha_efectiva BETWEEN p_desde AND p_hasta
        UNION ALL
        SELECT 'dq_linea_sin_planta', count(*) FROM gold.dq_linea_sin_planta AS fila
        WHERE fila.fecha_efectiva BETWEEN p_desde AND p_hasta
        UNION ALL
        SELECT 'dq_variacion', count(*) FROM gold.dq_variacion AS fila
        WHERE fila.mes BETWEEN date_trunc('month', p_desde::timestamp)::date AND p_hasta
    )
    SELECT control.vista, control.descripcion, control.bloqueante, coalesce(conteo.filas, 0)
    FROM gold.controles_calidad AS control
    LEFT JOIN conteo ON conteo.vista = control.vista
    ORDER BY control.orden
$$
"""

# The plant areas the sections point at, inserted as the Silver build inserts
# them, and on downgrade removed only while nothing in Silver points at them
# and their name is still the one inserted.
INSERT_AREAS_PLANTA = """
INSERT INTO silver.planta (planta, nombre)
SELECT area, area FROM unnest(CAST(:areas AS text[])) AS area
ON CONFLICT (planta) DO NOTHING
"""
DELETE_AREAS_PLANTA = """
DELETE FROM silver.planta AS area
WHERE area.planta = ANY(CAST(:areas AS text[]))
  AND area.nombre = area.planta
  AND NOT EXISTS (SELECT 1 FROM silver.lineas_produccion AS linea WHERE linea.planta = area.planta)
  AND NOT EXISTS (SELECT 1 FROM silver.costo_conversion AS costo WHERE costo.planta = area.planta)
"""

# In dependency order.
EQUS_VIEWS: tuple[str, ...] = (
    V_REPORTE_QAD,
    V_REPORTE_QAD_REGLA,
    V_UNIT_SHIP_REGLA,
    LINEAS_DE_PDCTO,
    V_CANTIDAD_CUENTA,
    V_RESUMEN_EQU,
    V_EQUS_BATT_MES,
    INYECCION,
    V_COSTO_POR_EQ,
    DQ_SIN_REGLA,
    DQ_DOBLE_REGLA,
    DQ_SIN_CONVERSOR,
    DQ_CATEGORIA_POLY,
    DQ_LINEA_SIN_PLANTA,
    DQ_VARIACION,
)
EQUS_FUNCTIONS: tuple[str, ...] = (
    VERSIONES_RANGO,
    RESUMEN_EQU_RANGO_MES,
    RESUMEN_EQU_RANGO,
    RESUMEN_EQU_CIERRE_MES,
    INYECCION_RANGO_MES,
    INYECCION_RANGO,
    CALIDAD_DATOS_RANGO,
)


def _sql_list(values: Sequence[str]) -> str:
    """Render values as the body of a SQL ``IN (...)`` list."""
    return ", ".join(f"'{value}'" for value in values)


def _text(name: str, *, nullable: bool = True) -> sa.Column[object]:
    """Return a text column."""
    return sa.Column(name, sa.Text(), nullable=nullable)


def _numeric(name: str, *, nullable: bool = True) -> sa.Column[object]:
    """Return an exact decimal column of unconstrained precision."""
    return sa.Column(name, sa.Numeric(), nullable=nullable)


def _first_day(table: str) -> sa.CheckConstraint:
    """Return the check that a table's ``mes`` is the first day of a month."""
    return sa.CheckConstraint("extract(day FROM mes) = 1", name=f"ck_{table}_primer_dia")


def _closed_rows(table: str) -> list[sa.Column[object] | sa.ForeignKeyConstraint]:
    """Return the columns of a frozen close row and its key into the equation."""
    return [
        sa.Column("mes", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        _text("cuenta_stats", nullable=False),
        _numeric("cantidad", nullable=False),
        _numeric("equs", nullable=False),
        sa.Column(
            "fecha_calculo",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        _text("closed_by", nullable=False),
        # A closed number always names the factor it was computed with, which
        # also stops a version from being deleted once a month used it.
        sa.ForeignKeyConstraint(
            ["version", "cuenta_stats"],
            [f"{SILVER}.factores_equ.version", f"{SILVER}.factores_equ.cuenta_stats"],
            name=f"fk_{table}_factor",
        ),
    ]


def _create_equs_tables() -> None:
    """Create the layout tables and the tables of the frozen close."""
    # The sections point at plant areas, which the Silver build creates from
    # sheet Comparativo lineas. A database migrated but never built has none
    # yet, so the four the sections need are inserted here, exactly as the
    # build would insert them; the build's own insert then does nothing.
    op.get_bind().execute(sa.text(INSERT_AREAS_PLANTA), {"areas": list(AREAS_PLANTA)})

    op.create_table(
        "secciones_reporte",
        _text("proceso", nullable=False),
        # The label of column F of RESUMEN EQU.
        _text("planta", nullable=False),
        _text("area_planta"),
        sa.Column("orden", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("proceso", name="pk_secciones_reporte"),
        sa.UniqueConstraint("orden", name="uq_secciones_reporte_orden"),
        sa.ForeignKeyConstraint(
            ["area_planta"], [f"{SILVER}.planta.planta"], name="fk_secciones_reporte_area_planta"
        ),
        # Not a foreign key: no table has the process as its key. The list is
        # that of silver.factores_equ, plus POLY.
        sa.CheckConstraint(
            f"proceso IN ({_sql_list(PROCESOS_SECCION)})", name="ck_secciones_reporte_proceso"
        ),
        schema=GOLD,
    )

    op.create_table(
        "filas_resumen_equ",
        _text("cuenta_stats", nullable=False),
        sa.Column("fila", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("cuenta_stats", name="pk_filas_resumen_equ"),
        sa.UniqueConstraint("fila", name="uq_filas_resumen_equ_fila"),
        schema=GOLD,
    )

    op.create_table(
        "controles_calidad",
        _text("vista", nullable=False),
        _text("descripcion", nullable=False),
        sa.Column("bloqueante", sa.Boolean(), nullable=False),
        sa.Column("orden", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("vista", name="pk_controles_calidad"),
        sa.UniqueConstraint("orden", name="uq_controles_calidad_orden"),
        schema=GOLD,
    )

    # The official close: one row per closed month and driver.
    op.create_table(
        "resumen_equ",
        *_closed_rows("resumen_equ"),
        sa.PrimaryKeyConstraint("mes", "version", "cuenta_stats", name="pk_resumen_equ"),
        _first_day("resumen_equ"),
        schema=GOLD,
    )

    # Every close and reclose, with the data-quality counts it saw and whether
    # the person closing accepted blocking warnings.
    op.create_table(
        "cierre_mes",
        sa.Column("cierre_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("mes", sa.Date(), nullable=False),
        sa.Column(
            "cerrado_en",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        _text("closed_by", nullable=False),
        sa.Column("recierre", sa.Boolean(), nullable=False),
        sa.Column("advertencias_aceptadas", sa.Boolean(), nullable=False),
        sa.Column("calidad_datos", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("cierre_id", name="pk_cierre_mes"),
        _first_day("cierre_mes"),
        schema=GOLD,
    )
    op.create_index("ix_cierre_mes_mes", "cierre_mes", ["mes"], schema=GOLD)

    # The rows a reclose replaced, kept as they were, under the reclose that
    # replaced them.
    op.create_table(
        "resumen_equ_historial",
        sa.Column("cierre_id", sa.BigInteger(), nullable=False),
        *_closed_rows("resumen_equ_historial"),
        sa.PrimaryKeyConstraint(
            "cierre_id", "version", "cuenta_stats", name="pk_resumen_equ_historial"
        ),
        sa.ForeignKeyConstraint(
            ["cierre_id"], [f"{GOLD}.cierre_mes.cierre_id"], name="fk_resumen_equ_historial_cierre"
        ),
        _first_day("resumen_equ_historial"),
        schema=GOLD,
    )
    op.create_index("ix_resumen_equ_historial_mes", "resumen_equ_historial", ["mes"], schema=GOLD)


def _seed_equs_layout() -> None:
    """Insert the layout of RESUMEN EQU and the list of data-quality checks."""
    op.bulk_insert(
        sa.table(
            "secciones_reporte",
            sa.column("proceso", sa.Text()),
            sa.column("planta", sa.Text()),
            sa.column("area_planta", sa.Text()),
            sa.column("orden", sa.SmallInteger()),
            schema=GOLD,
        ),
        [
            {"proceso": proceso, "planta": planta, "area_planta": area, "orden": orden}
            for proceso, planta, area, orden in SECCIONES
        ],
    )
    op.bulk_insert(
        sa.table(
            "filas_resumen_equ",
            sa.column("cuenta_stats", sa.Text()),
            sa.column("fila", sa.SmallInteger()),
            schema=GOLD,
        ),
        [{"cuenta_stats": cuenta, "fila": fila} for cuenta, fila in FILAS_RESUMEN_EQU],
    )
    op.bulk_insert(
        sa.table(
            "controles_calidad",
            sa.column("vista", sa.Text()),
            sa.column("descripcion", sa.Text()),
            sa.column("bloqueante", sa.Boolean()),
            sa.column("orden", sa.SmallInteger()),
            schema=GOLD,
        ),
        [
            {"vista": vista, "descripcion": descripcion, "bloqueante": bloqueante, "orden": orden}
            for vista, descripcion, bloqueante, orden in CONTROLES_CALIDAD
        ],
    )


def _create_equs_gold() -> None:
    """Create every EQUS Gold object, in dependency order."""
    _create_equs_tables()
    _seed_equs_layout()
    op.execute(EN_VIGOR)
    for statement in EQUS_VIEWS:
        op.execute(statement)
    for statement in EQUS_FUNCTIONS:
        op.execute(statement)


def upgrade() -> None:
    """Create the Gold schema and its tables."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {GOLD}")

    granularity_list = ", ".join(f"'{g}'" for g in GRANULARITIES)

    # ------------------------------------------------------------------
    # gold.kpi_hourly - hypertable in revision 0005
    # ------------------------------------------------------------------
    op.create_table(
        "kpi_hourly",
        sa.Column(
            "kpi_hourly_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kpi_key", sa.String(64), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("coverage", sa.Double(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("kpi_hourly_id", "period_start", name="pk_kpi_hourly"),
        sa.CheckConstraint("coverage >= 0 AND coverage <= 1", name="ck_kpi_hourly_coverage"),
        # Infinity means a KPI was computed with a zero denominator and should
        # have been skipped instead of stored.
        sa.CheckConstraint(
            "value = value AND value <> 'Infinity'::double precision "
            "AND value <> '-Infinity'::double precision",
            name="ck_kpi_hourly_value_finite",
        ),
        sa.CheckConstraint(
            "date_trunc('hour', period_start) = period_start", name="ck_kpi_hourly_aligned"
        ),
        schema=GOLD,
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_kpi_hourly_scope
            ON {GOLD}.kpi_hourly (plant_id, kpi_key, period_start)
        """
    )

    # ------------------------------------------------------------------
    # gold.kpi_daily
    # ------------------------------------------------------------------
    op.create_table(
        "kpi_daily",
        sa.Column(
            "kpi_daily_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kpi_key", sa.String(64), nullable=False),
        sa.Column("granularity", sa.String(10), nullable=False, server_default="day"),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("coverage", sa.Double(), nullable=False),
        # Denormalised so a variance can be rendered without a temporal join
        # back to reference.targets.
        sa.Column("target_value", sa.Double(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("kpi_daily_id", "period_start", name="pk_kpi_daily"),
        sa.CheckConstraint("coverage >= 0 AND coverage <= 1", name="ck_kpi_daily_coverage"),
        sa.CheckConstraint(f"granularity IN ({granularity_list})", name="ck_kpi_daily_granularity"),
        sa.CheckConstraint(
            "value = value AND value <> 'Infinity'::double precision "
            "AND value <> '-Infinity'::double precision",
            name="ck_kpi_daily_value_finite",
        ),
        schema=GOLD,
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_kpi_daily_scope
            ON {GOLD}.kpi_daily (plant_id, kpi_key, granularity, period_start)
        """
    )

    # ------------------------------------------------------------------
    # gold.plant_daily_summary - the wide table the landing page reads
    # ------------------------------------------------------------------
    op.create_table(
        "plant_daily_summary",
        sa.Column(
            "summary_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("day", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("energy_kwh", sa.Double(), nullable=False, server_default=sa.text("0")),
        sa.Column("gas_kwh", sa.Double(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "production_equivalent_units",
            sa.Double(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Null rather than zero when production was zero: an idle plant has no
        # efficiency, which is a different statement from an efficiency of zero.
        sa.Column("sec_kwh_per_unit", sa.Double(), nullable=True),
        sa.Column("load_factor", sa.Double(), nullable=True),
        sa.Column("peak_demand_kw", sa.Double(), nullable=True),
        sa.Column("baseline_kwh", sa.Double(), nullable=True),
        sa.Column("deviation_pct", sa.Double(), nullable=True),
        sa.Column("coverage", sa.Double(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("summary_id", "day", name="pk_plant_daily_summary"),
        sa.CheckConstraint("coverage >= 0 AND coverage <= 1", name="ck_summary_coverage"),
        sa.CheckConstraint("energy_kwh >= 0", name="ck_summary_energy_non_negative"),
        sa.CheckConstraint("gas_kwh >= 0", name="ck_summary_gas_non_negative"),
        sa.CheckConstraint(
            "load_factor IS NULL OR (load_factor > 0 AND load_factor <= 1)",
            name="ck_summary_load_factor",
        ),
        sa.CheckConstraint("date_trunc('day', day) = day", name="ck_summary_day_aligned"),
        schema=GOLD,
    )
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_plant_daily_summary_scope
            ON {GOLD}.plant_daily_summary (plant_id, day)
        """
    )

    # ------------------------------------------------------------------
    # Foreign keys into reference are added after the tables exist so that
    # revision 0005 can convert these into hypertables without fighting a
    # dependency ordering problem.
    # ------------------------------------------------------------------
    for table, column in (
        ("kpi_hourly", "plant_id"),
        ("kpi_daily", "plant_id"),
        ("plant_daily_summary", "plant_id"),
    ):
        op.create_foreign_key(
            f"fk_{table}_plant",
            table,
            "plants",
            [column],
            ["plant_id"],
            source_schema=GOLD,
            referent_schema=REFERENCE,
            ondelete="CASCADE",
        )

    # ------------------------------------------------------------------
    # EQUS Gold
    # ------------------------------------------------------------------
    _create_equs_gold()


def downgrade() -> None:
    """Drop the Gold schema, and the plant areas only it needed.

    Every EQUS view is derived and is created again by ``upgrade()``. The tables
    of the close are not: a downgrade loses every closed month, so export
    ``gold.resumen_equ``, ``gold.cierre_mes`` and ``gold.resumen_equ_historial``
    first if they hold an official close.

    The plant areas ``upgrade()`` inserted into ``silver.planta`` are removed
    when nothing in Silver points at them and their name is still the one
    inserted. Removing one the Silver build had also created loses nothing: the
    next build inserts it again.
    """
    op.execute(f"DROP SCHEMA IF EXISTS {GOLD} CASCADE")
    op.get_bind().execute(sa.text(DELETE_AREAS_PLANTA), {"areas": list(AREAS_PLANTA)})
