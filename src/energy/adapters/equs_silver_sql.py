"""The SQL of the Bronze to Silver build of the equivalent-batteries data.

Layer: adapters

Responsibility:
    Holds, in the order they run, the statements that turn the Bronze payloads
    of the equivalent-batteries (EQUS) exports into the typed Silver tables, and
    the queries of the data-quality report. The heavy lifting is SQL on purpose:
    every rule is a set operation over a few hundred thousand rows, and the
    database does those in seconds. :mod:`energy.adapters.postgres_equs_silver`
    runs these statements in one transaction and reads nothing else.

    The module is data, not behaviour. Three conventions make it readable:

    * Payload keys are the header texts of the exports, in Spanish with accents
      (``Número de transacción``, ``Artículo Matriz``). They are data and must
      match the workbooks character for character.
    * Staging lives in temporary tables named ``eq_*`` that disappear when the
      transaction ends, so a build leaves nothing behind but Silver rows.
    * Text is turned into typed values by a few session-local functions
      (``pg_temp.eq_txt``, ``pg_temp.eq_num``, ``pg_temp.eq_fecha``). A cell
      that is not what its column should hold becomes ``NULL`` rather than an
      error, so one bad cell cannot stop a monthly close.

    Bronze is only read. Nothing here updates or deletes a Bronze row.

Depends on:
    standard library only.
"""

from __future__ import annotations

from typing import Final, NamedTuple

# What a catalogue row is called when no source describes it. The statements
# below spell it out rather than interpolate it, so that no query is assembled
# from strings; a unit test checks that they agree with this constant.
SIN_DESCRIPCION: Final = "(sin descripción)"


class Step(NamedTuple):
    """One statement of the build.

    Attributes:
        name: what the step does, for logs and error messages.
        sql: the statement.
    """

    name: str
    sql: str


# -----------------------------------------------------------------------------
# Parsing
# -----------------------------------------------------------------------------
FUNCTIONS: Final = Step(
    "parsing functions",
    r"""
    -- Trimmed text; an empty cell is NULL. "N/A" is text and stays "N/A".
    CREATE OR REPLACE FUNCTION pg_temp.eq_txt(valor text) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$ SELECT nullif(btrim(valor), '') $$;

    -- Exact decimal. Bronze stores numbers as plain decimal text, never in
    -- scientific notation, so anything else is not a number.
    CREATE OR REPLACE FUNCTION pg_temp.eq_num(valor text) RETURNS numeric
    LANGUAGE sql IMMUTABLE AS $$
        SELECT CASE
            WHEN btrim(valor) ~ '^[-+]?([0-9]+[.]?[0-9]*|[.][0-9]+)$' THEN btrim(valor)::numeric
        END
    $$;

    -- The date part of an ISO date or date-time.
    CREATE OR REPLACE FUNCTION pg_temp.eq_fecha(valor text) RETURNS date
    LANGUAGE sql STABLE AS $$
        SELECT CASE
            WHEN valor ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN left(valor, 10)::date
        END
    $$;

    -- A production line as QAD names it. The sources disagree on two: some
    -- write faform02 in lower case, and the comparison sheet calls OEMPSOV the
    -- line QAD calls OEMPSOV1.
    CREATE OR REPLACE FUNCTION pg_temp.eq_linea(valor text) RETURNS text
    LANGUAGE sql IMMUTABLE AS $$
        SELECT CASE upper(nullif(btrim(valor), ''))
            WHEN 'OEMPSOV' THEN 'OEMPSOV1'
            ELSE upper(nullif(btrim(valor), ''))
        END
    $$;
    """,
)

# -----------------------------------------------------------------------------
# Which Bronze rows are used
# -----------------------------------------------------------------------------
# Snapshot sources say "this is how things are now": for each sheet only the
# latest completed run counts, and Silver is replaced by it.
LATEST_RUNS: Final = Step(
    "latest completed run of each snapshot sheet",
    """
    CREATE TEMP TABLE eq_corrida_estructura ON COMMIT DROP AS
    SELECT DISTINCT ON (hoja.sheet_name)
           hoja.sheet_name, hoja.ingestion_run_id, corrida.finished_at
    FROM (SELECT DISTINCT sheet_name, ingestion_run_id FROM bronze.raw_product_struc) AS hoja
    JOIN bronze.ingestion_runs AS corrida ON corrida.run_id = hoja.ingestion_run_id
    WHERE corrida.state = 'completed'
    ORDER BY hoja.sheet_name, corrida.finished_at DESC, corrida.run_id DESC;

    CREATE TEMP TABLE eq_corrida_manual ON COMMIT DROP AS
    SELECT DISTINCT ON (hoja.dataset_key)
           hoja.dataset_key, hoja.ingestion_run_id, corrida.finished_at
    FROM (SELECT DISTINCT dataset_key, ingestion_run_id FROM bronze.raw_manual_sheets) AS hoja
    JOIN bronze.ingestion_runs AS corrida ON corrida.run_id = hoja.ingestion_run_id
    WHERE corrida.state = 'completed'
    ORDER BY hoja.dataset_key, corrida.finished_at DESC, corrida.run_id DESC;

    CREATE TEMP TABLE eq_corrida_maestro ON COMMIT DROP AS
    SELECT DISTINCT ON (hoja.sheet_name)
           hoja.sheet_name, hoja.ingestion_run_id, corrida.finished_at
    FROM (SELECT DISTINCT sheet_name, ingestion_run_id FROM bronze.raw_item_master) AS hoja
    JOIN bronze.ingestion_runs AS corrida ON corrida.run_id = hoja.ingestion_run_id
    WHERE corrida.state = 'completed'
    ORDER BY hoja.sheet_name, corrida.finished_at DESC, corrida.run_id DESC;

    CREATE TEMP TABLE eq_manual ON COMMIT DROP AS
    SELECT fila.dataset_key, fila.source_row, fila.ingestion_run_id, fila.payload
    FROM bronze.raw_manual_sheets AS fila
    JOIN eq_corrida_manual AS corrida USING (dataset_key, ingestion_run_id);

    CREATE TEMP TABLE eq_maestro ON COMMIT DROP AS
    SELECT fila.sheet_name, fila.source_row, fila.ingestion_run_id, fila.payload
    FROM bronze.raw_item_master AS fila
    JOIN eq_corrida_maestro AS corrida USING (sheet_name, ingestion_run_id);
    """,
)

# -----------------------------------------------------------------------------
# Product structures
# -----------------------------------------------------------------------------
# Two defects of the sheets are handled before a single value is read.
#
# A header row pasted among the data (PLACAS row 257, BAT FOR row 4114) is
# recognised by its first cell holding the header text itself, and dropped.
#
# The rows below such a header belong to the export it came with, and that
# export need not have the columns in the same order as the sheet. In BAT FOR
# the block below row 4114 has type and group swapped and the unit of measure
# where the sheet has the second description. Bronze keyed those cells by the
# sheet's own headers, so here each cell is given the name the pasted header
# gives its column. Two of its texts are other spellings of a known header.
STRUCTURE_ROWS: Final = Step(
    "structure rows, read by the header that governs each block",
    """
    CREATE TEMP TABLE eq_estructura_cruda ON COMMIT DROP AS
    SELECT fila.sheet_name, fila.source_row, fila.ingestion_run_id,
           corrida.finished_at, fila.payload
    FROM bronze.raw_product_struc AS fila
    JOIN eq_corrida_estructura AS corrida USING (sheet_name, ingestion_run_id);

    CREATE TEMP TABLE eq_cabecera ON COMMIT DROP AS
    SELECT sheet_name, source_row, payload
    FROM eq_estructura_cruda
    WHERE payload ->> 'Artículo Matriz' = 'Artículo Matriz';

    CREATE TEMP TABLE eq_estructura ON COMMIT DROP AS
    WITH fila AS (
        SELECT cruda.*,
               (SELECT max(cabecera.source_row)
                FROM eq_cabecera AS cabecera
                WHERE cabecera.sheet_name = cruda.sheet_name
                  AND cabecera.source_row < cruda.source_row) AS fila_cabecera
        FROM eq_estructura_cruda AS cruda
        WHERE cruda.payload ->> 'Artículo Matriz' IS DISTINCT FROM 'Artículo Matriz'
    )
    SELECT fila.sheet_name, fila.source_row, fila.ingestion_run_id, fila.finished_at,
           fila.fila_cabecera IS NOT NULL AS releida,
           CASE
               WHEN fila.fila_cabecera IS NULL THEN fila.payload
               ELSE (
                   SELECT jsonb_object_agg(
                              CASE coalesce(cabecera.payload ->> celda.key, celda.key)
                                  WHEN 'DESCRIPCION_DE_ARTICULO' THEN 'Descripción'
                                  WHEN 'Grupo' THEN 'Grpo'
                                  ELSE coalesce(cabecera.payload ->> celda.key, celda.key)
                              END,
                              celda.value)
                   FROM jsonb_each(fila.payload) AS celda
                   JOIN eq_cabecera AS cabecera
                     ON cabecera.sheet_name = fila.sheet_name
                    AND cabecera.source_row = fila.fila_cabecera
               )
           END AS payload
    FROM fila;
    """,
)

