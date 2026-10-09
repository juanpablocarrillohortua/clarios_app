"""Create the reference and Silver schemas: dimensions, readings, and EQUS Silver.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-18

Silver holds two families of tables. The first, described next, belongs to the
energy-metering platform. The second, described after it, is the Silver layer
of the equivalent-batteries (EQUS) calculation: the typed, deduplicated version
of what revision 0002 lands in Bronze.

This revision provisions two schemas rather than one, and the reason matters:

``reference``
    The shared dimension tables (plants, meters, shifts, targets). Silver and
    Gold both carry foreign keys into them, so they must exist before either
    layer does. Silver is the first to need them, so they are created here
    rather than in a revision of their own, which would have meant renumbering
    the medallion migrations. Bronze has no use for them: it stores what arrived
    and resolves nothing.

``silver``
    Readings that are validated, deduplicated and aligned to the canonical
    hourly UTC grid.

Three design decisions are encoded in the Silver DDL:

Supersede, never delete
    A corrected reading does not overwrite its predecessor; it marks the old row
    ``superseded_at`` and inserts a new one. Uniqueness on ``(meter_id, ts_utc)``
    is therefore enforced by a *partial* unique index covering only live rows,
    which is what lets history and correction coexist in one table.

Quarantine is a peer, not a footnote
    ``silver.quarantine`` sits beside ``silver.readings`` and receives every row
    that fails validation. Rows in equals rows landed plus rows quarantined, and
    that conservation property is what makes silent data loss detectable.

Conflicts are audited
    ``silver.consolidation_audit`` records every interval where two sources
    disagreed, including the values that lost. Without it, a user whose
    spreadsheet disagrees with the dashboard has no way to discover that a
    higher-priority source overrode their number.

EQUS Silver
===========

The plant measures throughput in equivalent batteries (EQ): every physical
output is converted into "standard batteries" with the equation
``EQ = quantity x (multiplier / standard per battery) x weight``. Bronze keeps
the QAD exports that feed it as text. The tables below are what the
calculation actually reads. They live in ``silver`` too, beside the energy
tables, and the build that fills them replaces or upserts their rows from
Bronze, so nothing here is ever edited by hand except where a column says so.

Names are Spanish on purpose: tables and columns mirror the Excel sheets and
QAD headers the business recognises (``reporte_qad``, ``campo_equs``,
``articulo_matriz``).

Five decisions are encoded in this DDL:

Natural keys
    Rows are keyed by what the business calls them: an item code, a transaction
    number, an invoice line. Item codes are text, because ``21500000A`` exists.

One table for every level of the product structure
    ``bom_estructura`` holds parent-to-component links of all levels, so an item
    is a parent in some rows and a component in others, and both columns point
    at ``item_master``. A link is unique by its business key (parent, component,
    validity dates, quantity): the exports repeat links, and a repeated link
    doubles whatever is summed over it.

The conversions are materialized views
    ``td_form`` (kg of paste per formed battery) and ``td_deco`` (decoration
    pieces per battery) replace two Excel pivots. They are derived entirely from
    the structure, so they are views; they are materialized because every
    production row is multiplied by them. Each has a unique index on its item so
    it can be refreshed without blocking readers.

The equation is data
    ``factores_equ`` stores the standard, the multiplier and the weight of each
    driver, and the factor is a generated column, so the three numbers and their
    product can never disagree. ``instrucc`` says which rows feed each driver.
    ``equs_version`` gives a set of rules a validity period, so a past month is
    always computed with the rules that were in force. Version 1 is seeded here
    with the values the workbook uses today.

Lineage reaches back into Bronze
    Structures and movements carry ``source_run_id``, a foreign key to the
    Bronze run that produced the row.

Seven questions about the rules are open with the business (EQUS_CONTEXT
section 10). This revision decides none of them: the seed reproduces what the
workbook does today, and a comment marks each place a decision would land.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFERENCE = "reference"
SILVER = "silver"
BRONZE = "bronze"

QUALITY_FLAGS = ("ok", "interpolated", "estimated", "suspect", "missing")

# -----------------------------------------------------------------------------
# EQUS: the values a few columns may take
# -----------------------------------------------------------------------------
# What a driver sums: the plain quantity, the quantity times the kg of positive
# paste of the item (td_form), the quantity times its decoration pieces
# (td_deco), or invoiced units.
FUENTES: tuple[str, ...] = ("PRODUCCION", "MAT_ACTIVO", "COMP_DECO", "FACTURACION")

# The processes whose weights add up to 100 %, plus INFO for drivers that are
# reported but do not add to the total.
PROCESOS: tuple[str, ...] = ("OREC", "ENSAMBLE", "FORMACION", "DECORADO", "SHIPPING", "INFO")
PROCESOS_CON_COSTO: tuple[str, ...] = PROCESOS[:-1]

UNIDADES: tuple[str, ...] = ("KG", "UN")

# -----------------------------------------------------------------------------
# EQUS: version 1 of the equation, as the workbook computes it today
# -----------------------------------------------------------------------------
EQUS_VERSION_1: dict[str, object] = {
    "version": 1,
    "nombre": "STD FY26",
    # The first month of fiscal year 2026. Open-ended until a version 2 exists.
    "vigente_desde": date(2025, 10, 1),
    "vigente_hasta": None,
    "aprobado_por": None,
}

# (cuenta_stats, proceso, descripcion, unidad, fuente, estandar_por_bat,
#  multiplicador, peso, suma_eq)
#
# From sheet RESUMEN EQU of the EQUS BATT workbook: the code is column A, the
# description column B, and the three numbers are the terms of the formula in
# column G. The weights add up to 1: OREC 0.074 + 0.057 + 0.037 + 0.178,
# assembly 0.354, formation 0.146, decoration 0.113, shipping 0.041. Positive
# strip and casting grids share the 0.057, the two plate drivers the 0.178, and
# the two decoration drivers the 0.113.
#
# Informative drivers carry standard 1, multiplier 1 and weight 0, so their
# factor is 0 and they cannot add to the total by accident.
#
# Open with the business, and left as the workbook has them:
#   - only TRUCK-B gets the 2.15 multiplier; TRUCK-S counts as SLI;
#   - CARTON_BATT keeps its factor but has no rule, so it contributes 0;
#   - the strip yields are the 23.634 and 25.28 of the formulas, not the 19.72
#     and 23.48 written in sheet INSTRUCC.
FACTORES_V1: tuple[tuple[str, str, str, str, str, str, str, str, bool], ...] = (
    ("OXPROD_KG", "OREC", "Produccion de Oxido", "KG", "PRODUCCION", "6.08", "1", "0.074", True),
    (
        "GRIDS_REG_POS",
        "OREC",
        "Produccion Cinta Positiva por factor",
        "KG",
        "PRODUCCION",
        "42",
        "23.634",
        "0.057",
        True,
    ),
    (
        "GRIDS_CAST",
        "OREC",
        "Produccion Rejillas Casting",
        "UN",
        "PRODUCCION",
        "42",
        "1.33",
        "0.057",
        True,
    ),
    (
        "GRIDS_NEG",
        "OREC",
        "Produccion Cinta Negativa por factor",
        "KG",
        "PRODUCCION",
        "42",
        "25.28",
        "0.037",
        True,
    ),
    (
        "PLATEPRD_SLI",
        "OREC",
        "Produccion de Placa sin incluir las de Camion",
        "UN",
        "PRODUCCION",
        "84",
        "1",
        "0.178",
        True,
    ),
    (
        "PLATEPRD_TRUCK",
        "OREC",
        "Produccion de Placa de Camion",
        "UN",
        "PRODUCCION",
        "84",
        "1.14",
        "0.178",
        True,
    ),
    (
        "COSPRODU_SLI",
        "ENSAMBLE",
        "Produccion Baterias Ensambladas sin incluir las de Camion",
        "UN",
        "PRODUCCION",
        "1",
        "1",
        "0.354",
        True,
    ),
    (
        "COSPRODU_TRUCK",
        "ENSAMBLE",
        "Produccion Baterias Ensambladas de Camion",
        "UN",
        "PRODUCCION",
        "1",
        "2.15",
        "0.354",
        True,
    ),
    (
        "POS_ACT_MTL",
        "FORMACION",
        "Pasta Positiva Material Activo de Baterias Formadas",
        "KG",
        "MAT_ACTIVO",
        "3.39",
        "1",
        "0.146",
        True,
    ),
    (
        "15BATPRD",
        "INFO",
        "Produccion de Baterias Formadas",
        "UN",
        "PRODUCCION",
        "1",
        "1",
        "0",
        False,
    ),
    ("FORM_PRD_SLI", "INFO", "Formacion Baterias SLI", "UN", "PRODUCCION", "1", "1", "0", False),
    (
        "FORM_PRD_TRUCK",
        "INFO",
        "Baterias Formadas Heavy Dutty (4D-4DLT-8D).",
        "UN",
        "PRODUCCION",
        "1",
        "1",
        "0",
        False,
    ),
    (
        "COMP_DEC",
        "DECORADO",
        "Cantidad de Componentes de Decoracion",
        "UN",
        "COMP_DECO",
        "4.29",
        "1",
        "0.113",
        True,
    ),
    (
        "CARTON_BATT",
        "DECORADO",
        "Number of batteries placed in Cartons",
        "UN",
        "PRODUCCION",
        "4.29",
        "12.8",
        "0.113",
        True,
    ),
    (
        "UNITSHIP",
        "SHIPPING",
        "Cantidad de Baterias Despachadas",
        "UN",
        "FACTURACION",
        "1",
        "1",
        "0.041",
        True,
    ),
    (
        "LEADPROD",
        "INFO",
        "Produccion de Plomos Refinandos",
        "KG",
        "PRODUCCION",
        "1",
        "1",
        "0",
        False,
    ),
)

# (cuenta_stats, regla_n, linea_de_productos, campo_equs, campo_equs_excluir,
#  prefijo_linea)
#
# Within a rule every condition that is not empty must hold; several rules of
# one driver add up. Every rule of version 1 contributes with sign +1.
#
# Seeded exactly as EQUS_CONTEXT section 6.5 lists them, which leaves two gaps
# on purpose:
#   - 15BATPRD (formed batteries, informative) is given no rule there;
#   - UNITSHIP is "invoice lines of 3103 and 3104 with channel Venta", but this
#     table has no column for the channel. The invoices export is already
#     restricted to that channel, and UNITSHIP stays net of credit notes.
INSTRUCC_V1: tuple[tuple[str, int, str | None, str | None, str | None, str | None], ...] = (
    ("OXPROD_KG", 1, None, "OXIDO", None, None),
    ("GRIDS_REG_POS", 1, None, "CINTA(+)", None, None),
    ("GRIDS_CAST", 1, "2102", None, None, None),
    ("GRIDS_NEG", 1, None, "CINTA(-)", None, None),
    ("PLATEPRD_SLI", 1, None, "PLACASLI", None, None),
    ("PLATEPRD_TRUCK", 1, None, "PLACA HD", None, None),
    ("COSPRODU_SLI", 1, "2201", None, "TRUCK-B", None),
    ("COSPRODU_TRUCK", 1, "2201", "TRUCK-B", None, None),
    ("POS_ACT_MTL", 1, "3101", None, None, None),
    ("FORM_PRD_SLI", 1, "3101", None, "TRUCK-B", None),
    ("FORM_PRD_TRUCK", 1, "3101", "TRUCK-B", None, None),
    ("COMP_DEC", 1, "3102", None, None, None),
    ("COMP_DEC", 2, "3103", None, None, None),
    ("COMP_DEC", 3, "3104", None, None, None),
    ("UNITSHIP", 1, "3103", None, None, None),
    ("UNITSHIP", 2, "3104", None, None, None),
    ("LEADPROD", 1, None, None, None, "RCREFI"),
)

# -----------------------------------------------------------------------------
# EQUS: the two conversion tables
# -----------------------------------------------------------------------------
# The schema is written out in these two statements rather than interpolated:
# they are the only ones here that select, and a query assembled from strings
# is what the security linters are right to question.
#
# Kg of paste per formed battery, walking the structure from the bottom:
# paste per plate, then per assembled battery, then per formed battery.
#
# A plate is recognised by what it consumes (a component whose description
# starts with PASTA), and the paste is split by the polarity of the paste
# itself. The filter on product line 3101 applies to the formed battery only:
# plates and assembled batteries are deliberately not filtered, because the
# imported assembled batteries (222...) and their fictitious plate 21500000A
# have no product line. That plate's paste counts as positive, which is the
# assumption the workbook makes and one of the open questions.
#
# Formation adds no paste, so a formed battery inherits that of its assembled
# battery. `calculo = 'SI'` keeps the rows the workbook counts.
TD_FORM = """
CREATE MATERIALIZED VIEW silver.td_form AS
WITH pasta_por_placa AS (
    SELECT enlace.articulo_matriz AS placa,
           coalesce(sum(enlace.cantidad_por) FILTER (WHERE pasta.polaridad = '+'), 0)
               AS pasta_pos,
           coalesce(sum(enlace.cantidad_por) FILTER (WHERE pasta.polaridad = '-'), 0)
               AS pasta_neg
    FROM silver.bom_estructura AS enlace
    JOIN silver.item_master AS pasta
      ON pasta.numero_articulo = enlace.articulo_componente
    WHERE pasta.descripcion LIKE 'PASTA%'
    GROUP BY enlace.articulo_matriz
),
pasta_por_ensamblada AS (
    SELECT enlace.articulo_matriz AS ensamblada,
           sum(enlace.cantidad_por * placa.pasta_pos) AS pasta_pos,
           sum(enlace.cantidad_por * placa.pasta_neg) AS pasta_neg
    FROM silver.bom_estructura AS enlace
    JOIN pasta_por_placa AS placa ON placa.placa = enlace.articulo_componente
    GROUP BY enlace.articulo_matriz
)
SELECT enlace.articulo_matriz,
       sum(enlace.cantidad_por * ensamblada.pasta_pos) AS pasta_pos,
       sum(enlace.cantidad_por * ensamblada.pasta_neg) AS pasta_neg
