"""Hub de pruebas de hipótesis entre una lista de variables predictoras y una
variable objetivo, decidiendo la prueba a partir del tipo de cada variable.

Es el hermano de ``cat_num_dep``, que resuelve un solo caso (una numérica
contra varias categóricas) y exige que quien llama sepa de antemano cuál
es cuál. Aquí el tipo se infiere y la prueba se elige sola.

Inferencia de tipo, en orden:
    dtype no numérico                       -> categorica
    entera y nunique <= max_categorias      -> categorica
    entera                                  -> discreta
    con decimales                           -> continua

El segundo caso es el que importa en la práctica: variables como `race`
(códigos 1-8) o `sct_margins` (1, 2, 3, 9) son enteros pero no son
cantidades, y correlacionarlas trataría el 8 como cuatro veces el 2.

Ruta de decisión:
    categorica vs categorica
        Chi-cuadrado de Pearson; Fisher exacta si alguna frecuencia
        esperada < 5 en una tabla 2x2. Se reporta la V de Cramér, porque
        con miles de filas casi todo sale significativo y el tamaño del
        efecto es lo que separa una asociación real de una trivial.
    numerica vs categorica  (en cualquier orden)
        2 grupos  + normales     -> t de Student (o Welch si no hay
                                    homocedasticidad)
        2 grupos  + no normales  -> U de Mann-Whitney
        >2 grupos + normales     -> ANOVA (F) + post-hoc Tukey HSD
        >2 grupos + no normales  -> Kruskal-Wallis + post-hoc Mann-Whitney
                                    con Bonferroni
    numerica vs numerica
        alguna discreta          -> tau-b de Kendall (soporta empates)
        ambas continuas normales -> r de Pearson
        resto                    -> rho de Spearman
"""

from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats

#: Tipos que puede tomar una variable.
TIPOS = ("categorica", "discreta", "continua")

#: Frecuencia esperada mínima que exige el Chi-cuadrado.
MIN_ESPERADA = 5

#: Residuo estandarizado a partir del cual una celda se reporta.
RESIDUO_MIN = 2.0

#: Columnas de la tabla de salida, en orden.
COLUMNAS = [
    "feature",
    "tipo_feature",
    "target",
    "tipo_target",
    "n",
    "n_grupos",
    "prueba",
    "estadistico",
    "p_valor",
    "normalidad",
    "homocedasticidad",
    "supuesto_ok",
    "cramer_v",
    "decision",
    "conclusion",
    "post_hoc",
]


def _p_normalidad(x):
    """Shapiro-Wilk si n < 50, D'Agostino-Pearson si n >= 50."""
    if len(x) < 3 or np.ptp(x) == 0:
        return 0.0
    try:
        return stats.shapiro(x).pvalue if len(x) < 50 else stats.normaltest(x).pvalue
    except Exception:
        return 0.0


def _posthoc_tukey(grupos, niveles, alpha):
    res = stats.tukey_hsd(*grupos)
    pares = [
        f"{niveles[i]} vs {niveles[j]} (p={res.pvalue[i, j]:.4f})"
        for i, j in combinations(range(len(grupos)), 2)
        if res.pvalue[i, j] < alpha
    ]
    return "; ".join(pares) if pares else "sin pares significativos"


def _posthoc_dunn_bonf(grupos, niveles, alpha):
    pares_idx = list(combinations(range(len(grupos)), 2))
    m = len(pares_idx)
    pares = []
    for i, j in pares_idx:
        p = min(stats.mannwhitneyu(grupos[i], grupos[j]).pvalue * m, 1.0)
        if p < alpha:
            pares.append(f"{niveles[i]} vs {niveles[j]} (p={p:.4f})")
    return "; ".join(pares) if pares else "sin pares significativos"


def _posthoc_residuos(tabla, esperada):
    """Celdas cuyo residuo estandarizado supera RESIDUO_MIN.

    El equivalente al post-hoc de las pruebas numéricas: dice *dónde*
    está la asociación, no solo que existe.
    """
    resid = (tabla.to_numpy() - esperada) / np.sqrt(esperada)
    celdas = [
        f"{tabla.index[i]}|{tabla.columns[j]} (r={resid[i, j]:+.2f})"
        for i, j in zip(*np.where(np.abs(resid) > RESIDUO_MIN))
    ]
    if not celdas:
        return "sin celdas destacadas"
    orden = np.argsort([-abs(float(c.split("r=")[1].rstrip(")"))) for c in celdas])
    return "; ".join(celdas[i] for i in orden[:6])


def _infer_type(s: pd.Series, max_categorias: int) -> str:
    """Clasifica una serie en 'categorica', 'discreta' o 'continua'."""
    s = s.dropna()
    if not pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
        return "categorica"
    if s.empty:
        return "categorica"
    valores = s.to_numpy(dtype=float)
    entera = np.all(np.isfinite(valores)) and np.all(np.equal(np.mod(valores, 1), 0))
    if entera:
        return "categorica" if s.nunique() <= max_categorias else "discreta"
    return "continua"