# Keys are read by header name, never by position: the sheets do not share one
# column layout. `calculo` exists only in BAT FOR; everywhere else it is SI.
STRUCTURE_LINKS: Final = Step(
    "typed structure links, with their repeats marked",
    """
    CREATE TEMP TABLE eq_enlace ON COMMIT DROP AS
    SELECT sheet_name, source_row, ingestion_run_id, finished_at,
           pg_temp.eq_txt(payload ->> 'Artículo Matriz') AS articulo_matriz,
           pg_temp.eq_txt(payload ->> 'Artículo Componente') AS articulo_componente,
           round(pg_temp.eq_num(payload ->> 'Cantidad Por'), 8) AS cantidad_por,
           pg_temp.eq_txt(payload ->> 'UMCOMP') AS umcomp,
           pg_temp.eq_fecha(payload ->> 'Inicio Efectiva') AS inicio_efectiva,
           pg_temp.eq_fecha(payload ->> 'Fin Efectivo') AS fin_efectivo,
           pg_temp.eq_num(payload ->> 'Desp') AS desp,
           round(pg_temp.eq_num(payload ->> 'Operación'))::integer AS operacion,
           pg_temp.eq_txt(payload ->> 'Referencia') AS referencia,
           pg_temp.eq_fecha(payload ->> 'Fecha Modificada') AS fecha_modificada,
           pg_temp.eq_txt(payload ->> 'ID del Usuario') AS id_del_usuario,
           CASE
               WHEN sheet_name = 'BAT FOR' AND upper(btrim(payload ->> 'CALCULO')) = 'NO' THEN 'NO'
               ELSE 'SI'
           END AS calculo
    FROM eq_estructura;

    -- A link is the same link when parent, component, validity and quantity
    -- agree, whatever else differs. `copia_en_hoja` counts its repeats inside
    -- one sheet; `copia` counts them across sheets too, newest run first, so
    -- `copia = 1` is the one row Silver keeps.
    CREATE TEMP TABLE eq_bom ON COMMIT DROP AS
    SELECT enlace.*,
           row_number() OVER (
               PARTITION BY sheet_name, articulo_matriz, articulo_componente,
                            inicio_efectiva, fin_efectivo, cantidad_por
               ORDER BY source_row) AS copia_en_hoja,
           row_number() OVER (
               PARTITION BY articulo_matriz, articulo_componente,
                            inicio_efectiva, fin_efectivo, cantidad_por
               ORDER BY finished_at DESC, sheet_name, source_row) AS copia
    FROM eq_enlace AS enlace
    WHERE articulo_matriz IS NOT NULL
      AND articulo_componente IS NOT NULL
      AND cantidad_por IS NOT NULL;
    """,
)

