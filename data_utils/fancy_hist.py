"""Histogramas con el mismo lenguaje visual que ``fancy_barplot``.

La variante numérica de :mod:`utils.fancy_barplot`: mismos colores, mismas
etiquetas y mismo estilo de ejes, pero sobre una variable continua. El único
concepto nuevo es el binning, expuesto como ``n_bins``; si no se indica, se
calcula con la regla de Freedman-Diaconis.
"""

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

#: Rango en el que se acota el número de bins calculado automáticamente.
MIN_BINS, MAX_BINS = 5, 60


def _pretty(s: str) -> str:
    return s.replace("_", " ").title()


def _colors(n: int, palette=None):
    if isinstance(palette, str):
        return sns.color_palette(palette, n)
    pal = palette or PALETTE
    return list(pal[:n]) if n <= len(pal) else sns.husl_palette(n, s=0.55, l=0.68)


def _bar_labels(ax, container, counts, pcts, labels: Labels, fontsize=11):
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


def _style(ax, title, xlabel, ylabel, pct, rot, headroom):
    """Aplica el estilo visual compartido por todos los gráficos."""
    ax.set_facecolor("white")
    ax.set_title(title, loc="left", pad=20, fontsize=18, fontweight="bold", color=INK)
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
    plt.tight_layout()


def _default_bins(x: np.ndarray, max_bins: int = MAX_BINS) -> int:
    """Número de bins por la regla de Freedman-Diaconis, acotado.

    Cuando el rango intercuartílico o el rango total se anulan (columnas
    casi constantes) la regla se indefine, y se cae a la raíz de n.
    """
    q1, q3 = np.percentile(x, [25, 75])
    iqr, rng = q3 - q1, x.max() - x.min()
    if iqr <= 0 or rng <= 0:
        return int(np.clip(np.sqrt(x.size), MIN_BINS, max_bins))
    h = 2.0 * iqr * x.size ** (-1 / 3)
    return int(np.clip(np.ceil(rng / h), MIN_BINS, max_bins))


def _numeric_values(df: pd.DataFrame, x: str) -> np.ndarray:
    """Serie numérica sin nulos ni infinitos, lista para binear."""
    if not pd.api.types.is_numeric_dtype(df[x]):
        raise ValueError(
            f"'{x}' no es numérica: fancy_hist requiere una variable "
            f"continua (dtype actual: {df[x].dtype}). Para categóricas usa "
            f"fancy_bars de utils.fancy_barplot"
        )
    values = pd.to_numeric(df[x], errors="coerce").to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        raise ValueError(f"'{x}' no tiene valores finitos que graficar")
    return values


def _check_args(labels: Labels, n_bins: int | None) -> None:
    """Valida los argumentos comunes a las dos funciones públicas."""
    if labels not in {"none", "count", "pct", "both"}:
        raise ValueError("labels debe ser: 'none', 'count', 'pct' o 'both'")
    if n_bins is not None and (not isinstance(n_bins, int) or n_bins < 1):
        raise ValueError("n_bins debe ser un entero positivo")


def _edges(values: np.ndarray, n_bins: int | None) -> np.ndarray:
    """Bordes de los bins; ``n_bins`` manda sobre el valor calculado."""
    return np.histogram_bin_edges(values, bins=n_bins or _default_bins(values))