def _fila_base(feature, t_feat, target, t_targ, n):
    """Fila con los campos comunes; el resto queda vacío."""
    fila = dict.fromkeys(COLUMNAS, np.nan)
    fila.update(
        {
            "feature": feature,
            "tipo_feature": t_feat,
            "target": target,
            "tipo_target": t_targ,
            "n": n,
            "post_hoc": "",
        }
    )
    return fila


def _no_aplica(fila, motivo):
    fila.update(
        {
            "prueba": "no aplica",
            "decision": "no evaluada",
            "conclusion": motivo,
        }
    )
    return fila


def _test_cat_cat(fila, x, y, alpha, n_min):
    """Chi-cuadrado de independencia, con Fisher para 2x2 problemáticas."""
    tabla = pd.crosstab(x, y)
    # Se descartan niveles con menos de n_min casos, igual que en la
    # ruta numérica, para que ninguna prueba se apoye en celdas vacías.
    tabla = tabla.loc[tabla.sum(1) >= n_min, tabla.sum(0) >= n_min]
    if tabla.shape[0] < 2 or tabla.shape[1] < 2:
        return _no_aplica(fila, f"menos de 2 niveles con n>={n_min}")

    chi2, p, _, esperada = stats.chi2_contingency(tabla)
    supuesto = bool((esperada >= MIN_ESPERADA).all())
    n = int(tabla.to_numpy().sum())
    prueba = "Chi-cuadrado de Pearson"
    est = chi2

    if not supuesto and tabla.shape == (2, 2):
        est, p = stats.fisher_exact(tabla)
        prueba = "Fisher exacta"

    cramer = np.sqrt(chi2 / (n * (min(tabla.shape) - 1)))
    rechaza = p < alpha
    fila.update(
        {
            "n": n,
            "n_grupos": tabla.shape[0] * tabla.shape[1],
            "prueba": prueba,
            "estadistico": round(float(est), 4),
            "p_valor": round(float(p), 6),
            "supuesto_ok": "sí" if supuesto else "no",
            "cramer_v": round(float(cramer), 4),
            "decision": "Se rechaza H0" if rechaza else "No se rechaza H0",
            "conclusion": (
                "Dependencia: la distribución cambia entre categorías"
                if rechaza
                else "Independencia: no hay evidencia de asociación"
            ),
            "post_hoc": (_posthoc_residuos(tabla, esperada) if rechaza else ""),
        }
    )
    return fila


def _test_num_cat(fila, num, cat, alpha, n_min):
    """Compara la numérica entre los niveles de la categórica.

    Es la ruta de ``cat_num_dep.hub_pruebas_num_cat``, portada tal cual.
    """
    sub = pd.DataFrame({"_num": num, "_cat": cat}).dropna()
    datos = [
        (str(nivel), g["_num"].astype(float).values)
        for nivel, g in sub.groupby("_cat", observed=True)
    ]
    datos = [(n, g) for n, g in datos if len(g) >= n_min]
    niveles, grupos = ([d[0] for d in datos], [d[1] for d in datos])
    k = len(grupos)
    fila["n_grupos"] = k

    if k < 2:
        return _no_aplica(fila, f"menos de 2 grupos con n>={n_min}")

    normal = all(_p_normalidad(g) > alpha for g in grupos)
    homo = stats.levene(*grupos, center="median").pvalue > alpha
    post_hoc = ""

    if k == 2:
        if normal:
            est, p = stats.ttest_ind(grupos[0], grupos[1], equal_var=homo)
            prueba = "t de Student" if homo else "t de Welch"
        else:
            est, p = stats.mannwhitneyu(grupos[0], grupos[1])
            prueba = "U de Mann-Whitney"
    elif normal:
        est, p = stats.f_oneway(*grupos)
        prueba = "ANOVA (F)"
        if p < alpha:
            post_hoc = _posthoc_tukey(grupos, niveles, alpha)
    else:
        est, p = stats.kruskal(*grupos)
        prueba = "Kruskal-Wallis (H)"
        if p < alpha:
            post_hoc = _posthoc_dunn_bonf(grupos, niveles, alpha)

    rechaza = p < alpha
    fila.update(
        {
            "n": len(sub),
            "prueba": prueba,
            "estadistico": round(float(est), 4),
            "p_valor": round(float(p), 6),
            "normalidad": "sí" if normal else "no",
            "homocedasticidad": "sí" if homo else "no",
            "decision": "Se rechaza H0" if rechaza else "No se rechaza H0",
            "conclusion": (
                "Dependencia: la numérica difiere entre categorías"
                if rechaza
                else "Independencia: no hay evidencia de diferencia"
            ),
            "post_hoc": post_hoc,
        }
    )
    return fila


