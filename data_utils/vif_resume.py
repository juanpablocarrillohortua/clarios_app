"""Factor de inflación de la varianza (VIF) de un conjunto de predictores.

El VIF de una variable es 1 / (1 - R²) de la regresión de esa variable
contra todas las demás. Mide cuánto se infla la varianza de su coeficiente
por culpa de la colinealidad con el resto:

    VIF ~ 1      la variable aporta información propia
    VIF 5 - 10   colinealidad apreciable, los coeficientes empiezan a ser
                 inestables
    VIF > 10     regla de dedo habitual para considerar eliminarla

La variable objetivo se EXCLUYE del cálculo: el VIF describe la relación de
los predictores entre sí, no con lo que se quiere predecir.

Degeneración
------------
Si dos columnas son combinación lineal exacta de otras, la matriz de diseño
pierde rango y el VIF se dispara a valores del orden de 1e14, que son ruido
numérico y no una medida. Pasa con facilidad cuando las variables se derivan
unas de otras: fracciones que suman 1, un rango que es la resta de dos
percentiles, un contraste que es la resta de dos medias.

Este módulo descarta primero las columnas de varianza cero (su VIF es
indefinido) y luego comprueba el rango de lo que queda. Si sigue habiendo
deficiencia avisa nombrando el problema, pero devuelve la tabla igual: la
decisión de qué eliminar es del analista.

Caché
-----
`cache_path` guarda el resultado en un pickle junto con una huella de las
entradas (columnas usadas y hash de los valores). En la siguiente llamada la
huella se recalcula y, si coincide, se devuelve lo guardado sin volver a
ajustar ninguna regresión. Hashear cuesta milisegundos frente a los segundos
del cálculo completo.
"""

from __future__ import annotations

import hashlib
import pickle
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.tools.sm_exceptions import SingularMatrixWarning

CACHE_VERSION = 1  # subir si cambia la forma del diccionario guardado
VIF_ALTO = 10.0  # umbral habitual para sospechar de una variable
CONDICION_ALTA = 30.0  # índice de condición: umbral clásico de colinealidad


def _numeric_features(df, target_variable, feature_variables):
    """Columnas numéricas utilizables, ya sin la variable objetivo."""
    if target_variable not in df.columns:
        raise ValueError(f"la variable objetivo '{target_variable}' no está en el DataFrame")

    if feature_variables is None:
        candidatas = [c for c in df.columns if c != target_variable]
    else:
        if isinstance(feature_variables, str):
            feature_variables = [feature_variables]
        faltantes = [c for c in feature_variables if c not in df.columns]
        if faltantes:
            raise ValueError(f"no están en el DataFrame: {faltantes}")
        candidatas = [c for c in feature_variables if c != target_variable]

    columnas = [c for c in candidatas if is_numeric_dtype(df[c])]
    if len(columnas) < 2:
        raise ValueError(
            "hacen falta al menos dos columnas numéricas además de "
            f"'{target_variable}'; se encontraron {len(columnas)}"
        )
    return columnas


def _fingerprint(x, target_variable):
    """Huella de las entradas: columnas, objetivo y valores.

    Se indexa por la entrada y no por la salida porque no se puede saber si
    el resultado cambió sin volver a calcularlo, que es justo lo que la
    caché quiere evitar.
    """
    h = hashlib.sha256()
    h.update(str(CACHE_VERSION).encode())
    h.update(target_variable.encode())
    h.update("|".join(x.columns).encode())
    valores = pd.util.hash_pandas_object(x, index=False).to_numpy()
    h.update(valores.tobytes())
    return h.hexdigest()


def _load_cache(cache_path, fingerprint):
    """DataFrame guardado si la huella coincide; None en cualquier otro caso.

    Un pickle ilegible, truncado o de una versión anterior es un fallo de
    caché, no un error: se recalcula y se sobrescribe.
    """
    path = Path(cache_path)
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            guardado = pickle.load(fh)
        if guardado.get("fingerprint") == fingerprint:
            return guardado["vif"]
    except Exception:
        return None
    return None


def _save_cache(cache_path, fingerprint, vif):
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        pickle.dump(
            {"fingerprint": fingerprint, "vif": vif},
            fh,
            protocol=pickle.HIGHEST_PROTOCOL,
        )


def _design_matrix(x):
    """La matriz que ve el VIF: los predictores más la constante al final.

    La constante importa para el diagnóstico: dependencias como
    "estas fracciones suman 1" solo aparecen cuando el vector de unos
    forma parte de la matriz.
    """
    return x.assign(const=1.0).to_numpy(dtype=float)


def _condition_index(x):
    """Número de condición de los predictores estandarizados.

    Se estandariza porque el número de condición crudo depende de las
    escalas: un volumen en mm³ y una fracción entre 0 y 1 lo disparan sin
    que haya colinealidad. Sobre columnas de varianza unitaria mide solo
    la dependencia lineal. La regla clásica marca 30 como umbral.
    """
    z = (x - x.mean()) / x.std(ddof=0)
    return float(np.linalg.cond(z.to_numpy(dtype=float)))


