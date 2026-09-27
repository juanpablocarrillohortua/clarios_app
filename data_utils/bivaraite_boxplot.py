import matplotlib.pyplot as plt
import seaborn as sns
from pandas.api.types import is_numeric_dtype


def _outliers_only(df, num_col, cat_col):
    """Filas atípicas de cada categoría, según la regla de 1.5 * IQR."""
    datos = df[[num_col, cat_col]]
    if datos[num_col].dtype == bool:
        # quantile interpola haciendo b - a, que numpy no define para
        # booleanos: castear evita un TypeError críptico.
        datos = datos.assign(**{num_col: datos[num_col].astype("float64")})

    def _flag(group):
        q1, q3 = group[num_col].quantile([0.25, 0.75])
        iqr = q3 - q1
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        return group[(group[num_col] < lower) | (group[num_col] > upper)]

    return datos.groupby(cat_col, group_keys=False)[[num_col, cat_col]].apply(_flag)


def plot_segmented_boxplot(
    df,
    num_col: str,
    cat_col: str,
    palette: str = "mako",
    orient: str = "v",
    show_points: bool = True,
    title: str = None,
    x_rotation: int = 45,
):
    """Genera un boxplot segmentado por una variable categórica con estilo moderno

    y capa opcional de dispersión de puntos (strip plot).

    Parámetros:
    -----------
    df : pandas.DataFrame
        El DataFrame con los datos.
    num_col : str
        Nombre de la columna numérica.
    cat_col : str
        Nombre de la columna categórica para agrupar.
    palette : str, opcional
        Paleta de colores de Seaborn ('mako', 'viridis', 'rocket', 'Set2',
        etc.).
    orient : str, opcional
        Orientación del gráfico: 'v' (vertical) o 'h' (horizontal).
    show_points : bool, opcional
        Si es True, superpone únicamente los puntos atípicos (outliers)
        con transparencia.
    title : str, opcional
        Título personalizado para el gráfico.
    """
    # 0. Validar antes de crear la figura, para no dejarla huérfana si falla
    if not is_numeric_dtype(df[num_col]):
        raise ValueError(f"num_col='{num_col}' debe ser numérica; llegó {df[num_col].dtype}")

    # 1. Aplicar tema minimalista base
    sns.set_theme(style="white", font="sans-serif")

    # 2. Configurar la figura según la orientación
    figsize = (10, 6) if orient == "v" else (10, 7)
    fig, ax = plt.subplots(figsize=figsize, dpi=120)

    # Definir ejes según la orientación
    x_var, y_var = (cat_col, num_col) if orient == "v" else (num_col, cat_col)

    # 3. Dibujar el Boxplot principal con líneas estilizadas
    sns.boxplot(
        data=df,
        x=x_var,
        y=y_var,
        palette=palette,
        hue=cat_col if orient == "v" else cat_col,
        legend=False,
        width=0.45,
        linewidth=1.2,
        fliersize=0,
        boxprops=dict(alpha=0.75),  # Ligera transparencia en las cajas
        ax=ax,
    )

    # 4. Capa opcional de puntos (Strip Plot) para visualizar distribución real
    if show_points:
        sns.stripplot(
            data=_outliers_only(df, num_col, cat_col),
            x=x_var,
            y=y_var,
            color="#1e293b",
            alpha=0.3,
            size=3.5,
            jitter=0.2,
            ax=ax,
        )

    # 5. Estilizado moderno del marco (eliminar espinas innecesarias)
    sns.despine(top=True, right=True, left=False, bottom=False)

    # 6. Títulos y etiquetas estilizadas
    formatted_num_title = num_col.replace("_", " ").title()
    formatted_cat_title = cat_col.replace("_", " ").title()

    main_title = (
        title if title else f"Distribución de {formatted_num_title} por {formatted_cat_title}"
    )

    ax.set_title(
        main_title,
        fontsize=14,
        fontweight="bold",
        pad=20,
        loc="left",
        color="#1e293b",
    )

    # Ajustar etiquetas según la orientación
    if orient == "v":
        ax.set_xlabel(
            formatted_cat_title,
            fontsize=11,
            fontweight="bold",
            color="#475569",
            labelpad=10,
        )
        ax.set_ylabel(
            formatted_num_title,
            fontsize=11,
            fontweight="bold",
            color="#475569",
            labelpad=10,
        )
        ax.yaxis.grid(True, linestyle="--", alpha=0.3, color="#94a3b8")
        if x_rotation != 0:
            plt.setp(
                ax.get_xticklabels(),
                rotation=x_rotation,
                ha="right",
                rotation_mode="anchor",
            )
    else:
        ax.set_xlabel(
            formatted_num_title,
            fontsize=11,
            fontweight="bold",
            color="#475569",
            labelpad=10,
        )
        ax.set_ylabel(
            formatted_cat_title,
            fontsize=11,
            fontweight="bold",
            color="#475569",
            labelpad=10,
        )
        ax.xaxis.grid(True, linestyle="--", alpha=0.3, color="#94a3b8")
        if x_rotation != 0:
            plt.setp(
                ax.get_xticklabels(),
                rotation=x_rotation,
                ha="right",
                rotation_mode="anchor",
            )

    ax.set_axisbelow(True)

    plt.tight_layout()
    plt.show()