# -----------------------------------------------------------------------------
# Movements
# -----------------------------------------------------------------------------
# Movements accumulate: every completed run counts, and when two runs bring the
# same receipt or invoice line, the most recently landed copy wins.
MOVEMENTS: Final = Step(
    "production receipts and invoice lines, newest copy of each",
    """
    CREATE TEMP TABLE eq_rct ON COMMIT DROP AS
    SELECT DISTINCT ON (numero_de_transaccion) *
    FROM (
        SELECT round(pg_temp.eq_num(fila.payload ->> 'Número de transacción'))::bigint
                   AS numero_de_transaccion,
               pg_temp.eq_txt(fila.payload ->> 'Tipo Transacción') AS tipo_transaccion,
               pg_temp.eq_fecha(fila.payload ->> 'Fecha Efectiva') AS fecha_efectiva,
               pg_temp.eq_fecha(fila.payload ->> 'Fecha') AS fecha,
               pg_temp.eq_txt(fila.payload ->> 'Tiempo') AS tiempo,
               pg_temp.eq_txt(fila.payload ->> 'Número Artículo') AS numero_articulo,
               pg_temp.eq_txt(fila.payload ->> 'Almac') AS almac,
               pg_temp.eq_txt(fila.payload ->> 'Ubicación') AS ubicacion,
               pg_temp.eq_txt(fila.payload ->> 'Lote/Serie') AS lote_serie,
               pg_temp.eq_txt(fila.payload ->> 'Orden') AS orden,
               round(pg_temp.eq_num(fila.payload ->> 'ID'))::bigint AS id,
               pg_temp.eq_linea(fila.payload ->> 'Línea Producción') AS linea_produccion,
               pg_temp.eq_num(fila.payload ->> 'Cambiar cantidad de ubic')
                   AS cambiar_cantidad_de_ubic,
               pg_temp.eq_txt(fila.payload ->> 'Unidad de medida') AS unidad_de_medida,
               pg_temp.eq_num(fila.payload ->> 'Precio') AS precio,
               pg_temp.eq_num(fila.payload ->> 'Costo Total') AS costo_total,
               pg_temp.eq_txt(fila.payload ->> 'Campo Equs') AS campo_equs,
               pg_temp.eq_num(fila.payload ->> 'Peso Neto') AS peso_neto,
               pg_temp.eq_txt(fila.payload ->> 'Status Inventario') AS status_inventario,
               pg_temp.eq_txt(fila.payload ->> 'Program') AS program,
               pg_temp.eq_txt(fila.payload ->> 'ID del Usuario') AS id_del_usuario,
               pg_temp.eq_txt(fila.payload ->> 'Descripción de Artículo') AS descripcion,
               pg_temp.eq_txt(fila.payload ->> 'Línea de productos') AS linea_de_productos,
               pg_temp.eq_txt(fila.payload ->> 'Descripción') AS descripcion_linea,
               fila.ingestion_run_id AS source_run_id,
               fila.ingested_at, fila.source_row
        FROM bronze.raw_rct_wo AS fila
        JOIN bronze.ingestion_runs AS corrida ON corrida.run_id = fila.ingestion_run_id
        WHERE corrida.state = 'completed'
    ) AS recibo
    ORDER BY numero_de_transaccion, ingested_at DESC, source_run_id DESC, source_row DESC;

    CREATE TEMP TABLE eq_factura ON COMMIT DROP AS
    SELECT DISTINCT ON (num_fact, orden, linea) *
    FROM (
        SELECT pg_temp.eq_txt(fila.payload ->> 'Núm Fact') AS num_fact,
               pg_temp.eq_txt(fila.payload ->> 'Orden') AS orden,
               round(pg_temp.eq_num(fila.payload ->> 'Línea'))::integer AS linea,
               pg_temp.eq_txt(fila.payload ->> 'Remisión') AS remision,
               pg_temp.eq_txt(fila.payload ->> 'Canal') AS canal,
               pg_temp.eq_fecha(fila.payload ->> 'Fecha de la Factura') AS fecha_de_la_factura,
               pg_temp.eq_fecha(fila.payload ->> 'Fecha Efectiva') AS fecha_efectiva,
               pg_temp.eq_txt(fila.payload ->> 'Vendido-A') AS vendido_a,
               pg_temp.eq_txt(fila.payload ->> 'Embarcar-para') AS embarcar_para,
               pg_temp.eq_txt(fila.payload ->> 'Número Artículo') AS numero_articulo,
               pg_temp.eq_num(fila.payload ->> 'Cantidad fac') AS cantidad_fac,
               pg_temp.eq_num(fila.payload ->> 'Precio de Lista') AS precio_de_lista,
               pg_temp.eq_num(fila.payload ->> 'Porcentaje Descuento') AS porcentaje_descuento,
               pg_temp.eq_num(fila.payload ->> 'Precio Neto') AS precio_neto,
               pg_temp.eq_num(fila.payload ->> 'Monto Factura') AS monto_factura,
               pg_temp.eq_txt(fila.payload ->> 'Moneda') AS moneda,
               pg_temp.eq_num(fila.payload ->> 'Tipos de Cambio') AS tipos_de_cambio,
               pg_temp.eq_num(fila.payload ->> 'Importe de factura MB') AS importe_de_factura_mb,
               pg_temp.eq_num(fila.payload ->> 'TOTAL_PLOMO') AS total_plomo,
               pg_temp.eq_txt(fila.payload ->> 'Campo Equs') AS campo_equs,
               pg_temp.eq_txt(fila.payload ->> 'Descripcion') AS descripcion,
               pg_temp.eq_txt(fila.payload ->> 'UM') AS unidad_de_medida,
               pg_temp.eq_txt(fila.payload ->> 'Línea de productos') AS linea_de_productos,
               pg_temp.eq_txt(fila.payload ->> 'Descripción Línea Producto') AS descripcion_linea,
               pg_temp.eq_txt(fila.payload ->> 'Ordenar Nombre') AS ordenar_nombre,
               pg_temp.eq_txt(fila.payload ->> 'País') AS pais,
               pg_temp.eq_txt(fila.payload ->> 'Tipo') AS tipo,
               fila.ingestion_run_id AS source_run_id,
               fila.ingested_at, fila.source_row
        FROM bronze.raw_facturas AS fila
        JOIN bronze.ingestion_runs AS corrida ON corrida.run_id = fila.ingestion_run_id
        WHERE corrida.state = 'completed'
    ) AS renglon
    ORDER BY num_fact, orden, linea, ingested_at DESC, source_run_id DESC, source_row DESC;

    -- A row without its key, its date, its item or its quantity is not a
    -- movement. It is counted for the report and then left out here, before the
    -- catalogues are built, so that it does not put an item or a line in them.
    CREATE TEMP TABLE eq_excluido ON COMMIT DROP AS
    SELECT (SELECT count(*) FROM eq_rct
            WHERE numero_de_transaccion IS NULL OR fecha_efectiva IS NULL
               OR numero_articulo IS NULL OR cambiar_cantidad_de_ubic IS NULL) AS recibos,
           (SELECT count(*) FROM eq_factura
            WHERE num_fact IS NULL OR orden IS NULL OR linea IS NULL
               OR fecha_efectiva IS NULL OR numero_articulo IS NULL
               OR cantidad_fac IS NULL) AS renglones;

    DELETE FROM eq_rct
    WHERE numero_de_transaccion IS NULL OR fecha_efectiva IS NULL
       OR numero_articulo IS NULL OR cambiar_cantidad_de_ubic IS NULL;

    DELETE FROM eq_factura
    WHERE num_fact IS NULL OR orden IS NULL OR linea IS NULL
       OR fecha_efectiva IS NULL OR numero_articulo IS NULL OR cantidad_fac IS NULL;
    """,
)

# -----------------------------------------------------------------------------
# Hand-maintained catalogues
# -----------------------------------------------------------------------------
# Clasif_Poly holds two tables side by side. The first, one row per item, is the
# catalogue. The second lists the factor of each category; Bronze keyed its
# three columns with a " #2" suffix because they repeat headers of the first.
CATALOGUE_SHEETS: Final = Step(
    "decoration catalogue, plastic factors and production lines",
    """
    CREATE TEMP TABLE eq_element_deco ON COMMIT DROP AS
    SELECT DISTINCT ON (numero_articulo) *
    FROM (
        SELECT pg_temp.eq_txt(payload ->> 'Número Artículo') AS numero_articulo,
               pg_temp.eq_txt(payload ->> 'Descripción') AS descripcion,
               pg_temp.eq_txt(payload ->> 'Unidad de medida') AS unidad_de_medida,
               pg_temp.eq_txt(payload ->> 'Línea de productos') AS linea_de_productos,
               pg_temp.eq_txt(payload ->> 'Descripción Línea Producto') AS descripcion_linea,
               pg_temp.eq_txt(payload ->> 'Tipo de artículo') AS tipo_de_articulo,
               pg_temp.eq_txt(payload ->> 'Grpo') AS grpo,
               upper(btrim(coalesce(payload ->> 'elemento deco', ''))) = 'SI' AS elemento_deco,
               source_row
        FROM eq_manual WHERE dataset_key = 'element_deco'
    ) AS fila
    WHERE numero_articulo IS NOT NULL
    ORDER BY numero_articulo, source_row;

    CREATE TEMP TABLE eq_clasif_poly_fila ON COMMIT DROP AS
    SELECT pg_temp.eq_txt(payload ->> 'Número Artículo') AS numero_articulo,
           pg_temp.eq_txt(payload ->> 'Descripción') AS descripcion,
           pg_temp.eq_txt(payload ->> 'Unidad de medida') AS unidad_de_medida,
           pg_temp.eq_txt(payload ->> 'Línea de productos') AS linea_de_productos,
           pg_temp.eq_txt(payload ->> 'Descripción Línea Producto') AS descripcion_linea,
           pg_temp.eq_txt(payload ->> 'Tipo de artículo') AS tipo_de_articulo,
           pg_temp.eq_txt(payload ->> 'Grpo') AS grpo,
           pg_temp.eq_txt(payload ->> 'Campo Equs') AS campo_equs,
           pg_temp.eq_txt(payload ->> 'CUENTA STATS') AS cuenta_stats,
           pg_temp.eq_num(payload ->> 'FACTOR EQU') AS factor_equ,
           source_row
    FROM eq_manual
    WHERE dataset_key = 'clasif_poly' AND pg_temp.eq_txt(payload ->> 'Número Artículo') IS NOT NULL;

    CREATE TEMP TABLE eq_clasif_poly ON COMMIT DROP AS
    SELECT DISTINCT ON (numero_articulo) *
    FROM eq_clasif_poly_fila
    ORDER BY numero_articulo, source_row;

    CREATE TEMP TABLE eq_poly_categoria ON COMMIT DROP AS
    SELECT DISTINCT ON (campo_equs) *
    FROM (
        SELECT pg_temp.eq_txt(payload ->> 'Campo Equs #2') AS campo_equs,
               pg_temp.eq_txt(payload ->> 'CUENTA STATS #2') AS cuenta_stats,
               pg_temp.eq_num(payload ->> 'FACTOR EQU #2') AS factor_equ,
               source_row
        FROM eq_manual WHERE dataset_key = 'clasif_poly'
    ) AS fila
    WHERE campo_equs IS NOT NULL
    ORDER BY campo_equs, source_row;

    CREATE TEMP TABLE eq_linea_catalogo ON COMMIT DROP AS
    SELECT DISTINCT ON (linea) *
    FROM (
        SELECT pg_temp.eq_linea(payload ->> 'Línea') AS linea,
               pg_temp.eq_txt(payload ->> 'Descripción') AS descripcion,
               pg_temp.eq_txt(payload ->> 'Almac') AS almac,
               pg_temp.eq_num(payload ->> 'Planificación de Unidades por Hora')
                   AS unidades_por_hora,
               source_row
        FROM eq_manual WHERE dataset_key = 'lineas_produccion'
    ) AS fila
    WHERE linea IS NOT NULL
    ORDER BY linea, source_row;

    -- Which plant area each line belongs to, and what the energy database
    -- calls its process.
    CREATE TEMP TABLE eq_linea_planta ON COMMIT DROP AS
    SELECT DISTINCT ON (linea) *
    FROM (
        SELECT pg_temp.eq_linea(
                   payload ->> 'Línea Producción (archivo transacciones por art)') AS linea,
               pg_temp.eq_txt(payload ->> 'Planta') AS planta,
               pg_temp.eq_txt(payload ->> 'Proceso (Base de datos energia y gas)')
                   AS proceso_energia,
               source_row
        FROM eq_manual WHERE dataset_key = 'comparativo_lineas'
    ) AS fila
    WHERE linea IS NOT NULL
    ORDER BY linea, source_row;

    -- Sheet L.PRODUCCION is landed only on request, because it shares its sheet
    -- with two pivot tables. When it is not there, no line has a group.
    CREATE TEMP TABLE eq_linea_grupo ON COMMIT DROP AS
    SELECT linea, (array_agg(grupo ORDER BY veces DESC, grupo))[1] AS grupo
    FROM (
        SELECT pg_temp.eq_linea(payload ->> 'L.PRODUCCION') AS linea,
               pg_temp.eq_txt(payload ->> 'GRUPO') AS grupo,
               count(*) AS veces
        FROM eq_manual WHERE dataset_key = 'l_produccion'
        GROUP BY 1, 2
    ) AS fila
    WHERE linea IS NOT NULL AND grupo IS NOT NULL
    GROUP BY linea;
    """,
)

