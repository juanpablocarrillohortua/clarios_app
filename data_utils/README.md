# `utils/` — analysis toolkit

One self-contained module per analysis idea. The modules **never import each
other** and [`__init__.py`](__init__.py) re-exports nothing, so always import
the submodule directly. If two modules need the same helper, it is copied
rather than shared.

## Quick start

From a notebook, the `sys.path` hop is what makes `utils` resolve at all:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd().parent))  # notebooks/ -> repo root

from utils.fancy_barplot import fancy_bars
from utils.target_against_features import test_features_against_target

fancy_bars(df, "label", pct=True, labels="both")
test_features_against_target(df, ["Age_at_StudyDate", "race"], "label")
```

## Overview

| Module | Answers | Public API | Returns |
| --- | --- | --- | --- |
| [`fancy_barplot`](fancy_barplot.py) | How is a category distributed? And against a second category? | `fancy_bars`, `plot_cat_relation` | `plt.Axes` |
| [`fancy_hist`](fancy_hist.py) | How is a numeric variable distributed? And split by a category? | `fancy_hist`, `plot_num_relation` | `plt.Axes` |
| [`segmented_bars`](segmented_bars.py) | Same bars, one panel per segment | `segmented_bars` | `np.ndarray` of Axes |
| [`bivaraite_boxplot`](bivaraite_boxplot.py) | Numeric spread across categories | `plot_segmented_boxplot` | nothing |
| [`category_vs_numeric_plot`](category_vs_numeric_plot.py) | One histogram per category | `plot_faceted_histograms` | nothing |
| [`target_against_features`](target_against_features.py) | Which features relate to the target? (test picked automatically) | `test_features_against_target` | `DataFrame` |
| [`cat_num_dep`](cat_num_dep.py) | Does a numeric differ across categories? | `hub_pruebas_num_cat` | `DataFrame` |
| [`triple_plot`](triple_plot.py) | Is this variable normal? | `normality_report` | `dict` |
| [`corr_vector`](corr_vector.py) | Pearson correlation of everything against one target | `plot_target_correlation` | `(fig, ax)` |
| [`mutual_inf_plot`](mutual_inf_plot.py) | Feature relevance = correlation + mutual information | `analizar_relevancia_caracteristicas` (+4) | `DataFrame` |
| [`vif_resume`](vif_resume.py) | Which predictors are redundant with each other? | `vif_table` | `DataFrame` |
| [`fast_OLS`](fast_OLS.py) | Full diagnostics of a fitted OLS model | class `DiagnosticoOLS` | varies |

> **Two modules do not import today.** `fast_OLS` needs `plotly` and
> `mutual_inf_plot` needs `scikit-learn`; neither is installed. See
> [Requirements](#requirements).

---

# Distribution plots

## `fancy_barplot`

### `fancy_bars(df, x, ...)` — distribution of one categorical

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `df` | `DataFrame` | — | source data |
| `x` | `str` | — | categorical column to count |
| `pct` | `bool` | `False` | `False` counts, `True` percentages |
| `labels` | `"none"\|"count"\|"pct"\|"both"` | `"none"` | annotation drawn on each bar |
| `title` | `str \| None` | `None` | `None` auto-generates from the column name |
| `order` | `list \| None` | `None` | category order; default most frequent first |
| `palette` | `str \| list` | `None` | seaborn palette name or list of colours |
| `ax` | `Axes \| None` | `None` | draw into an existing axes instead of a new figure |
| `figsize` | `tuple` | `(9, 7)` | only used when `ax is None` |
| `rot` | `float` | `0` | rotation of the category labels |
| `top` | `int \| None` | `None` | keep only the N most frequent categories |
| `orient` | `"v"\|"h"` | `"v"` | `"h"` puts categories down the left, bars rightward |

**Returns** `plt.Axes`.

### `plot_cat_relation(df, x, hue, ...)` — two categoricals, grouped bars

Same parameters as `fancy_bars` plus:

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `hue` | `str` | — | second categorical; one bar group per level |
| `norm` | `"x"\|"hue"\|"all"` | `"x"` | percentage base: each `x` group sums 100, each `hue` sums 100, or over the grand total |
| `hue_order` | `list \| None` | `None` | order of the `hue` levels |
| `figsize` | `tuple` | `(10, 6)` | — |

**Returns** `plt.Axes`.

## `fancy_hist`

The numeric counterpart of `fancy_barplot`, same visual language.

### `fancy_hist(df, x, ...)` — distribution of one numeric

Same parameters as `fancy_bars` **minus** `order` and `top` (meaningless on a
continuous axis) and **minus `orient`** (no horizontal mode here), **plus**:

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `n_bins` | `int \| None` | `None` | `None` computes the bin count with the Freedman–Diaconis rule, clamped to 5–60 |

Nulls and infinities are dropped before binning. **Returns** `plt.Axes`.

### `plot_num_relation(df, x, hue, ...)` — numeric split by a categorical

Same parameters as `fancy_hist` plus:

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `hue` | `str` | — | categorical that splits the histogram |
| `norm` | `"x"\|"hue"\|"all"` | `"x"` | `"hue"` makes each group's own histogram sum to 100 — the useful one when groups differ in size |
| `hue_order` | `list \| None` | `None` | order of the `hue` levels |
| `n_bins` | `int \| None` | `None` | edges computed once over the pooled column so groups stay comparable |
| `figsize` | `tuple` | `(10, 6)` | only used when `ax is None` |

Bars are grouped side by side, so a **low `n_bins` reads better** here.
**Returns** `plt.Axes`.

## `segmented_bars`

### `segmented_bars(df, x, segment, ...)` — small multiples

One panel per level of `segment`; every panel shows the same bar variable.
Category **order and colour are computed once globally**, so a category keeps
its position and colour in every panel even where its count is zero.

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `df` | `DataFrame` | — | source data |
| `x` | `str` | — | categorical that forms the bars |
| `segment` | `str` | — | categorical that splits into panels |
| `pct` | `bool` | `True` | **defaults to `True`**, unlike `fancy_bars` — unequal segments are unreadable as raw counts |
| `norm` | `"segmento"\|"barra"\|"all"` | `"segmento"` | each panel sums 100 / each category sums 100 across panels / over the total |
| `labels` | `"none"\|"count"\|"pct"\|"both"` | `"none"` | annotation on each bar |
| `title` | `str \| None` | `None` | overall figure title |
| `order` | `list \| None` | `None` | bar order, computed on the total rather than per panel |
| `segment_order` | `list \| None` | `None` | order **and selection** of panels |
| `palette` | `str \| list` | `None` | seaborn palette name or list |
| `axes` | `ndarray \| None` | `None` | existing grid to draw into |
| `figsize` | `tuple \| None` | `None` | defaults to `(4.5·ncols, 4·nrows)` |
| `rot` | `float` | `0` | rotation of the category labels |
| `ncols` | `int` | `3` | panels per row when the grid is created |
| `sharey` | `bool` | `True` | shared y-axis; only applies when the grid is created here |
| `top` | `int \| None` | `None` | keep only the N most frequent categories |
| `dropna` | `bool` | `True` | drop nulls in both columns |

**Returns** `np.ndarray` of Axes. `axes.flat[0].figure` gets the figure.
Raises if `segment` would produce more than 20 panels — almost always a
continuous variable passed by mistake.

## `bivaraite_boxplot`

### `plot_segmented_boxplot(df, num_col, cat_col, ...)`

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `df` | `DataFrame` | — | source data |
| `num_col` | `str` | — | numeric column |
| `cat_col` | `str` | — | categorical that groups the boxes |
| `palette` | `str` | `"mako"` | seaborn palette name |
| `orient` | `str` | `"v"` | `"v"` vertical, `"h"` horizontal |
| `show_points` | `bool` | `True` | overlay the individual points (strip plot) |
| `title` | `str` | `None` | custom title |
| `x_rotation` | `int` | `45` | rotation of the x labels |

**Returns nothing** — draws and calls `plt.show()`, so it cannot be composed
into a subplot grid.

## `category_vs_numeric_plot`

### `plot_faceted_histograms(df, num_col, cat_col, ...)`

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `df` | `DataFrame` | — | source data |
| `num_col` | `str` | — | numeric column to histogram |
| `cat_col` | `str` | — | categorical that creates the facets |
| `palette` | `str` | `"mako"` | seaborn palette name |
| `kde` | `bool` | `True` | overlay a density curve |
| `col_wrap` | `int` | `3` | facets per row |
| `title` | `str` | `None` | custom title |

**Returns nothing** — builds a `sns.FacetGrid` and calls `plt.show()`.

---

# Hypothesis tests

## `target_against_features`

### `test_features_against_target(df, feature_variables, target_variable, ...)`

Infers each variable's type, then picks the test. One row per feature.

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `df` | `DataFrame` | — | source data |
| `feature_variables` | `list[str] \| str` | — | predictors to test |
| `target_variable` | `str` | — | the target |
| `alpha` | `float` | `0.05` | significance level |
| `n_min` | `int` | `3` | minimum size for a group/level to count |
| `max_categorias` | `int` | `10` | an integer column with at most this many distinct values is treated as categorical |
| `tipos` | `dict \| None` | `None` | force a type: `{"col": "categorica"\|"discreta"\|"continua"}` |

**Type inference**, in order:

| Condition | Type |
| --- | --- |
| dtype is not numeric | `categorica` |
| integer-valued and `nunique <= max_categorias` | `categorica` |
| integer-valued | `discreta` |
| has decimals | `continua` |

The second rule is the one that matters: `race` coded 1-8 is an integer but not
a quantity, and correlating it would treat 8 as four times 2.

**Test selection**:

| Feature × Target | Test |
| --- | --- |
| categorical × categorical | Chi-squared; Fisher's exact if an expected frequency < 5 in a 2×2. Reports Cramér's V |
| numeric × categorical (either order), 2 groups, normal | t de Student (Welch if Levene fails) |
| numeric × categorical, 2 groups, non-normal | U de Mann-Whitney |
| numeric × categorical, >2 groups, normal | ANOVA + Tukey HSD post-hoc |
| numeric × categorical, >2 groups, non-normal | Kruskal-Wallis + Mann-Whitney with Bonferroni |
| numeric × numeric, either discrete | Kendall tau-b (handles ties) |
| numeric × numeric, both continuous and normal | Pearson |
| numeric × numeric, otherwise | Spearman |

**Returns** a `DataFrame` sorted by `p_valor`, columns: `feature`,
`tipo_feature`, `target`, `tipo_target`, `n`, `n_grupos`, `prueba`,
`estadistico`, `p_valor`, `normalidad`, `homocedasticidad`, `supuesto_ok`,
`cramer_v`, `decision`, `conclusion`, `post_hoc`. Columns that do not apply to
the test used are `NaN`.

## `cat_num_dep`

### `hub_pruebas_num_cat(df, var_num, vars_cat, ...)`

The narrower predecessor: **one** numeric against a list of categoricals. Same
numeric-vs-categorical decision tree as the table above.

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `df` | `DataFrame` | — | source data |
| `var_num` | `str` | — | the numeric variable |
| `vars_cat` | `list[str] \| str` | — | categoricals to evaluate |
| `alpha` | `float` | `0.05` | significance level |
| `n_min` | `int` | `3` | minimum group size to include a level |

**Returns** a `DataFrame` sorted by `p_valor`, one row per numeric-categorical
pair: `var_numerica`, `var_categorica`, `n_grupos`, `prueba`, `estadistico`,
`p_valor`, `normalidad`, `homocedasticidad`, `decision`, `conclusion`,
`post_hoc`.

## `triple_plot`

### `normality_report(df, col, ...)`

A 1×3 panel — histogram, boxplot, Q-Q — plus a normality test.

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `df` | `DataFrame` | — | source data |
| `col` | `str` | — | numeric column |
| `log_scale` | `bool` | `False` | log10 **axes**, data untouched; `x <= 0` dropped |
| `log_transformation` | `bool` | `False` | replaces the data with log10(x); mutually exclusive with `log_scale` |
| `neg_strategy` | `"shift"\|"signed"\|"drop"` | `"shift"` | how to treat `x <= 0` under `log_transformation` |
| `test` | `"auto"\|"shapiro"\|"dagostino"\|"ks"` | `"auto"` | `"auto"` uses Shapiro-Wilk for n ≤ 5000, K² above |
| `alpha` | `float` | `0.05` | significance level |
| `color` | `str` | `"#5dade2"` | main colour |
| `kde` | `bool` | `True` | overlay the density curve |
| `max_qq_points` | `int` | `5000` | points actually drawn on the Q-Q |
| `max_fliers` | `int` | `2000` | outliers actually drawn on the boxplot |
| `kde_max_n` | `int` | `20000` | above this the KDE is subsampled |
| `figsize` | `tuple` | `(17, 5.6)` | figure size |
| `random_state` | `int` | `0` | seed for the subsampling |
| `show` | `bool` | `True` | call `plt.show()` |

Everything after `log_scale` is **keyword-only**.

**Returns** a `dict` with `n`, `n_dropped`, `n_dropped_nonpositive`, `mode`,
`transform`, `mean`, `median`, `std`, `skew`, `kurtosis`, `test`, `statistic`,
`p_value`, `is_normal`, `n_test`, the `*_raw` counterparts, and `fig`.

---

# Relevance and regression

## `corr_vector`

### `plot_target_correlation(df, columns, target_col, ...)`

A one-column heatmap of Pearson correlations against a single target, sorted.

| Parameter | Type | Default | What it does |
| --- | --- | --- | --- |
| `df` | `DataFrame` | — | source data |
| `columns` | `list` | — | numeric columns to consider; `target_col` is appended if missing |
| `target_col` | `str` | — | variable everything is correlated against |
| `title` | `str` | `None` | main title |
| `subtitle` | `str` | `None` | subtitle |
| `figsize` | `tuple` | `(6, 10)` | figure size |
| `dpi` | `int` | `150` | resolution |

Non-numeric columns are dropped; raises `ValueError` if the target is not
numeric. **Returns** `(fig, ax)`.

## `mutual_inf_plot`

> Needs `scikit-learn`, **not installed** — this module cannot be imported today.

Relevance score = weighted mean of |correlation| and mutual information, both
MinMax-normalised to 0-1. Built for a **binary target**.

| Function | Key parameters | Returns |
| --- | --- | --- |
| `analizar_relevancia_caracteristicas` | `df`, `target`, `predictoras`, `pesos=(0.5, 0.5)`, `top_n`, `graficar=True`, `random_state=42` | `DataFrame` — the entry point, computes and optionally plots |
| `calcular_score_relevancia` | `df`, `target`, `predictoras`, `pesos`, `random_state` | `DataFrame`: `correlacion`, `info_mutua`, `corr_scaled`, `mi_scaled`, `score` |
| `calcular_informacion_mutua` | `df`, `target`, `predictoras`, `discretas=False`, `random_state=42` | `Series`, ascending |
| `calcular_correlacion` | `df`, `target`, `predictoras`, `metodo="pearson"` | `Series`, signed |
| `graficar_score_relevancia` | `df_score`, `columna="score"`, `titulo`, `subtitulo`, `top_n`, `color`, `figsize=(10, 8)`, `mostrar=True` | `(fig, ax)` |

Note `target` defaults to `"is_popular"` in every function — a leftover from
another project, so pass it explicitly.

## `vif_resume`

Variance inflation factor: how much each predictor is explained by the
others. `VIF ≈ 1` is independent, `5–10` is worth noting, `> 10` is the usual
cut-off for dropping a variable. The target is **excluded** — VIF describes
the predictors' relationship to each other, not to what you are predicting.

### `vif_table(df, target_variable, ...)`

| Parameter | Default | Meaning |
| --- | --- | --- |
| `df` | — | DataFrame |
| `target_variable` | — | excluded from the computation; must exist |
| `feature_variables` | `None` | predictors to use; `None` takes every numeric column but the target. Non-numeric columns are always dropped |
| `cache_path` | `None` | pickle to save and reuse the result; `None` disables caching |
| `refresh` | `False` | recompute and overwrite the cache |
| `verbose` | `True` | print the dropped-column and conditioning warnings |

Returns a `DataFrame` sorted by `VIF` descending:

| Column | Meaning |
| --- | --- |
| `Variable` | column name |
| `VIF` | inflation factor, `NaN` if the column was dropped |
| `alto` | `True` when `VIF > 10` |
| `descartada` | why it was dropped, or `None` |

**Caching keys on the inputs, not the output** — you cannot know whether the
result changed without computing it, which is what the cache exists to avoid.
The pickle stores the table beside a SHA-256 of the column names, the target
name and the values. On the next call the fingerprint is recomputed and the
saved table is returned only if it matches. An unreadable or stale-format
pickle counts as a miss, not an error. Hashing costs milliseconds against
roughly a second for the computation.

**Two guards against meaningless output.** Zero-variance columns are dropped
first (their VIF is undefined) and reported in `descartada`. Then the design
matrix — predictors *plus the constant*, which is what the VIF actually uses —
is checked for rank deficiency; failing that, the condition index of the
standardized predictors is checked against 30. Either way you get a warning
naming the suspects, and the table still comes back: deciding what to drop is
the analyst's call. Without these, an exact dependency such as "these
fractions sum to 1" silently produces VIFs around 10¹⁴, which are numerical
noise rather than a measurement.

## `fast_OLS`

> Needs `plotly`, **not installed** — this module cannot be imported today.
> (`statsmodels` 0.15.0 *is* installed.)

### `DiagnosticoOLS(modelo)`

Wraps an already-fitted `sm.OLS(...).fit()` result.

| Method | Parameters | Returns |
| --- | --- | --- |
| `reporte_completo` | `df=None`, `alpha=0.05`, `hc3=False` | **entry point** — runs everything below; returns the influence diagnosis when `df` is given |
| `tabla_coeficientes` | `alpha=0.05`, `hc3=False`, `mostrar=True` | `DataFrame` of coefficients; `hc3=True` adds HC3-robust errors |
| `coeficientes_significativos` | `alpha=0.05`, `hc3=False`, `mostrar=True` | `DataFrame`, significant rows only |
| `bondad_ajuste` | — | `(r2, r2_adj)` |
| `prueba_f_global` | — | `(F, p)` |
| `error_estandar_residual` | — | `float` (RSE) |
| `pruebas_hipotesis` | `mostrar=True` | `DataFrame`: Breusch-Pagan, Durbin-Watson, Jarque-Bera, RESET |
| `decisiones` | `alpha=0.05`, `mostrar=True` | `list[str]`, one verdict per test |
| `metricas_influencia` | — | `(df_influencia, outliers_y, outliers_x, influyentes_cook)` |
| `diagnosticar_influencia_ols` | `df` | `(diag, mask_cook, mask_revisar)` |
| `grafico_residuales_vs_ajustados` | — | plot |
| `grafico_qq` | — | plot |

---

# Shared conventions

The newer plotting modules follow one contract; the two older ones do not.
This is what decides whether you can compose a module into a subplot grid.

| Module | Takes `ax`/`axes` | Returns | Calls `plt.show()` |
| --- | --- | --- | --- |
| `fancy_barplot` | yes | `Axes` | no |
| `fancy_hist` | yes | `Axes` | no |
| `segmented_bars` | yes (`axes`) | `ndarray` of Axes | no |
| `corr_vector` | no | `(fig, ax)` | no |
| `triple_plot` | no | `dict` with `fig` | when `show=True` |
| `bivaraite_boxplot` | **no** | **nothing** | **yes** |
| `category_vs_numeric_plot` | **no** | **nothing** | **yes** |

Other conventions in the modules that follow the contract:

| Convention | Meaning |
| --- | --- |
| `title=None` | auto-generate the title from the column name (`"foo_bar"` → `"Foo Bar"`) |
| `ax=None` | build the figure here; otherwise draw into the caller's axes |
| Figure text | Spanish, as are the `ValueError` messages |
| Identifiers | English |
| Line length | 79 columns, ruff `py313` |

# Requirements

Installed: `pandas`, `numpy`, `scipy`, `matplotlib`, `seaborn`, `statsmodels`,
`IPython`.

| Module | Needs | Status |
| --- | --- | --- |
| `fast_OLS` | `plotly` (plus `statsmodels`, present) | **not importable** |
| `mutual_inf_plot` | `scikit-learn` | **not importable** |

```bash
python -m pip install plotly scikit-learn
```

The other ten modules import cleanly with what is already in `.venv`.