FROM silver.bom_estructura AS enlace
JOIN pasta_por_ensamblada AS ensamblada
  ON ensamblada.ensamblada = enlace.articulo_componente
JOIN silver.item_master AS formada
  ON formada.numero_articulo = enlace.articulo_matriz
WHERE enlace.calculo = 'SI'
  AND formada.linea_de_productos = '3101'
GROUP BY enlace.articulo_matriz
WITH DATA
"""

# Decoration pieces per battery: the in-force links of a semi-decorated (3102),
# decorated (3103) or imported (3104) battery whose component is a decoration
# element.
#
# The rule is the one in column X of sheet Datos of ESTRUCTURA DECORADO, applied
# to every row. The workbook's own formula drops TERM-PRT and TERMINAL-PRT from
# row 29,261 on, which undercounts the terminal protectors of imported
# batteries; that is a defect, not a rule, and is not reproduced.
TD_DECO = """
CREATE MATERIALIZED VIEW silver.td_deco AS
SELECT enlace.articulo_matriz,
       sum(enlace.cantidad_por) AS comp_deco
FROM silver.bom_estructura AS enlace
JOIN silver.item_master AS bateria
  ON bateria.numero_articulo = enlace.articulo_matriz
JOIN silver.item_master AS pieza
  ON pieza.numero_articulo = enlace.articulo_componente