# -----------------------------------------------------------------------------
# Item master
# -----------------------------------------------------------------------------
# No single export describes every item, so the item master is assembled: every
# item code seen anywhere gets a row, and each attribute takes the first value
# that is not empty, going through the sources in this order.
#
#   0  the Item_Master workbook, once it is delivered
#   1  production receipts
#   2  invoices
#   3  product structures, where the item is the parent
#   4  product structures, where the item is a component
#   5  the decoration catalogue
#   6  the plastic factors catalogue
#
# The Item_Master workbook has not been delivered, so its header texts are not
# known. They are assumed to be those of the other QAD exports; the report says
# so if the workbook lands and none of them is found.
ITEM_SOURCES: Final = Step(
    "what every source says about each item",
    """
    CREATE TEMP TABLE eq_articulo_dato ON COMMIT DROP AS
    SELECT pg_temp.eq_txt(payload ->> 'Número Artículo') AS numero_articulo,
           0 AS prioridad, source_row::bigint AS orden,
           pg_temp.eq_txt(payload ->> 'Descripción') AS descripcion,
           NULL::text AS descripcion2,
           pg_temp.eq_txt(payload ->> 'Unidad de medida') AS unidad_de_medida,
           pg_temp.eq_txt(payload ->> 'Línea de productos') AS linea_de_productos,
           pg_temp.eq_txt(payload ->> 'Campo Equs') AS campo_equs,
           pg_temp.eq_txt(payload ->> 'Tipo de artículo') AS tipo_de_articulo,
           pg_temp.eq_txt(payload ->> 'Grpo') AS grpo,
           pg_temp.eq_num(payload ->> 'Peso Neto') AS peso_neto,
           pg_temp.eq_txt(payload ->> 'Estado') AS estado,
           pg_temp.eq_txt(payload ->> 'Familia Placa') AS familia_placa
    FROM eq_maestro WHERE sheet_name = 'Datos'

    UNION ALL
    -- Newest receipt first, so an attribute that changed keeps its latest value.
    SELECT numero_articulo, 1, min(-numero_de_transaccion),
           descripcion, NULL, unidad_de_medida, linea_de_productos, campo_equs,
           NULL, NULL, peso_neto, NULL, NULL
    FROM eq_rct
    GROUP BY numero_articulo, descripcion, unidad_de_medida, linea_de_productos,
             campo_equs, peso_neto

    UNION ALL
    SELECT numero_articulo, 2, min(-extract(epoch FROM ingested_at)::bigint),
           descripcion, NULL, unidad_de_medida, linea_de_productos, campo_equs,
           NULL, NULL, NULL, NULL, NULL
    FROM eq_factura
    GROUP BY numero_articulo, descripcion, unidad_de_medida, linea_de_productos, campo_equs

    UNION ALL
    -- As parent. The second description exists only where the sheet has that
    -- column; the unit of measure only where it has that one. A row that is not
    -- a link (sheet Datos has 741 that hold nothing but a "1") names no item.
    SELECT pg_temp.eq_txt(payload ->> 'Artículo Matriz'), 3,
           row_number() OVER (ORDER BY finished_at DESC, sheet_name, source_row),
           pg_temp.eq_txt(payload ->> 'Descripción'),
           pg_temp.eq_txt(payload ->> 'Descripción #2'),
           pg_temp.eq_txt(payload ->> 'Unidad de medida'),
           pg_temp.eq_txt(payload ->> 'Línea de productos'),
           NULL,
           pg_temp.eq_txt(payload ->> 'Tipo de artículo'),
           pg_temp.eq_txt(payload ->> 'Grpo'),
           NULL,
           pg_temp.eq_txt(payload ->> 'Estado'),
           NULL
    FROM eq_estructura
    WHERE pg_temp.eq_txt(payload ->> 'Artículo Matriz') IS NOT NULL
      AND pg_temp.eq_txt(payload ->> 'Artículo Componente') IS NOT NULL

    UNION ALL
    -- As component: a description and a unit, nothing else.
    SELECT pg_temp.eq_txt(payload ->> 'Artículo Componente'), 4,
           row_number() OVER (ORDER BY finished_at DESC, sheet_name, source_row),
           pg_temp.eq_txt(payload ->> 'Descripción Componente'),
           NULL,
           pg_temp.eq_txt(payload ->> 'UMCOMP'),
           NULL, NULL, NULL, NULL, NULL, NULL, NULL
    FROM eq_estructura
    WHERE pg_temp.eq_txt(payload ->> 'Artículo Matriz') IS NOT NULL
      AND pg_temp.eq_txt(payload ->> 'Artículo Componente') IS NOT NULL

    UNION ALL
    SELECT numero_articulo, 5, source_row,
           descripcion, NULL, unidad_de_medida, linea_de_productos,
           NULL, NULL, NULL, NULL, NULL, NULL
    FROM eq_element_deco

    UNION ALL
    SELECT numero_articulo, 6, source_row,
           descripcion, NULL, unidad_de_medida, linea_de_productos,
           NULL, tipo_de_articulo, grpo, NULL, NULL, NULL
    FROM eq_clasif_poly;

    DELETE FROM eq_articulo_dato WHERE numero_articulo IS NULL;
    """,
)