def _compute_vif(x):
    """VIF de cada columna de `x`, con la constante añadida al final.

    `variance_inflation_factor` espera la constante dentro de la matriz;
    al ir en la última posición, el índice i sigue apuntando a x.columns[i].
    """
    valores = _design_matrix(x)
    with warnings.catch_warnings():
        # el aviso de matriz singular se repite una vez por columna; el
        # aviso propio del módulo dice lo mismo con más contexto
        warnings.simplefilter("ignore", SingularMatrixWarning)
        return [variance_inflation_factor(valores, i) for i in range(x.shape[1])]


def vif_table(
    df: pd.DataFrame,
    target_variable: str,
    feature_variables: list | None = None,
    cache_path: str | Path | None = None,
    refresh: bool = False,
    verbose: bool = True,
) -> pd.DataFrame:
    """Calcula el VIF de los predictores, excluyendo la variable objetivo.

    Parámetros
    ----------
    df                : DataFrame
    target_variable   : str, variable objetivo; se excluye del cálculo
    feature_variables : lista opcional de predictores. Si es None se usan
                        todas las columnas numéricas menos la objetivo.
                        Las no numéricas se descartan siempre.
    cache_path        : ruta a un pickle donde guardar y reutilizar el
                        resultado. None desactiva la caché. Se reutiliza
                        solo si las columnas y los valores son los mismos.
    refresh           : True recalcula y sobrescribe la caché.
    verbose           : True imprime los avisos de columnas descartadas y
                        de deficiencia de rango.

    Retorna
    -------
    DataFrame con una fila por predictor, ordenado por VIF descendente:

        Variable    nombre de la columna
        VIF         factor de inflación, NaN si se descartó
        alto        True si VIF > 10
        descartada  motivo, o None si entró en el cálculo

    Las columnas de varianza cero se devuelven con VIF NaN y su motivo:
    su VIF es indefinido, así que participar solo rompería el cálculo del
    resto.
    """
    columnas = _numeric_features(df, target_variable, feature_variables)
    x = df[columnas].astype(float)

    fingerprint = None
    if cache_path is not None:
        fingerprint = _fingerprint(x, target_variable)
        if not refresh:
            guardado = _load_cache(cache_path, fingerprint)
            if guardado is not None:
                return guardado

    constantes = [c for c in x.columns if x[c].nunique(dropna=False) <= 1]
    utiles = [c for c in x.columns if c not in constantes]
    if len(utiles) < 2:
        raise ValueError(
            f"quedan menos de dos columnas con varianza tras descartar las constantes: {constantes}"
        )

    x_utiles = x[utiles]
    vif = pd.DataFrame(
        {
            "Variable": utiles,
            "VIF": _compute_vif(x_utiles),
        }
    )
    vif["descartada"] = None

    if constantes:
        vif = pd.concat(
            [
                vif,
                pd.DataFrame(
                    {
                        "Variable": constantes,
                        "VIF": np.nan,
                        "descartada": "varianza cero",
                    }
                ),
            ],
            ignore_index=True,
        )

    vif["alto"] = vif["VIF"] > VIF_ALTO
    vif = vif.sort_values(by="VIF", ascending=False, na_position="last").reset_index(drop=True)

    if verbose:
        _avisar(x_utiles, constantes, vif)

    if cache_path is not None:
        _save_cache(cache_path, fingerprint, vif)
    return vif


def _avisar(x_utiles, constantes, vif):
    """Avisos sobre columnas descartadas y sobre matriz mal condicionada."""
    if constantes:
        print(
            f"Aviso: {len(constantes)} columna(s) de varianza cero, sin VIF "
            f"definido: {', '.join(constantes)}"
        )

    peores = ", ".join(vif.dropna(subset=["VIF"]).head(5)["Variable"])

    # El rango se mide sobre la matriz CON la constante, que es la que usa
    # el VIF: dependencias del tipo "estas fracciones suman 1" solo se ven
    # cuando el vector de unos está dentro.
    diseno = _design_matrix(x_utiles)
    rango = np.linalg.matrix_rank(diseno)
    n_col = diseno.shape[1]
    if rango < n_col:
        print(
            f"Aviso: la matriz de diseño tiene rango {rango} de {n_col}: "
            f"{n_col - rango} columna(s) son combinación lineal exacta de "
            "otras. Los VIF enormes son ruido numérico, no una medida de "
            f"colinealidad. Empieza por revisar: {peores}"
        )
        return

    # Aunque el rango salga completo la matriz puede estar al borde de la
    # singularidad; matrix_rank usa una tolerancia demasiado laxa para
    # detectarlo. El índice de condición sí lo ve.
    indice = _condition_index(x_utiles)
    if indice > CONDICION_ALTA:
        print(
            f"Aviso: índice de condición {indice:.1e} (por encima de "
            f"{CONDICION_ALTA:g} ya se considera problemático). Hay "
            "colinealidad casi perfecta, así que los VIF más altos son "
            f"poco fiables. Empieza por revisar: {peores}"
        )