def _test_num_num(fila, x, y, t_x, t_y, alpha):
    """Correlación; el método depende de empates y normalidad."""
    sub = pd.DataFrame({"_x": x, "_y": y}).dropna().astype(float)
    if len(sub) < 3 or sub["_x"].nunique() < 2 or sub["_y"].nunique() < 2:
        return _no_aplica(fila, "muestra insuficiente o variable constante")

    a, b = sub["_x"].to_numpy(), sub["_y"].to_numpy()
    normal = _p_normalidad(a) > alpha and _p_normalidad(b) > alpha
    discreta = "discreta" in (t_x, t_y)

    if discreta:
        # Con tantos empates, tau-b es preferible a rho de Spearman.
        res = stats.kendalltau(a, b)
        prueba = "tau-b de Kendall"
    elif normal:
        res = stats.pearsonr(a, b)
        prueba = "r de Pearson"
    else:
        res = stats.spearmanr(a, b)
        prueba = "rho de Spearman"

    est, p = float(res.statistic), float(res.pvalue)
    rechaza = p < alpha
    fila.update(
        {
            "n": len(sub),
            "prueba": prueba,
            "estadistico": round(est, 4),
            "p_valor": round(p, 6),
            "normalidad": "sí" if normal else "no",
            "decision": "Se rechaza H0" if rechaza else "No se rechaza H0",
            "conclusion": (
                f"Dependencia: asociación monótona ({est:+.3f})"
                if rechaza
                else "Independencia: no hay evidencia de asociación"
            ),
        }
    )
    return fila


def test_features_against_target(
    df: pd.DataFrame,
    feature_variables: list,
    target_variable: str,
    alpha: float = 0.05,
    n_min: int = 3,
    max_categorias: int = 10,
    tipos: dict | None = None,
):
    """Prueba cada variable predictora contra la variable objetivo,
    eligiendo la prueba según el tipo de ambas.

    Parámetros
    ----------
    df                : DataFrame
    feature_variables : str o lista de str, variables predictoras
    target_variable   : str, variable objetivo
    alpha             : nivel de significancia (default 0.05)
    n_min             : tamaño mínimo por grupo/nivel (default 3)
    max_categorias    : una variable entera con hasta este número de
                        valores distintos se considera categórica
                        (default 10). Sube el umbral si tienes códigos
                        con más niveles.
    tipos             : dict opcional {variable: tipo} para forzar el
                        tipo de una variable; tipo en TIPOS.

    Retorna
    -------
    DataFrame con una fila por variable predictora, ordenado por p_valor.
    Las columnas que no aplican a la prueba usada quedan en NaN: por
    ejemplo `homocedasticidad` solo tiene sentido en la ruta numérica
    contra categórica, y `cramer_v` solo en la de Chi-cuadrado.
    """
    if isinstance(feature_variables, str):
        feature_variables = [feature_variables]

    tipos = dict(tipos or {})
    malos = [t for t in tipos.values() if t not in TIPOS]
    if malos:
        raise ValueError(f"tipos debe usar {list(TIPOS)}; recibido: {malos}")
    if target_variable not in df.columns:
        raise ValueError(f"'{target_variable}' no está en el DataFrame")
    faltantes = [c for c in feature_variables if c not in df.columns]
    if faltantes:
        raise ValueError(f"no están en el DataFrame: {faltantes}")
    if target_variable in feature_variables:
        raise ValueError(f"'{target_variable}' es el target: no puede ser también feature")

    def tipo_de(col):
        return tipos.get(col) or _infer_type(df[col], max_categorias)

    t_target = tipo_de(target_variable)
    filas = []
    for feat in feature_variables:
        t_feat = tipo_de(feat)
        sub = df[[feat, target_variable]].dropna()
        fila = _fila_base(feat, t_feat, target_variable, t_target, len(sub))

        if sub.empty or sub[feat].nunique() < 2:
            filas.append(_no_aplica(fila, "sin datos o variable constante"))
            continue

        x, y = sub[feat], sub[target_variable]
        cat_feat, cat_targ = t_feat == "categorica", t_target == "categorica"

        if cat_feat and cat_targ:
            filas.append(_test_cat_cat(fila, x, y, alpha, n_min))
        elif cat_feat:
            filas.append(_test_num_cat(fila, y, x, alpha, n_min))
        elif cat_targ:
            filas.append(_test_num_cat(fila, x, y, alpha, n_min))
        else:
            filas.append(_test_num_num(fila, x, y, t_feat, t_target, alpha))

    return pd.DataFrame(filas, columns=COLUMNAS).sort_values("p_valor").reset_index(drop=True)