JOIN silver.element_deco AS elemento
  ON elemento.numero_articulo = enlace.articulo_componente
WHERE enlace.fin_efectivo IS NULL
  AND bateria.linea_de_productos IN ('3102', '3103', '3104')
  AND elemento.elemento_deco
  AND NOT (
      coalesce(pieza.linea_de_productos, '') = '1202'
      AND (pieza.descripcion LIKE 'SOBRECUB%' OR pieza.descripcion LIKE 'MANIJA%')
  )
  AND (
      coalesce(pieza.linea_de_productos, '') = '1401'
      OR pieza.descripcion LIKE 'TAPA %'
      OR pieza.descripcion LIKE 'CUBRE%'
      OR pieza.descripcion LIKE 'POST PROTECTOR%'
      OR pieza.descripcion LIKE 'TERM-PRT%'
      OR pieza.descripcion LIKE 'TERMINAL-PRT%'
  )
GROUP BY enlace.articulo_matriz
WITH DATA
"""


def _sql_list(values: Sequence[str]) -> str:
    """Render values as the body of a SQL ``IN (...)`` list."""
    return ", ".join(f"'{value}'" for value in values)


def _text(name: str, *, nullable: bool = True) -> sa.Column[object]:
    """Return a text column."""
    return sa.Column(name, sa.Text(), nullable=nullable)


def _numeric(name: str, *, nullable: bool = True) -> sa.Column[object]:
    """Return an exact decimal column of unconstrained precision."""
    return sa.Column(name, sa.Numeric(), nullable=nullable)


def _date(name: str, *, nullable: bool = True) -> sa.Column[object]:
    """Return a date column."""
    return sa.Column(name, sa.Date(), nullable=nullable)


def _references(
    table: str, column: str, target: str, target_column: str | None = None
) -> sa.ForeignKeyConstraint:
    """Return a foreign key from one Silver column to a Silver catalogue."""
    return sa.ForeignKeyConstraint(
        [column],
        [f"{SILVER}.{target}.{target_column or column}"],
        name=f"fk_{table}_{column}",
    )


def _source_run(table: str) -> tuple[sa.Column[object], sa.ForeignKeyConstraint]:
    """Return the lineage column of a table and its foreign key into Bronze."""
    return (
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            [f"{BRONZE}.ingestion_runs.run_id"],
            name=f"fk_{table}_source_run",
        ),
    )


def _create_equs_catalogues() -> None:
    """Create the EQUS catalogues: who and what the movements refer to."""
    # Plant areas (RC, Componentes, OREC, Ensamble, F&A). Not the plants of
    # reference.plants: those are sites, these are areas inside one site.
    op.create_table(
        "planta",
        _text("planta", nullable=False),
        # Kept by hand once the build has created the row.
        _text("nombre", nullable=False),
        sa.PrimaryKeyConstraint("planta", name="pk_planta"),
        schema=SILVER,
    )

    op.create_table(
        "almacen",
        _text("almac", nullable=False),
        _text("nombre"),
        sa.PrimaryKeyConstraint("almac", name="pk_almacen"),
        schema=SILVER,
    )

    op.create_table(
        "linea_de_productos",
        _text("linea_de_productos", nullable=False),
        _text("descripcion"),
        sa.PrimaryKeyConstraint("linea_de_productos", name="pk_linea_de_productos"),
        schema=SILVER,
    )

    # "N/A" is a row of this table: it means "not applicable", not "unknown".
    op.create_table(
        "campo_equs",
        _text("campo_equs", nullable=False),
        _text("descripcion"),
        sa.PrimaryKeyConstraint("campo_equs", name="pk_campo_equs"),
        schema=SILVER,
    )

    op.create_table(
        "grupo_fam",
        _text("familia_placa", nullable=False),
        _text("tipo_bat"),
        _text("familia"),
        sa.PrimaryKeyConstraint("familia_placa", name="pk_grupo_fam"),
        schema=SILVER,
    )

    op.create_table(
        "vendido_a",
        _text("vendido_a", nullable=False),
        _text("ordenar_nombre"),
        _text("pais"),
        _text("tipo"),
        sa.PrimaryKeyConstraint("vendido_a", name="pk_vendido_a"),
        schema=SILVER,
    )

    op.create_table(
        "lineas_produccion",
        _text("linea", nullable=False),
        _text("descripcion"),
        _text("almac"),
        # Empty for the lines the plant has not assigned to an area yet.
        _text("planta"),
        _text("grupo"),
        _text("proceso_energia"),
        _numeric("unidades_por_hora"),
        sa.PrimaryKeyConstraint("linea", name="pk_lineas_produccion"),
        _references("lineas_produccion", "almac", "almacen"),
        _references("lineas_produccion", "planta", "planta"),
        schema=SILVER,
    )

    op.create_table(
        "item_master",
        # Text, not a number: 21500000A and 21100000A are real item codes.
        _text("numero_articulo", nullable=False),
        _text("descripcion"),
        _text("descripcion2"),
        _text("unidad_de_medida"),
        _text("linea_de_productos"),
        _text("campo_equs"),
        _text("tipo_de_articulo"),
        _text("grpo"),
        _text("familia_placa"),
        _numeric("peso_neto"),
        # Derived, not exported: the polarity of a paste or a plate.
        sa.Column("polaridad", sa.CHAR(1), nullable=True),
        _text("estado"),
        sa.PrimaryKeyConstraint("numero_articulo", name="pk_item_master"),
        _references("item_master", "linea_de_productos", "linea_de_productos"),
        _references("item_master", "campo_equs", "campo_equs"),
        _references("item_master", "familia_placa", "grupo_fam"),
        sa.CheckConstraint("polaridad IN ('+', '-')", name="ck_item_master_polaridad"),
        schema=SILVER,
    )

    op.create_table(
        "costo_std",
        _text("numero_articulo", nullable=False),
        _date("vigente_desde", nullable=False),
        # The day the next cost starts; empty for the cost in force.
        _date("vigente_hasta"),
        _numeric("costo_std", nullable=False),
        sa.PrimaryKeyConstraint("numero_articulo", "vigente_desde", name="pk_costo_std"),
        _references("costo_std", "numero_articulo", "item_master"),
        sa.CheckConstraint(
            "vigente_hasta IS NULL OR vigente_hasta > vigente_desde",
            name="ck_costo_std_vigencia",
        ),
        schema=SILVER,
    )


def _create_equs_structures() -> None:
    """Create the product structure and the catalogues the calculation uses."""
    op.create_table(
        "bom_estructura",
        sa.Column("bom_id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        _text("articulo_matriz", nullable=False),
        _text("articulo_componente", nullable=False),
        # Negative when the component is recovered scrap.
        sa.Column("cantidad_por", sa.Numeric(18, 8), nullable=False),
        _text("umcomp"),
        _date("inicio_efectiva"),
        # Empty while the link is in force.
        _date("fin_efectivo"),
        _numeric("desp"),
        sa.Column("operacion", sa.Integer(), nullable=True),
        _text("referencia"),
        _date("fecha_modificada"),
        _text("id_del_usuario"),
        # Whether the workbook counts the row towards the paste of a formed
        # battery. Only sheet BAT FOR says NO.
        sa.Column("calculo", sa.String(2), nullable=False, server_default="SI"),
        _text("source_sheet", nullable=False),
        *_source_run("bom_estructura"),
        sa.PrimaryKeyConstraint("bom_id", name="pk_bom_estructura"),
        # Two foreign keys into the same table: an item is a parent here and a
        # component there, which is what chains the levels together.
        _references("bom_estructura", "articulo_matriz", "item_master", "numero_articulo"),
        _references("bom_estructura", "articulo_componente", "item_master", "numero_articulo"),
        sa.CheckConstraint("calculo IN ('SI', 'NO')", name="ck_bom_estructura_calculo"),
        # One row per link. Empty dates compare as equal, or two open-ended
        # copies of the same link would both be accepted.
        sa.UniqueConstraint(
            "articulo_matriz",
            "articulo_componente",
            "inicio_efectiva",
            "fin_efectivo",
            "cantidad_por",
            name="uq_bom_estructura_enlace",
            postgresql_nulls_not_distinct=True,
        ),
        schema=SILVER,
    )
    op.create_index(
        "ix_bom_estructura_matriz", "bom_estructura", ["articulo_matriz"], schema=SILVER
    )
    op.create_index(
        "ix_bom_estructura_componente", "bom_estructura", ["articulo_componente"], schema=SILVER
    )

    op.create_table(
        "element_deco",
        _text("numero_articulo", nullable=False),
        _text("grpo"),
        _text("tipo_de_articulo"),
        sa.Column("elemento_deco", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("numero_articulo", name="pk_element_deco"),
        _references("element_deco", "numero_articulo", "item_master"),
        schema=SILVER,
    )

    # The factor of a plastic piece, for the separate EQUS POLY indicator.
    # TAPONES is 0.05 here; a side table of the workbook says 0.28, which is an
    # open question, and 0.05 is what the workbook actually uses.
    op.create_table(
        "clasif_poly",
        _text("numero_articulo", nullable=False),
        _text("campo_equs"),
        _text("cuenta_stats"),
        _numeric("factor_equ"),
        sa.PrimaryKeyConstraint("numero_articulo", name="pk_clasif_poly"),
        _references("clasif_poly", "numero_articulo", "item_master"),
        schema=SILVER,
    )

    # Conversion cost from accounting. No workbook carries it yet, so the table
    # is created empty.
    op.create_table(
        "costo_conversion",
        _date("mes", nullable=False),
        _text("planta", nullable=False),
        _text("proceso", nullable=False),
        _numeric("costo_cop", nullable=False),
        sa.PrimaryKeyConstraint("mes", "planta", "proceso", name="pk_costo_conversion"),
        _references("costo_conversion", "planta", "planta"),
        sa.CheckConstraint(
            "mes = date_trunc('month', mes)::date", name="ck_costo_conversion_primer_dia"
        ),
        sa.CheckConstraint(
            f"proceso IN ({_sql_list(PROCESOS_CON_COSTO)})", name="ck_costo_conversion_proceso"
        ),
        schema=SILVER,
    )


def _create_equs_movements() -> None:
    """Create the two movement tables: production receipts and invoice lines."""
    op.create_table(
        "reporte_qad",
        sa.Column("numero_de_transaccion", sa.BigInteger(), nullable=False),
        _text("tipo_transaccion"),
        # The accounting date. It decides the month a receipt belongs to; the
        # entry date in `fecha` does not.
        _date("fecha_efectiva", nullable=False),
        _date("fecha"),
        _text("tiempo"),
        _text("numero_articulo", nullable=False),
        _text("almac"),
        _text("ubicacion"),
        _text("lote_serie"),
        _text("orden"),
        sa.Column("id", sa.BigInteger(), nullable=True),
        # Empty for the receipts QAD records without a line.
        _text("linea_produccion"),
        # The quantity used everywhere. Negative for a reversal.
        _numeric("cambiar_cantidad_de_ubic", nullable=False),
        _text("unidad_de_medida"),
        _numeric("precio"),
        _numeric("costo_total"),
        # The category the item had when the export was taken. "N/A" is a value.
        _text("campo_equs"),
        _numeric("peso_neto"),
        _text("status_inventario"),
        _text("program"),
        _text("id_del_usuario"),
        *_source_run("reporte_qad"),
        sa.PrimaryKeyConstraint("numero_de_transaccion", name="pk_reporte_qad"),
        _references("reporte_qad", "numero_articulo", "item_master"),
        _references("reporte_qad", "almac", "almacen"),
        _references("reporte_qad", "linea_produccion", "lineas_produccion", "linea"),
        schema=SILVER,
    )
    op.create_index(
        "ix_reporte_qad_fecha_tipo",
        "reporte_qad",
        ["fecha_efectiva", "tipo_transaccion"],
        schema=SILVER,
    )
    op.create_index("ix_reporte_qad_articulo", "reporte_qad", ["numero_articulo"], schema=SILVER)
    op.create_index("ix_reporte_qad_linea", "reporte_qad", ["linea_produccion"], schema=SILVER)

    op.create_table(
        "unit_ship",
        _text("num_fact", nullable=False),
        _text("orden", nullable=False),
        sa.Column("linea", sa.Integer(), nullable=False),
        _text("remision"),
        _text("canal"),
        _date("fecha_de_la_factura"),
        _date("fecha_efectiva", nullable=False),
        _text("vendido_a"),
        _text("embarcar_para"),
        _text("numero_articulo", nullable=False),
        # Negative for a credit note.
        _numeric("cantidad_fac", nullable=False),
        _numeric("precio_de_lista"),
        _numeric("porcentaje_descuento"),
        _numeric("precio_neto"),
        _numeric("monto_factura"),
        _text("moneda"),
        _numeric("tipos_de_cambio"),
        _numeric("importe_de_factura_mb"),
        _numeric("total_plomo"),
        _text("campo_equs"),
        *_source_run("unit_ship"),
        sa.PrimaryKeyConstraint("num_fact", "orden", "linea", name="pk_unit_ship"),
        _references("unit_ship", "vendido_a", "vendido_a"),
        _references("unit_ship", "numero_articulo", "item_master"),
        schema=SILVER,
    )
    op.create_index("ix_unit_ship_fecha", "unit_ship", ["fecha_efectiva"], schema=SILVER)
    op.create_index("ix_unit_ship_articulo", "unit_ship", ["numero_articulo"], schema=SILVER)


def _create_equs_configuration() -> None:
    """Create the tables that hold the equation as data."""
    op.create_table(
        "equs_version",
        sa.Column("version", sa.Integer(), sa.Identity(always=False), nullable=False),
        _text("nombre", nullable=False),
        _date("vigente_desde", nullable=False),
        # Empty for the version in force.
        _date("vigente_hasta"),
        _text("aprobado_por"),
        sa.PrimaryKeyConstraint("version", name="pk_equs_version"),
        sa.CheckConstraint(
            "vigente_hasta IS NULL OR vigente_hasta >= vigente_desde",
            name="ck_equs_version_vigencia",
        ),
        schema=SILVER,
    )

    op.create_table(
        "factores_equ",
        sa.Column("version", sa.Integer(), nullable=False),
        _text("cuenta_stats", nullable=False),
        _text("proceso", nullable=False),
        _text("descripcion"),
        _text("unidad", nullable=False),
        _text("fuente", nullable=False),
        _numeric("estandar_por_bat", nullable=False),
        _numeric("multiplicador", nullable=False),
        _numeric("peso", nullable=False),
        # The EQ value of one physical unit. Generated, so it cannot drift from
        # the three numbers it is made of.
        sa.Column(
            "factor",
            sa.Numeric(),
            sa.Computed("multiplicador / estandar_por_bat * peso", persisted=True),
        ),
        sa.Column("suma_eq", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("version", "cuenta_stats", name="pk_factores_equ"),
        _references("factores_equ", "version", "equs_version"),
        sa.CheckConstraint(f"proceso IN ({_sql_list(PROCESOS)})", name="ck_factores_equ_proceso"),
        sa.CheckConstraint(f"unidad IN ({_sql_list(UNIDADES)})", name="ck_factores_equ_unidad"),
        sa.CheckConstraint(f"fuente IN ({_sql_list(FUENTES)})", name="ck_factores_equ_fuente"),
        sa.CheckConstraint("estandar_por_bat > 0", name="ck_factores_equ_estandar"),
        sa.CheckConstraint("peso >= 0 AND peso <= 1", name="ck_factores_equ_peso"),
        schema=SILVER,
    )

    op.create_table(
        "instrucc",
        sa.Column("version", sa.Integer(), nullable=False),
        _text("cuenta_stats", nullable=False),
        sa.Column("regla_n", sa.Integer(), nullable=False),
        # A condition left empty accepts anything.
        _text("linea_de_productos"),
        _text("campo_equs"),
        _text("campo_equs_excluir"),
        _text("prefijo_linea"),
        sa.Column("signo", sa.SmallInteger(), nullable=False, server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("version", "cuenta_stats", "regla_n", name="pk_instrucc"),
        sa.ForeignKeyConstraint(
            ["version", "cuenta_stats"],
            [f"{SILVER}.factores_equ.version", f"{SILVER}.factores_equ.cuenta_stats"],
            name="fk_instrucc_factor",
        ),
        sa.CheckConstraint("signo IN (1, -1)", name="ck_instrucc_signo"),
        schema=SILVER,
    )


def _create_equs_views() -> None:
    """Create the two conversion tables as materialized views."""
    op.execute(TD_FORM)
    op.execute(TD_DECO)
    # A unique index is what lets REFRESH ... CONCURRENTLY rebuild a view while
    # it is being read.
    op.execute(f"CREATE UNIQUE INDEX uq_td_form_articulo ON {SILVER}.td_form (articulo_matriz)")
    op.execute(f"CREATE UNIQUE INDEX uq_td_deco_articulo ON {SILVER}.td_deco (articulo_matriz)")


def _seed_equs_version_1() -> None:
    """Insert version 1 of the equation: the rules the workbook applies today."""
    version = EQUS_VERSION_1["version"]

    op.bulk_insert(
        sa.table(
            "equs_version",
            sa.column("version", sa.Integer()),
            sa.column("nombre", sa.Text()),
            sa.column("vigente_desde", sa.Date()),
            sa.column("vigente_hasta", sa.Date()),
            sa.column("aprobado_por", sa.Text()),
            schema=SILVER,
        ),
        [EQUS_VERSION_1],
    )
    # The version was given explicitly, so move the sequence past it.
    op.execute(
        f"SELECT setval(pg_get_serial_sequence('{SILVER}.equs_version', 'version'), {version})"
    )

    op.bulk_insert(
        sa.table(
            "factores_equ",
            sa.column("version", sa.Integer()),
            sa.column("cuenta_stats", sa.Text()),
            sa.column("proceso", sa.Text()),
            sa.column("descripcion", sa.Text()),
            sa.column("unidad", sa.Text()),
            sa.column("fuente", sa.Text()),
            sa.column("estandar_por_bat", sa.Numeric()),
            sa.column("multiplicador", sa.Numeric()),
            sa.column("peso", sa.Numeric()),
            sa.column("suma_eq", sa.Boolean()),
            schema=SILVER,
        ),
        [
            {
                "version": version,
                "cuenta_stats": cuenta,
                "proceso": proceso,
                "descripcion": descripcion,
                "unidad": unidad,
                "fuente": fuente,
                "estandar_por_bat": Decimal(estandar),
                "multiplicador": Decimal(multiplicador),
                "peso": Decimal(peso),
                "suma_eq": suma_eq,
            }
            for (
                cuenta,
                proceso,
                descripcion,
                unidad,
                fuente,
                estandar,
                multiplicador,
                peso,
                suma_eq,
            ) in FACTORES_V1
        ],
    )

    op.bulk_insert(
        sa.table(
            "instrucc",
            sa.column("version", sa.Integer()),
            sa.column("cuenta_stats", sa.Text()),
            sa.column("regla_n", sa.Integer()),
            sa.column("linea_de_productos", sa.Text()),
            sa.column("campo_equs", sa.Text()),
            sa.column("campo_equs_excluir", sa.Text()),
            sa.column("prefijo_linea", sa.Text()),
            schema=SILVER,
        ),
        [
            {
                "version": version,
                "cuenta_stats": cuenta,
                "regla_n": regla_n,
                "linea_de_productos": linea,
                "campo_equs": campo,
                "campo_equs_excluir": excluir,
                "prefijo_linea": prefijo,
            }
            for cuenta, regla_n, linea, campo, excluir, prefijo in INSTRUCC_V1
        ],
    )


def upgrade() -> None:
    """Create the reference and Silver schemas and their tables."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {REFERENCE}")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SILVER}")

    # ------------------------------------------------------------------
    # reference.plants
    # ------------------------------------------------------------------
    op.create_table(
        "plants",
        sa.Column(
            "plant_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("floor_area_m2", sa.Numeric(14, 2), nullable=True),
        sa.Column("commissioned_on", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("floor_area_m2 IS NULL OR floor_area_m2 > 0", name="ck_plants_area"),
        schema=REFERENCE,
    )

    # ------------------------------------------------------------------
    # reference.meters
    # ------------------------------------------------------------------
    op.create_table(
        "meters",
        sa.Column(
            "meter_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag", sa.String(64), nullable=False),
        sa.Column("commodity", sa.String(20), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("multiplier", sa.Numeric(18, 6), nullable=False, server_default=sa.text("1.0")),
        sa.Column("is_virtual", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("parent_meter_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("installed_on", sa.Date(), nullable=True),
        sa.Column("removed_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["plant_id"], [f"{REFERENCE}.plants.plant_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_meter_id"], [f"{REFERENCE}.meters.meter_id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("plant_id", "tag", name="uq_meters_plant_tag"),
        sa.CheckConstraint(
            "commodity IN ('electricity', 'gas', 'steam', 'water', 'production')",
            name="ck_meters_commodity",
        ),
        sa.CheckConstraint("multiplier > 0", name="ck_meters_multiplier"),
        schema=REFERENCE,
    )

    # ------------------------------------------------------------------
    # reference.shifts
    # ------------------------------------------------------------------
    op.create_table(
        "shifts",
        sa.Column(
            "shift_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=False),
        sa.Column("ends_at", sa.Time(), nullable=False),
        sa.Column("weekdays", postgresql.ARRAY(sa.SmallInteger()), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["plant_id"], [f"{REFERENCE}.plants.plant_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("plant_id", "name", "valid_from", name="uq_shifts_plant_name_from"),
        schema=REFERENCE,
    )

    # ------------------------------------------------------------------
    # reference.targets - temporally versioned
    # ------------------------------------------------------------------
    op.create_table(
        "targets",
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kpi_key", sa.String(64), nullable=False),
        sa.Column("value", sa.Numeric(18, 6), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("direction", sa.String(10), nullable=False, server_default="lower"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["plant_id"], [f"{REFERENCE}.plants.plant_id"], ondelete="CASCADE"),
        sa.CheckConstraint("direction IN ('lower', 'higher')", name="ck_targets_direction"),
        sa.CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_targets_validity"),
        sa.UniqueConstraint("plant_id", "kpi_key", "valid_from", name="uq_targets_scope_from"),
        schema=REFERENCE,
    )

    quality_list = ", ".join(f"'{flag}'" for flag in QUALITY_FLAGS)

    # ------------------------------------------------------------------
    # silver.readings - hypertable in revision 0005
    # ------------------------------------------------------------------
    op.create_table(
        "readings",
        sa.Column(
            "reading_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("quality", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column(
            "source_priority",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("100"),
        ),
        sa.Column("ingestion_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("reading_id", "ts_utc", name="pk_silver_readings"),
        sa.CheckConstraint(f"quality IN ({quality_list})", name="ck_readings_quality"),
        # A `missing` row carries no value; any other flag must carry one. This is
        # what keeps "no data" from being stored as zero.
        sa.CheckConstraint(
            "(quality = 'missing' AND value IS NULL) "
            "OR (quality <> 'missing' AND value IS NOT NULL)",
            name="ck_readings_missing_has_no_value",
        ),
        sa.CheckConstraint(
            "value IS NULL OR (value = value AND value <> 'Infinity'::double precision "
            "AND value <> '-Infinity'::double precision)",
            name="ck_readings_value_finite",
        ),
        # The canonical grid is hourly: timestamps must land exactly on the hour.
        sa.CheckConstraint("date_trunc('hour', ts_utc) = ts_utc", name="ck_readings_grid_aligned"),
        schema=SILVER,
    )

    # Uniqueness applies only to live rows, so a superseded correction can sit
    # beside the value that replaced it. Includes ts_utc for TimescaleDB.
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_silver_readings_live
            ON {SILVER}.readings (meter_id, ts_utc)
            WHERE superseded_at IS NULL
        """
    )
    op.create_index(
        "ix_silver_readings_plant_ts",
        "readings",
        ["plant_id", sa.text("ts_utc DESC")],
        schema=SILVER,
    )
    op.create_index("ix_silver_readings_run", "readings", ["ingestion_run_id"], schema=SILVER)

    # ------------------------------------------------------------------
    # silver.quarantine
    # ------------------------------------------------------------------
    op.create_table(
        "quarantine",
        sa.Column(
            "quarantine_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bronze_row_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plant_code", sa.String(32), nullable=False),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("rule", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "quarantined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=False, server_default=""),
        schema=SILVER,
    )
    op.create_index("ix_quarantine_run", "quarantine", ["run_id"], schema=SILVER)
    op.create_index("ix_quarantine_rule", "quarantine", ["rule"], schema=SILVER)
    # Partial index: the working queue a data steward actually looks at.
    op.execute(
        f"""
        CREATE INDEX ix_quarantine_unresolved
            ON {SILVER}.quarantine (plant_code, quarantined_at DESC)
            WHERE resolved_at IS NULL
        """
    )

    # ------------------------------------------------------------------
    # silver.consolidation_audit
    # ------------------------------------------------------------------
    op.create_table(
        "consolidation_audit",
        sa.Column(
            "audit_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("ts_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("winning_source", sa.String(64), nullable=False),
        sa.Column("losing_sources", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("winning_value", sa.Double(), nullable=True),
        sa.Column("losing_values", postgresql.ARRAY(sa.Double()), nullable=False),
        sa.Column("divergence_pct", sa.Double(), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["meter_id"], [f"{REFERENCE}.meters.meter_id"], ondelete="CASCADE"),
        schema=SILVER,
    )
    op.create_index(
        "ix_consolidation_audit_meter_ts",
        "consolidation_audit",
        ["meter_id", sa.text("ts_utc DESC")],
        schema=SILVER,
    )
    # Surfaces the intervals where sources disagreed materially.
    op.execute(
        f"""
        CREATE INDEX ix_consolidation_audit_divergent
            ON {SILVER}.consolidation_audit (divergence_pct DESC)
            WHERE divergence_pct > 2.0
        """
    )

    # ------------------------------------------------------------------
    # EQUS Silver. Catalogues first: everything else points at them.
    # ------------------------------------------------------------------
    _create_equs_catalogues()
    _create_equs_structures()
    _create_equs_movements()
    _create_equs_configuration()
    _create_equs_views()
    _seed_equs_version_1()


def downgrade() -> None:
    """Drop the Silver and reference schemas.

    Silver goes first because it holds a foreign key into ``reference``. Gold,
    the other layer that references it, is already gone by the time this runs.

    Dropping the whole Silver schema is safe for the EQUS tables too. Nothing
    outside it points in: their only foreign keys leave Silver, towards
    ``bronze.ingestion_runs``, and go with the tables. And every row they hold
    is derived, so no data is lost: the build fills them again from Bronze, and
    the seeded version 1 of the equation is inserted again by ``upgrade()``.
    The one exception is a version of the equation added by hand later, which
    lives nowhere else and has to be exported before a downgrade.
    """
    op.execute(f"DROP SCHEMA IF EXISTS {SILVER} CASCADE")
    op.execute(f"DROP SCHEMA IF EXISTS {REFERENCE} CASCADE")