# Three things are decided here that no export states.
#
# The product line of a decoration component comes from column LINEA DE
# PRODUCTO of sheet Datos of ESTRUCTURA DECORADO, which the workbook looks up in
# the item master, and it overrides every other source: the decoration rule
# depends on it.
#
# Polarity is derived. For a paste: the type the PASTA sheet gives it
# (POSITVO, as QAD misspells it, or NEGATIVO), otherwise its description. For a
# plate: the sign in its description, otherwise its type. The fictitious paste
# and plate of imported batteries (PASTA BAT IMPO, PLACA PARA BAT IMPO) are
# positive: the workbook's author put them in the positive column, and whether
# that is right is an open question for the business.
ITEM_MASTER: Final = Step(
    "the item master, one attribute at a time",
    """
    CREATE TEMP TABLE eq_articulo ON COMMIT DROP AS
    WITH elegido AS (
        SELECT numero_articulo,
               (array_agg(descripcion ORDER BY prioridad, orden)
                    FILTER (WHERE descripcion IS NOT NULL))[1] AS descripcion,
               (array_agg(descripcion2 ORDER BY prioridad, orden)
                    FILTER (WHERE descripcion2 IS NOT NULL))[1] AS descripcion2,
               (array_agg(unidad_de_medida ORDER BY prioridad, orden)
                    FILTER (WHERE unidad_de_medida IS NOT NULL))[1] AS unidad_de_medida,
               (array_agg(linea_de_productos ORDER BY prioridad, orden)
                    FILTER (WHERE linea_de_productos IS NOT NULL))[1] AS linea_de_productos,
               (array_agg(campo_equs ORDER BY prioridad, orden)
                    FILTER (WHERE campo_equs IS NOT NULL))[1] AS campo_equs,
               (array_agg(tipo_de_articulo ORDER BY prioridad, orden)
                    FILTER (WHERE tipo_de_articulo IS NOT NULL))[1] AS tipo_de_articulo,
               (array_agg(grpo ORDER BY prioridad, orden)
                    FILTER (WHERE grpo IS NOT NULL))[1] AS grpo,
               (array_agg(peso_neto ORDER BY prioridad, orden)
                    FILTER (WHERE peso_neto IS NOT NULL))[1] AS peso_neto,
               (array_agg(estado ORDER BY prioridad, orden)
                    FILTER (WHERE estado IS NOT NULL))[1] AS estado,
               (array_agg(familia_placa ORDER BY prioridad, orden)
                    FILTER (WHERE familia_placa IS NOT NULL))[1] AS familia_placa
        FROM eq_articulo_dato
        GROUP BY numero_articulo
    ),
    linea_de_componente AS (
        SELECT pg_temp.eq_txt(payload ->> 'Artículo Componente') AS numero_articulo,
               mode() WITHIN GROUP (ORDER BY pg_temp.eq_txt(payload ->> 'LINEA DE PRODUCTO'))
                   AS linea_de_productos
        FROM eq_estructura
        WHERE sheet_name = 'Datos'
          AND pg_temp.eq_txt(payload ->> 'LINEA DE PRODUCTO') ~ '^[0-9]+$'
        GROUP BY 1
    ),
    tipo_de_pasta AS (
        SELECT pg_temp.eq_txt(payload ->> 'Artículo Matriz') AS numero_articulo,
               max(upper(pg_temp.eq_txt(payload ->> 'Tipo de artículo'))) AS tipo
        FROM eq_estructura
        WHERE sheet_name = 'PASTA'
          AND upper(pg_temp.eq_txt(payload ->> 'Tipo de artículo'))
              IN ('POSITVO', 'POSITIVO', 'NEGATIVO')
        GROUP BY 1
    )
    SELECT elegido.numero_articulo,
           coalesce(elegido.descripcion, '(sin descripción)') AS descripcion,
           elegido.descripcion IS NULL AS sin_descripcion,
           elegido.descripcion2,
           elegido.unidad_de_medida,
           coalesce(linea_de_componente.linea_de_productos, elegido.linea_de_productos)
               AS linea_de_productos,
           elegido.campo_equs,
           elegido.tipo_de_articulo,
           elegido.grpo,
           elegido.familia_placa,
           elegido.peso_neto,
           CASE
               WHEN elegido.descripcion LIKE 'PASTA%' THEN
                   CASE
                       WHEN tipo_de_pasta.tipo IN ('POSITVO', 'POSITIVO') THEN '+'
                       WHEN tipo_de_pasta.tipo = 'NEGATIVO' THEN '-'
                       WHEN elegido.descripcion LIKE '%NEG%' THEN '-'
                       ELSE '+'
                   END
               WHEN elegido.descripcion LIKE 'PLACA%' THEN
                   CASE
                       WHEN elegido.descripcion LIKE '%(+)%' THEN '+'
                       WHEN elegido.descripcion LIKE '%(-)%' THEN '-'
                       WHEN upper(elegido.tipo_de_articulo) IN ('POSITVO', 'POSITIVO') THEN '+'
                       WHEN upper(elegido.tipo_de_articulo) = 'NEGATIVO' THEN '-'
                       WHEN elegido.descripcion LIKE 'PLACA PARA BAT IMPO%' THEN '+'
                   END
           END AS polaridad,
           elegido.estado
    FROM elegido
    LEFT JOIN linea_de_componente USING (numero_articulo)
    LEFT JOIN tipo_de_pasta USING (numero_articulo);
    """,
)

