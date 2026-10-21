# 12. The web app shows the energy baseline models, served by the API

- **Status:** Proposed. Built on 2026-10-20; the owner's check in a signed-in
  browser is pending.
- **Date:** 2026-10-20
- **Deciders:** Platform team, with the owner
- **Amends:** [ADR 0007](0007-figures-reach-people-through-power-bi.md)
  (figures reach people through Power BI only)

## Context

Section 7 of `notebooks/further_search.ipynb` showed that an area's energy is
well predicted from its production:

- a straight line on the equivalent batteries (EQ) and the days of the month;
- validated one step ahead with `TimeSeriesSplit`, it errs by about 5 % a
  month, where "same as last month" errs by 8 % to 10 %.

The owner asked for a section of the web app where a person:

- picks one of these models;
- enters the production planned;
- sees the forecast, where it sits against the area's history, and the
  equation that produced it, term by term.

That conflicts with ADR 0007, which says that figures reach people through the
`reporting` views and Power BI, and that the API serves no analytical figure.
Its reasons are two:

- a figure served from two places can show two values, with two levels of
  freshness;
- it would have two access models.

There are two more constraints:

- **The energy data is not in the database.** It is in the plant's workbooks
  (`data/Base_de_datos.xlsx`, the SCADA files), which no layer loads yet.
- **The fit needs scikit-learn and statsmodels,** an optional extra that the
  api image does not install.

## Decision

1. **One exception to ADR 0007, for the baseline models only.**
   - **What the API serves:**
     - `GET /api/models`, the models in force;
     - `GET /api/models/{model_key}`, one model in full;
     - `POST /api/models/{model_key}/forecast`, the forecast of the next
       period for values a person enters.
   - **Who may call them:** holders of the new permission `models:view`
     (viewer, operator, engineer, admin).
   - **Why a report cannot do it:** it cannot answer a value typed a moment
     ago. Everything else (KPIs, equivalent batteries) still reaches people
     through Power BI.
2. **Three models.** Ensamble has none: its R² is too low for a forecast worth
   showing.

   | Key | Predicts | From |
   |---|---|---|
   | `orec_mensual` | OREC's total kWh of a month | EQ, days of the month (last 36 months: its relation broke in Sep-2023) |
   | `fa_mensual` | F&A's total kWh of a month | EQ, days of the month (all history: no break) |
   | `fa_diario` | The kWh of a day of F&A's three rectifiers | EQ of the day, Sunday, no production (derived from EQ) |
3. **Trained offline, kept in Gold, evaluated in the API.**
   - **Training:** `energy models train` (`make models-train`) runs on the
     host, where the workbooks and the `analytics` extra are. It reads the
     energy from the workbooks and the daily production from Gold.
   - **The fit:** each model is fitted as the notebook does, a scikit-learn
     `Pipeline` validated with `TimeSeriesSplit`.
   - **Storage:** the result goes to `gold.baseline_models` (revision `0010`),
     superseding the model in force; nothing is overwritten.
   - **The API** reads that table and evaluates the equation in plain Python.
     The api image gets no statistics library and no workbook.
4. **Every number is traceable.** A model carries:
   - its equation, its coefficients and their Newey-West p-values;
   - its training and validation figures, kept apart;
   - the history it was judged on.

   A forecast carries every term of the equation with the values filled in,
   and names the variables outside the range the model was trained on. The
   app draws what the API sends and computes nothing.

## Consequences

- **A second path for figures, deliberately narrow.** A model's figures are
  as fresh as its last training, which the page shows. They are not the
  report's figures and do not compete with them: the report shows what
  happened, the models what would happen.
- **Retraining is a command.** New energy data reaches the models only when
  someone runs `make models-train`. That should be scheduled once energy
  readings are loaded into the platform (Phase 3/4); until then the
  workbooks are the source.
- **The daily model differs slightly from the notebook.** The notebook
  interpolated three days without SCADA readings; training does not invent a
  day. Its coefficients therefore move a little:

  | | Notebook | Training |
  |---|---|---|
  | kWh per EQ | 15.39 | 15.24 |
  | Validation WAPE | 12.28 % | 12.35 % |

  The monthly models match the notebook to the last digit.
- **A spec changed in the code makes a stored model unservable** until it is
  trained again: the store refuses a model whose variables are not those of
  its spec.
- **Power BI does not see the models.** A `reporting` view of them can be
  added later, granted to `reporting_reader` like the others.
