"""Barras en pequeños múltiplos: un recuadro por cada nivel de una variable
de segmentación, y dentro de cada uno la distribución de otra categórica.

Es la alternativa a las barras agrupadas de ``fancy_barplot`` cuando
cualquiera de las dos variables tiene más de unos pocos niveles y la
versión agrupada deja de leerse. Cada segmento recibe su propio recuadro,
así que las distribuciones se comparan como formas y no como alturas.

Por defecto los recuadros muestran **porcentaje dentro del segmento**
(cada panel suma 100%), al revés que ``fancy_bars``, que trae ``pct`` en
False. La razón es que los segmentos casi nunca tienen el mismo tamaño
--label reparte 5.608 contra 555-- y con conteos crudos el panel pequeño
queda aplastado contra el eje.

El color se asigna **por categoría**, no por frecuencia. ``fancy_bars``
pinta colors[0], colors[1]... en orden de frecuencia, de modo que llamarlo
una vez por panel le daría a la misma categoría colores distintos en cada
recuadro cuando el orden cambia entre segmentos. Aquí el orden y el mapa
de colores se calculan una sola vez sobre el total y se reutilizan.
"""

from math import ceil
from typing import Literal

import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd
import seaborn as sns

INK, TXT, GRID = "#1F2E3D", "#4A4A4A", "#E2E8F0"
PALETTE = [
    "#5BA7DE",
    "#E28585",
    "#7FC4A0",
    "#F0B26B",
    "#9B8FC7",
    "#96A5A5",
    "#6FC3D0",
    "#D98CB3",
]
YLAB = {False: "Conteo", True: "Porcentaje"}

Labels = Literal["none", "count", "pct", "both"]
Norm = Literal["segmento", "barra", "all"]

#: Más paneles que esto casi siempre significa que se pasó una variable
#: continua como segmento.
MAX_PANELES = 20


def _pretty(s: str) -> str:
    return s.replace("_", " ").title()


def _colors(n: int, palette=None):
    if isinstance(palette, str):
        return sns.color_palette(palette, n)
    pal = palette or PALETTE
    return list(pal[:n]) if n <= len(pal) else sns.husl_palette(n, s=0.55, l=0.68)


def _bar_labels(ax, container, counts, pcts, labels: Labels, fontsize=9):
    """Anota una serie de barras con conteos y/o porcentajes."""
    if labels == "none":
        return
    tpl = {
        "count": "{c:,.0f}",
        "pct": "{p:.1f}%",
        "both": "{c:,.0f}\n({p:.1f}%)",
    }[labels]
    txt = ["" if c == 0 else tpl.format(c=c, p=p) for c, p in zip(counts, pcts)]
    ax.bar_label(
        container,
        labels=txt,
        padding=4,
        fontsize=fontsize,
        fontweight="bold",
        color=INK,
    )


def _style(ax, title, xlabel, ylabel, pct, rot, headroom, titlesize=18):
    """Aplica el estilo visual compartido por todos los gráficos.

    titlesize : 18 en un gráfico suelto; en una rejilla el título de cada
                recuadro necesita ser bastante más pequeño.
    """
    ax.set_facecolor("white")
    ax.set_title(
        title,
        loc="left",
        pad=12,
        fontsize=titlesize,
        fontweight="bold",
        color=INK,
    )
    ax.set_xlabel(xlabel, fontsize=12, color=TXT, labelpad=12)
    ax.set_ylabel(ylabel, fontsize=12, color=TXT, labelpad=12)
    ax.yaxis.set_major_formatter(
        mtick.PercentFormatter(decimals=0)
        if pct
        else mtick.FuncFormatter(lambda v, _: f"{v / 1000:.1f}k" if v >= 1000 else f"{v:.0f}")
    )
    ax.tick_params(axis="x", rotation=rot, labelsize=10, colors=TXT, length=0)
    ax.tick_params(axis="y", labelsize=10, colors=TXT, length=0)
    if rot:
        plt.setp(ax.get_xticklabels(), ha="right", rotation_mode="anchor")
    ax.set_axisbelow(True)
    ax.yaxis.grid(True, linestyle="--", color=GRID, linewidth=1.0)
    ax.xaxis.grid(False)
    ax.margins(y=headroom)
    sns.despine(ax=ax, left=True, bottom=True)


def _validate(df, x, segment, labels, norm):
    """Comprueba argumentos literales y nombres de columna."""
    if labels not in {"none", "count", "pct", "both"}:
        raise ValueError("labels debe ser: 'none', 'count', 'pct' o 'both'")
    if norm not in {"segmento", "barra", "all"}:
        raise ValueError("norm debe ser: 'segmento', 'barra' o 'all'")
    for col in (x, segment):
        if col not in df.columns:
            raise ValueError(f"'{col}' no está en el DataFrame")
    if x == segment:
        raise ValueError(f"'{x}' no puede ser a la vez la barra y el segmento")