# -----------------------------------------------------------------------------
# Silver catalogues
# -----------------------------------------------------------------------------
# Catalogues are upserted and never deleted: a movement landed last year may
# still point at a line or a customer that today's exports no longer mention.
#
# Three columns are kept by hand once the build has created the row - the name
# of a plant area, the name of a warehouse and the description of a category -
# so the build fills them only when they are empty.
CATALOGUES: Final = Step(
    "catalogues: areas, warehouses, product lines, categories, customers, lines",
    """
    INSERT INTO silver.planta (planta, nombre)
    SELECT DISTINCT planta, planta FROM eq_linea_planta WHERE planta IS NOT NULL
    ON CONFLICT (planta) DO NOTHING;

    INSERT INTO silver.almacen (almac, nombre)
    SELECT almac,
           CASE almac
               WHEN '1010' THEN 'Componentes plásticos'
               WHEN '1515' THEN 'Planta de baterías'
               WHEN '1520' THEN 'Reciclaje de plomo'
               WHEN '1521' THEN 'Reciclaje de plomo'
           END
    FROM (
        SELECT almac FROM eq_rct WHERE almac IS NOT NULL
        UNION
        SELECT almac FROM eq_linea_catalogo WHERE almac IS NOT NULL
    ) AS visto
    ON CONFLICT (almac) DO UPDATE
        SET nombre = coalesce(silver.almacen.nombre, EXCLUDED.nombre);

    -- The family table of the Item_Master workbook, when it has been landed.
    -- Its header texts are assumed, like those of the item sheet.
    INSERT INTO silver.grupo_fam (familia_placa, tipo_bat, familia)
    SELECT DISTINCT ON (familia_placa) familia_placa, tipo_bat, familia
    FROM (
        SELECT pg_temp.eq_txt(payload ->> 'Familia Placa') AS familia_placa,
               pg_temp.eq_txt(payload ->> 'Tipo Bat') AS tipo_bat,
               pg_temp.eq_txt(payload ->> 'Familia') AS familia,
               source_row
        FROM eq_maestro WHERE sheet_name = 'Grupo-Fam'
    ) AS fila
    WHERE familia_placa IS NOT NULL
    ORDER BY familia_placa, source_row
    ON CONFLICT (familia_placa) DO UPDATE
        SET tipo_bat = EXCLUDED.tipo_bat, familia = EXCLUDED.familia;

    INSERT INTO silver.linea_de_productos (linea_de_productos, descripcion)
    SELECT linea_de_productos,
           coalesce((array_agg(descripcion ORDER BY prioridad)
                         FILTER (WHERE descripcion IS NOT NULL))[1],
                    '(sin descripción)')
    FROM (
        SELECT linea_de_productos, descripcion_linea AS descripcion, 1 AS prioridad FROM eq_rct
        UNION ALL
        SELECT linea_de_productos, descripcion_linea, 2 FROM eq_factura
        UNION ALL
        SELECT linea_de_productos, descripcion_linea, 3 FROM eq_element_deco
        UNION ALL
        SELECT linea_de_productos, descripcion_linea, 4 FROM eq_clasif_poly
        UNION ALL
        SELECT linea_de_productos, NULL, 5 FROM eq_articulo
    ) AS visto
    WHERE linea_de_productos IS NOT NULL
    GROUP BY linea_de_productos
    ON CONFLICT (linea_de_productos) DO UPDATE SET descripcion = EXCLUDED.descripcion;

    -- "N/A" is a category like any other: it means "not applicable". The
    -- plastic catalogue writes it "NA"; both are kept as delivered.
    INSERT INTO silver.campo_equs (campo_equs)
    SELECT campo_equs FROM eq_rct WHERE campo_equs IS NOT NULL
    UNION
    SELECT campo_equs FROM eq_factura WHERE campo_equs IS NOT NULL
    UNION
    SELECT campo_equs FROM eq_clasif_poly WHERE campo_equs IS NOT NULL
    ON CONFLICT (campo_equs) DO NOTHING;

    -- A country written all in capitals is put in title case, so that COLOMBIA
    -- and Colombia are one country. Mixed case is left as it is.
    INSERT INTO silver.vendido_a (vendido_a, ordenar_nombre, pais, tipo)
    SELECT DISTINCT ON (vendido_a)
           vendido_a,
           ordenar_nombre,
           CASE WHEN pais = upper(pais) THEN initcap(pais) ELSE pais END,
           tipo
    FROM eq_factura
    WHERE vendido_a IS NOT NULL
    ORDER BY vendido_a, fecha_de_la_factura DESC NULLS LAST, num_fact DESC, orden DESC, linea DESC
    ON CONFLICT (vendido_a) DO UPDATE
        SET ordenar_nombre = EXCLUDED.ordenar_nombre,
            pais = EXCLUDED.pais,
            tipo = EXCLUDED.tipo;

    -- Every line of the catalogue, plus any line a receipt names that the
    -- catalogue does not. A line the comparison sheet omits has no plant area.
    INSERT INTO silver.lineas_produccion
        (linea, descripcion, almac, planta, grupo, proceso_energia, unidades_por_hora)
    SELECT linea.linea,
           coalesce(catalogo.descripcion, '(sin descripción)'),
           catalogo.almac,
           planta.planta,
           grupo.grupo,
           planta.proceso_energia,
           catalogo.unidades_por_hora
    FROM (
        SELECT linea FROM eq_linea_catalogo
        UNION
        SELECT linea_produccion FROM eq_rct WHERE linea_produccion IS NOT NULL
    ) AS linea
    LEFT JOIN eq_linea_catalogo AS catalogo USING (linea)
    LEFT JOIN eq_linea_planta AS planta ON planta.linea = linea.linea
    LEFT JOIN eq_linea_grupo AS grupo ON grupo.linea = linea.linea
    ON CONFLICT (linea) DO UPDATE
        SET descripcion = EXCLUDED.descripcion,
            almac = EXCLUDED.almac,
            planta = EXCLUDED.planta,
            grupo = EXCLUDED.grupo,
            proceso_energia = EXCLUDED.proceso_energia,
            unidades_por_hora = EXCLUDED.unidades_por_hora;
    """,
)

ITEMS: Final = Step(
    "item master",
    """
    INSERT INTO silver.item_master AS destino
        (numero_articulo, descripcion, descripcion2, unidad_de_medida, linea_de_productos,
         campo_equs, tipo_de_articulo, grpo, familia_placa, peso_neto, polaridad, estado)
    SELECT articulo.numero_articulo, articulo.descripcion, articulo.descripcion2,
           articulo.unidad_de_medida, articulo.linea_de_productos, articulo.campo_equs,
           articulo.tipo_de_articulo, articulo.grpo,
           -- Kept only when the family table knows the family.
           familia.familia_placa,
           articulo.peso_neto, articulo.polaridad, articulo.estado
    FROM eq_articulo AS articulo
    LEFT JOIN silver.grupo_fam AS familia ON familia.familia_placa = articulo.familia_placa
    ORDER BY articulo.numero_articulo
    ON CONFLICT (numero_articulo) DO UPDATE
        SET descripcion = EXCLUDED.descripcion,
            descripcion2 = EXCLUDED.descripcion2,
            unidad_de_medida = EXCLUDED.unidad_de_medida,
            linea_de_productos = EXCLUDED.linea_de_productos,
            campo_equs = EXCLUDED.campo_equs,
            tipo_de_articulo = EXCLUDED.tipo_de_articulo,
            grpo = EXCLUDED.grpo,
            familia_placa = EXCLUDED.familia_placa,
            peso_neto = EXCLUDED.peso_neto,
            polaridad = EXCLUDED.polaridad,
            estado = EXCLUDED.estado
        WHERE destino IS DISTINCT FROM EXCLUDED;
    """,
)

# -----------------------------------------------------------------------------
# Silver structures, and the catalogues of the calculation
# -----------------------------------------------------------------------------
# These are snapshots, so they are replaced. The structure is emptied with its
# counter restarted and filled in a fixed order, which is what makes two builds
# of the same Bronze give row-for-row identical tables.
SNAPSHOTS: Final = Step(
    "product structure, decoration catalogue and plastic factors",
    """
    DELETE FROM silver.element_deco;
    INSERT INTO silver.element_deco (numero_articulo, grpo, tipo_de_articulo, elemento_deco)
    SELECT numero_articulo, grpo, tipo_de_articulo, elemento_deco
    FROM eq_element_deco
    ORDER BY numero_articulo;

    DELETE FROM silver.clasif_poly;
    INSERT INTO silver.clasif_poly (numero_articulo, campo_equs, cuenta_stats, factor_equ)
    SELECT numero_articulo, campo_equs, cuenta_stats, factor_equ
    FROM eq_clasif_poly
    ORDER BY numero_articulo;

    TRUNCATE silver.bom_estructura RESTART IDENTITY;
    INSERT INTO silver.bom_estructura
        (articulo_matriz, articulo_componente, cantidad_por, umcomp, inicio_efectiva,
         fin_efectivo, desp, operacion, referencia, fecha_modificada, id_del_usuario,
         calculo, source_sheet, source_run_id)
    SELECT articulo_matriz, articulo_componente, cantidad_por, umcomp, inicio_efectiva,
           fin_efectivo, desp, operacion, referencia, fecha_modificada, id_del_usuario,
           calculo, sheet_name, ingestion_run_id
    FROM eq_bom
    WHERE copia = 1
    ORDER BY sheet_name, source_row;
    """,
)