def fancy_hist(
    df: pd.DataFrame,
    x: str,
    pct: bool = False,
    labels: Labels = "none",
    title: str | None = None,
    palette=None,
    ax: plt.Axes | None = None,
    figsize: tuple[float, float] = (9, 7),
    rot: float = 0,
    n_bins: int | None = None,
) -> plt.Axes:
    """Distribución de una variable numérica.

    pct     : False -> conteos | True -> porcentajes
    labels  : "none" | "count" | "pct" | "both"
    palette : nombre de paleta seaborn o lista de colores
    n_bins  : número de bins (default: regla de Freedman-Diaconis)

    Los nulos y los infinitos se descartan antes de binear.
    """
    _check_args(labels, n_bins)

    values = _numeric_values(df, x)
    edges = _edges(values, n_bins)
    counts, edges = np.histogram(values, bins=edges)
    pcts = counts / counts.sum() * 100

    centers = (edges[:-1] + edges[1:]) / 2
    widths = np.diff(edges)

    if ax is None:
        _, ax = plt.subplots(figsize=figsize)
    # Un solo color: los bins son una serie ordenada, no categorías sueltas.
    color = _colors(1, palette)[0]
    container = ax.bar(
        centers,
        pcts if pct else counts,
        width=widths * 0.98,
        color=color,
        align="center",
    )
    _bar_labels(ax, container, counts, pcts, labels)
    _style(
        ax,
        title or f"Distribución de {_pretty(x)}",
        _pretty(x),
        YLAB[pct],
        pct,
        rot,
        0.14 if labels != "none" else 0.05,
    )
    return ax


def plot_num_relation(
    df: pd.DataFrame,
    x: str,
    hue: str,
    pct: bool = False,
    norm: Literal["x", "hue", "all"] = "x",
    labels: Labels = "none",
    title: str | None = None,
    hue_order: list | None = None,
    palette=None,
    ax: plt.Axes | None = None,
    figsize: tuple[float, float] = (10, 6),
    rot: float = 0,
    n_bins: int | None = None,
) -> plt.Axes:
    """Distribución de una variable numérica segmentada por una categórica.

    pct  : False -> conteos | True -> porcentajes
    norm : base del porcentaje -> "x" (cada bin suma 100%),
           "hue" (el histograma de cada categoría suma 100%, útil cuando los
           grupos tienen tamaños muy distintos) o "all" (sobre el total)
    n_bins : número de bins (default: regla de Freedman-Diaconis). Los bordes
           se calculan una sola vez sobre la columna completa para que los
           grupos sean comparables; con muchos bins las barras agrupadas se
           vuelven delgadas, así que aquí conviene un n_bins bajo.
    Resto de argumentos: igual que fancy_hist.
    """
    _check_args(labels, n_bins)

    valid = df[[x, hue]].copy()
    valid[x] = pd.to_numeric(valid[x], errors="coerce")
    valid = valid[np.isfinite(valid[x].to_numpy(dtype=float))]

    edges = _edges(_numeric_values(df, x), n_bins)

    hue_order = list(hue_order) if hue_order is not None else list(valid[hue].value_counts().index)

    counts = np.vstack(
        [
            np.histogram(
                valid.loc[valid[hue] == level, x].to_numpy(dtype=float),
                bins=edges,
            )[0]
            for level in hue_order
        ]
    ).T  # filas = bins, columnas = niveles de hue

    denom = {
        "x": counts.sum(1, keepdims=True),
        "hue": counts.sum(0, keepdims=True),
        "all": counts.sum(),
    }[norm]
    pcts = np.divide(
        counts * 100.0,
        denom,
        out=np.zeros(counts.shape, dtype=float),
        where=denom != 0,
    )

    centers = (edges[:-1] + edges[1:]) / 2
    widths = np.diff(edges)
    sub = widths * 0.9 / len(hue_order)
    offsets = (np.arange(len(hue_order)) - (len(hue_order) - 1) / 2)[:, None]

    if ax is None:
        _, ax = plt.subplots(figsize=figsize)
    heights = pcts if pct else counts
    for i, (level, color) in enumerate(zip(hue_order, _colors(len(hue_order), palette))):
        container = ax.bar(
            centers + offsets[i] * sub,
            heights[:, i],
            width=sub,
            color=color,
            label=str(level),
            align="center",
        )
        _bar_labels(ax, container, counts[:, i], pcts[:, i], labels, fontsize=9)

    _style(
        ax,
        title or f"Distribución de {_pretty(x)} por {_pretty(hue)}",
        _pretty(x),
        YLAB[pct],
        pct,
        rot,
        0.14 if labels != "none" else 0.05,
    )

    legend = ax.legend(
        title=_pretty(hue),
        frameon=True,
        facecolor="white",
        edgecolor="none",
        fontsize=10,
    )
    legend.get_title().set(fontweight="bold", color=INK)
    return ax