def _grid(n_paneles, axes, ncols, figsize, sharey):
    """Devuelve (axes_planos, creada) para n_paneles recuadros."""
    if axes is not None:
        planos = np.atleast_1d(np.asarray(axes)).ravel()
        if len(planos) < n_paneles:
            raise ValueError(f"se necesitan {n_paneles} ejes y se recibieron {len(planos)}")
        return planos, False
    ncols = max(1, min(ncols, n_paneles))
    nrows = ceil(n_paneles / ncols)
    fig, ejes = plt.subplots(
        nrows,
        ncols,
        figsize=figsize or (4.5 * ncols, 4.0 * nrows),
        sharey=sharey,
        squeeze=False,
    )
    return ejes.ravel(), True


def segmented_bars(
    df: pd.DataFrame,
    x: str,
    segment: str,
    pct: bool = True,
    norm: Norm = "segmento",
    labels: Labels = "none",
    title: str | None = None,
    order: list | None = None,
    segment_order: list | None = None,
    palette=None,
    axes=None,
    figsize: tuple[float, float] | None = None,
    rot: float = 0,
    ncols: int = 3,
    sharey: bool = True,
    top: int | None = None,
    dropna: bool = True,
) -> np.ndarray:
    """Distribución de una categórica, un recuadro por cada segmento.

    x             : variable que forma las barras dentro de cada recuadro
    segment       : variable de segmentación; un recuadro por nivel
    pct           : True -> porcentajes (default) | False -> conteos
    norm          : base del porcentaje -> "segmento" (cada recuadro suma
                    100%), "barra" (cada categoría suma 100% entre
                    recuadros) o "all" (sobre el total)
    labels        : "none" | "count" | "pct" | "both"
    order         : orden de las barras (default: de mayor a menor sobre
                    el total, no dentro de cada recuadro)
    segment_order : orden y selección de los recuadros
    palette       : nombre de paleta seaborn o lista de colores
    axes          : rejilla de ejes ya creada; si es None se crea una
    ncols         : recuadros por fila cuando se crea la rejilla
    top           : mostrar solo las N categorías más frecuentes
    dropna        : descarta nulos en ambas variables (default True)

    El orden de las barras y el color de cada categoría se calculan una
    sola vez sobre el total, así que una categoría ocupa la misma
    posición y el mismo color en todos los recuadros, incluso donde su
    conteo es cero.

    Retorna el array de ejes; ``axes.flat[0].figure`` da la figura.
    """
    _validate(df, x, segment, labels, norm)

    # reset_index: un DataFrame concatenado trae índices repetidos y
    # pd.crosstab no puede reindexar sobre etiquetas duplicadas.
    datos = df[[x, segment]].reset_index(drop=True)
    datos = datos.dropna() if dropna else datos.fillna("Sin datos")
    if datos.empty:
        raise ValueError(f"no quedan filas con '{x}' y '{segment}' no nulos")

    tabla = pd.crosstab(datos[x], datos[segment])

    cats = (
        list(order) if order is not None else list(tabla.sum(1).sort_values(ascending=False).index)
    )
    if top:
        cats = cats[:top]
    niveles = (
        list(segment_order)
        if segment_order is not None
        else list(tabla.sum(0).sort_values(ascending=False).index)
    )
    tabla = tabla.reindex(index=cats, columns=niveles).fillna(0)

    if len(niveles) > MAX_PANELES:
        raise ValueError(
            f"'{segment}' tiene {len(niveles)} niveles y saldrían otros "
            f"tantos recuadros (máximo {MAX_PANELES}). Usa segment_order "
            f"para elegir cuáles, o agrupa la variable antes de graficar"
        )

    denom = {
        "segmento": tabla.sum(0).to_numpy()[None, :],
        "barra": tabla.sum(1).to_numpy()[:, None],
        "all": tabla.to_numpy().sum(),
    }[norm]
    with np.errstate(invalid="ignore", divide="ignore"):
        pcts = tabla / denom * 100
    pcts = pcts.fillna(0.0)

    # Un color por categoría, fijado sobre el orden global: es lo que
    # mantiene el color estable de un recuadro a otro.
    color_de = dict(zip(cats, _colors(len(cats), palette)))

    planos, creada = _grid(len(niveles), axes, ncols, figsize, sharey)
    etiquetas = [str(c) for c in cats]
    ncols_real = ncols if creada else len(planos)
    headroom = 0.14 if labels != "none" else 0.05

    for i, nivel in enumerate(niveles):
        ax = planos[i]
        alturas = (pcts if pct else tabla)[nivel].to_numpy()
        cont = ax.bar(
            etiquetas,
            alturas,
            width=0.62,
            color=[color_de[c] for c in cats],
        )
        _bar_labels(ax, cont, tabla[nivel].to_numpy(), pcts[nivel].to_numpy(), labels)
        n_nivel = int(tabla[nivel].sum())
        ultima_fila = i >= len(niveles) - ncols_real
        _style(
            ax,
            f"{_pretty(segment)} = {nivel}  (n={n_nivel:,})",
            _pretty(x) if ultima_fila else "",
            YLAB[pct] if i % ncols_real == 0 else "",
            pct,
            rot,
            headroom,
            titlesize=13,
        )

    for ax in planos[len(niveles) :]:
        ax.set_visible(False)

    if creada:
        fig = planos[0].figure
        fig.suptitle(
            title or f"Distribución de {_pretty(x)} por {_pretty(segment)}",
            x=0.02,
            ha="left",
            fontsize=18,
            fontweight="bold",
            color=INK,
        )
        fig.tight_layout()

    return planos[: len(niveles)]