# -----------------------------------------------------------------------------
# Silver movements
# -----------------------------------------------------------------------------
# Upserted, never deleted: a receipt stays in Silver even after retention has
# removed its Bronze row. A row that has not changed is not rewritten, so
# building again costs a comparison and leaves no dead rows behind.
MOVEMENT_TABLES: Final = Step(
    "production receipts and invoice lines",
    """
    INSERT INTO silver.reporte_qad AS destino
        (numero_de_transaccion, tipo_transaccion, fecha_efectiva, fecha, tiempo,
         numero_articulo, almac, ubicacion, lote_serie, orden, id, linea_produccion,
         cambiar_cantidad_de_ubic, unidad_de_medida, precio, costo_total, campo_equs,
         peso_neto, status_inventario, program, id_del_usuario, source_run_id)
    SELECT numero_de_transaccion, tipo_transaccion, fecha_efectiva, fecha, tiempo,
           numero_articulo, almac, ubicacion, lote_serie, orden, id, linea_produccion,
           cambiar_cantidad_de_ubic, unidad_de_medida, precio, costo_total, campo_equs,
           peso_neto, status_inventario, program, id_del_usuario, source_run_id
    FROM eq_rct
    ORDER BY numero_de_transaccion
    ON CONFLICT (numero_de_transaccion) DO UPDATE
        SET tipo_transaccion = EXCLUDED.tipo_transaccion,
            fecha_efectiva = EXCLUDED.fecha_efectiva,
            fecha = EXCLUDED.fecha,
            tiempo = EXCLUDED.tiempo,
            numero_articulo = EXCLUDED.numero_articulo,
            almac = EXCLUDED.almac,
            ubicacion = EXCLUDED.ubicacion,
            lote_serie = EXCLUDED.lote_serie,
            orden = EXCLUDED.orden,
            id = EXCLUDED.id,
            linea_produccion = EXCLUDED.linea_produccion,
            cambiar_cantidad_de_ubic = EXCLUDED.cambiar_cantidad_de_ubic,
            unidad_de_medida = EXCLUDED.unidad_de_medida,
            precio = EXCLUDED.precio,
            costo_total = EXCLUDED.costo_total,
            campo_equs = EXCLUDED.campo_equs,
            peso_neto = EXCLUDED.peso_neto,
            status_inventario = EXCLUDED.status_inventario,
            program = EXCLUDED.program,
            id_del_usuario = EXCLUDED.id_del_usuario,
            source_run_id = EXCLUDED.source_run_id
        WHERE destino IS DISTINCT FROM EXCLUDED;

    INSERT INTO silver.unit_ship AS destino
        (num_fact, orden, linea, remision, canal, fecha_de_la_factura, fecha_efectiva,
         vendido_a, embarcar_para, numero_articulo, cantidad_fac, precio_de_lista,
         porcentaje_descuento, precio_neto, monto_factura, moneda, tipos_de_cambio,
         importe_de_factura_mb, total_plomo, campo_equs, source_run_id)
    SELECT num_fact, orden, linea, remision, canal, fecha_de_la_factura, fecha_efectiva,
           vendido_a, embarcar_para, numero_articulo, cantidad_fac, precio_de_lista,
           porcentaje_descuento, precio_neto, monto_factura, moneda, tipos_de_cambio,
           importe_de_factura_mb, total_plomo, campo_equs, source_run_id
    FROM eq_factura
    ORDER BY num_fact, orden, linea
    ON CONFLICT (num_fact, orden, linea) DO UPDATE
        SET remision = EXCLUDED.remision,
            canal = EXCLUDED.canal,
            fecha_de_la_factura = EXCLUDED.fecha_de_la_factura,
            fecha_efectiva = EXCLUDED.fecha_efectiva,
            vendido_a = EXCLUDED.vendido_a,
            embarcar_para = EXCLUDED.embarcar_para,
            numero_articulo = EXCLUDED.numero_articulo,
            cantidad_fac = EXCLUDED.cantidad_fac,
            precio_de_lista = EXCLUDED.precio_de_lista,
            porcentaje_descuento = EXCLUDED.porcentaje_descuento,
            precio_neto = EXCLUDED.precio_neto,
            monto_factura = EXCLUDED.monto_factura,
            moneda = EXCLUDED.moneda,
            tipos_de_cambio = EXCLUDED.tipos_de_cambio,
            importe_de_factura_mb = EXCLUDED.importe_de_factura_mb,
            total_plomo = EXCLUDED.total_plomo,
            campo_equs = EXCLUDED.campo_equs,
            source_run_id = EXCLUDED.source_run_id
        WHERE destino IS DISTINCT FROM EXCLUDED;
    """,
)

# One row per change of the standard cost of an item, read off the receipts in
# the order they were accounted for. A cost ends the day the next one starts.
# When one day shows two prices, the last receipt of the day decides.
STANDARD_COST: Final = Step(
    "standard cost history",
    """
    DELETE FROM silver.costo_std;
    INSERT INTO silver.costo_std (numero_articulo, vigente_desde, vigente_hasta, costo_std)
    WITH por_dia AS (
        SELECT DISTINCT ON (numero_articulo, fecha_efectiva)
               numero_articulo, fecha_efectiva, precio
        FROM silver.reporte_qad
        WHERE precio IS NOT NULL
        ORDER BY numero_articulo, fecha_efectiva, numero_de_transaccion DESC
    ),
    marcado AS (
        SELECT *, lag(precio) OVER (PARTITION BY numero_articulo ORDER BY fecha_efectiva) AS previo
        FROM por_dia
    )
    SELECT numero_articulo,
           fecha_efectiva,
           lead(fecha_efectiva) OVER (PARTITION BY numero_articulo ORDER BY fecha_efectiva),
           precio
    FROM marcado
    WHERE previo IS DISTINCT FROM precio
    ORDER BY numero_articulo, fecha_efectiva;
    """,
)

BUILD_STEPS: Final[tuple[Step, ...]] = (
    FUNCTIONS,
    LATEST_RUNS,
    STRUCTURE_ROWS,
    STRUCTURE_LINKS,
    MOVEMENTS,
    CATALOGUE_SHEETS,
    ITEM_SOURCES,
    ITEM_MASTER,
    CATALOGUES,
    ITEMS,
    SNAPSHOTS,
    MOVEMENT_TABLES,
    STANDARD_COST,
)

# -----------------------------------------------------------------------------
# The two conversion tables
# -----------------------------------------------------------------------------
# CONCURRENTLY lets readers keep using a view while it is rebuilt. PostgreSQL
# refuses it in some situations, so the adapter falls back to the plain form.
REFRESH_VIEWS_CONCURRENTLY: Final[tuple[str, ...]] = (
    "REFRESH MATERIALIZED VIEW CONCURRENTLY silver.td_form",
    "REFRESH MATERIALIZED VIEW CONCURRENTLY silver.td_deco",
)
REFRESH_VIEWS: Final[tuple[str, ...]] = (
    "REFRESH MATERIALIZED VIEW silver.td_form",
    "REFRESH MATERIALIZED VIEW silver.td_deco",
)

# -----------------------------------------------------------------------------
# The data-quality report
# -----------------------------------------------------------------------------
SILVER_TABLES: Final[tuple[str, ...]] = (
    "planta",
    "almacen",
    "lineas_produccion",
    "linea_de_productos",
    "campo_equs",
    "grupo_fam",
    "item_master",
    "costo_std",
    "vendido_a",
    "bom_estructura",
    "element_deco",
    "clasif_poly",
    "costo_conversion",
    "reporte_qad",
    "unit_ship",
    "equs_version",
    "factores_equ",
    "instrucc",
    "td_form",
    "td_deco",
)

# Whether there is anything to build from. One row: sheets, datasets, receipts.
REPORT_SOURCES: Final = """
    SELECT (SELECT count(*) FROM eq_corrida_estructura),
           (SELECT count(*) FROM eq_corrida_manual),
           (SELECT count(*) FROM eq_rct),
           (SELECT count(*) FROM eq_factura),
           (SELECT count(*) FROM eq_maestro),
           (SELECT count(*) FROM eq_manual WHERE dataset_key = 'l_produccion')
"""

# Per structure sheet: rows read, pasted header rows, rows that are not a link,
# repeats inside the sheet, links another sheet also has, rows read by a pasted
# header, and rows that reached Silver.
REPORT_STRUCTURE: Final = """
    SELECT hoja.sheet_name,
           (SELECT count(*) FROM eq_estructura_cruda AS c WHERE c.sheet_name = hoja.sheet_name),
           (SELECT count(*) FROM eq_cabecera AS c WHERE c.sheet_name = hoja.sheet_name),
           (SELECT count(*) FROM eq_enlace AS e
            WHERE e.sheet_name = hoja.sheet_name
              AND (e.articulo_matriz IS NULL OR e.articulo_componente IS NULL
                   OR e.cantidad_por IS NULL)),
           (SELECT count(*) FROM eq_bom AS b
            WHERE b.sheet_name = hoja.sheet_name AND b.copia_en_hoja > 1),
           (SELECT count(*) FROM eq_bom AS b
            WHERE b.sheet_name = hoja.sheet_name AND b.copia_en_hoja = 1 AND b.copia > 1),
           (SELECT count(*) FROM eq_estructura AS e
            WHERE e.sheet_name = hoja.sheet_name AND e.releida),
           (SELECT count(*) FROM eq_bom AS b WHERE b.sheet_name = hoja.sheet_name AND b.copia = 1)
    FROM eq_corrida_estructura AS hoja
    ORDER BY hoja.sheet_name
"""

# Movements that could not become a row: receipts, then invoice lines.
REPORT_MOVEMENTS_LEFT_OUT: Final = """
    SELECT recibos, renglones FROM eq_excluido
"""

REPORT_ITEMS_WITHOUT_DESCRIPTION: Final = """
    SELECT numero_articulo FROM eq_articulo WHERE sin_descripcion ORDER BY numero_articulo
"""

REPORT_PLACEHOLDERS: Final = """
    SELECT 'linea_de_productos', count(*) FROM silver.linea_de_productos
    WHERE descripcion = '(sin descripción)'
    UNION ALL
    SELECT 'lineas_produccion', count(*) FROM silver.lineas_produccion
    WHERE descripcion = '(sin descripción)'
    UNION ALL
    SELECT 'item_master', count(*) FROM silver.item_master
    WHERE descripcion = '(sin descripción)'
    UNION ALL
    SELECT 'vendido_a', count(*) FROM silver.vendido_a WHERE ordenar_nombre IS NULL
"""

REPORT_LINES_WITHOUT_PLANT: Final = """
    SELECT linea.linea,
           (SELECT count(*) FROM silver.reporte_qad AS r WHERE r.linea_produccion = linea.linea)
    FROM silver.lineas_produccion AS linea
    WHERE linea.planta IS NULL
    ORDER BY 2 DESC, 1
"""

# Each item of the plastic catalogue against the factor its category has in the
# side table. Reported, never corrected: which of the two is right is a
# business decision.
REPORT_POLY_MISMATCHES: Final = """
    SELECT item.numero_articulo, item.campo_equs, item.factor_equ, categoria.factor_equ
    FROM eq_clasif_poly AS item
    LEFT JOIN eq_poly_categoria AS categoria USING (campo_equs)
    WHERE item.factor_equ IS DISTINCT FROM categoria.factor_equ
    ORDER BY item.numero_articulo
"""

REPORT_POLY_REPEATED: Final = """
    SELECT numero_articulo, count(*) FROM eq_clasif_poly_fila
    GROUP BY numero_articulo HAVING count(*) > 1 ORDER BY 1
"""

# Formed batteries (product line 3101) that were produced but contribute no
# active material: no row in td_form, or zero positive paste.
REPORT_FORMED_WITHOUT_PASTE: Final = """
    SELECT recibo.numero_articulo, sum(recibo.cambiar_cantidad_de_ubic), count(*)
    FROM silver.reporte_qad AS recibo
    JOIN silver.item_master AS articulo USING (numero_articulo)
    LEFT JOIN silver.td_form AS pasta ON pasta.articulo_matriz = recibo.numero_articulo
    WHERE articulo.linea_de_productos = '3101'
      AND coalesce(pasta.pasta_pos, 0) = 0
    GROUP BY recibo.numero_articulo
    ORDER BY 2 DESC, 1
"""

# Formed batteries whose paste adds up more than one assembled battery. A
# battery is formed from one, so two usually means an old link that was closed
# and a new one that replaced it, both counted. Sheet BAT FOR marks the one that
# counts with CALCULO = SI and leaves the other blank, and a blank counts as SI.
# That is the rule as specified, and it is what the documented targets contain;
# the workbook itself counts only the marked row. Reported so that someone
# decides.
REPORT_FORMED_WITH_SEVERAL_ASSEMBLED: Final = """
    WITH placa AS (
        SELECT DISTINCT enlace.articulo_matriz
        FROM silver.bom_estructura AS enlace
        JOIN silver.item_master AS pasta ON pasta.numero_articulo = enlace.articulo_componente
        WHERE pasta.descripcion LIKE 'PASTA%'
    ),
    ensamblada AS (
        SELECT DISTINCT enlace.articulo_matriz
        FROM silver.bom_estructura AS enlace
        JOIN placa ON placa.articulo_matriz = enlace.articulo_componente
    )
    SELECT enlace.articulo_matriz,
           count(*),
           (SELECT coalesce(sum(recibo.cambiar_cantidad_de_ubic), 0)
            FROM silver.reporte_qad AS recibo
            WHERE recibo.numero_articulo = enlace.articulo_matriz)
    FROM silver.bom_estructura AS enlace
    JOIN ensamblada ON ensamblada.articulo_matriz = enlace.articulo_componente
    JOIN silver.td_form AS pasta ON pasta.articulo_matriz = enlace.articulo_matriz
    WHERE enlace.calculo = 'SI'
    GROUP BY enlace.articulo_matriz
    HAVING count(*) > 1
    ORDER BY 3 DESC, 1
"""

REPORT_DECORATED_WITHOUT_PIECES: Final = """
    SELECT recibo.numero_articulo, sum(recibo.cambiar_cantidad_de_ubic), count(*)
    FROM silver.reporte_qad AS recibo
    JOIN silver.item_master AS articulo USING (numero_articulo)
    LEFT JOIN silver.td_deco AS piezas ON piezas.articulo_matriz = recibo.numero_articulo
    WHERE articulo.linea_de_productos IN ('3102', '3103', '3104')
      AND piezas.articulo_matriz IS NULL
    GROUP BY recibo.numero_articulo
    ORDER BY 2 DESC, 1
"""

# Items QAD marks "N/A" although the plastic catalogue gives them a category.
# The catalogue's own "not applicable" is spelled "NA".
REPORT_NA_WITH_POLY_CATEGORY: Final = """
    SELECT recibo.numero_articulo, poly.campo_equs, count(*)
    FROM silver.reporte_qad AS recibo
    JOIN silver.clasif_poly AS poly USING (numero_articulo)
    WHERE recibo.campo_equs = 'N/A'
      AND poly.campo_equs IS NOT NULL
      AND poly.campo_equs NOT IN ('N/A', 'NA')
    GROUP BY recibo.numero_articulo, poly.campo_equs
    ORDER BY 3 DESC, 1
"""

# Header texts found in a landed Item_Master among the ones the build reads.
REPORT_ITEM_MASTER_KEYS: Final = """
    SELECT count(*) FROM eq_maestro
    WHERE sheet_name = 'Datos' AND payload ? 'Número Artículo'
"""

__all__ = [
    "BUILD_STEPS",
    "REFRESH_VIEWS",
    "REFRESH_VIEWS_CONCURRENTLY",
    "REPORT_DECORATED_WITHOUT_PIECES",
    "REPORT_FORMED_WITHOUT_PASTE",
    "REPORT_FORMED_WITH_SEVERAL_ASSEMBLED",
    "REPORT_ITEMS_WITHOUT_DESCRIPTION",
    "REPORT_ITEM_MASTER_KEYS",
    "REPORT_LINES_WITHOUT_PLANT",
    "REPORT_MOVEMENTS_LEFT_OUT",
    "REPORT_NA_WITH_POLY_CATEGORY",
    "REPORT_PLACEHOLDERS",
    "REPORT_POLY_MISMATCHES",
    "REPORT_POLY_REPEATED",
    "REPORT_SOURCES",
    "REPORT_STRUCTURE",
    "SILVER_TABLES",
    "SIN_DESCRIPCION",
    "Step",
]
