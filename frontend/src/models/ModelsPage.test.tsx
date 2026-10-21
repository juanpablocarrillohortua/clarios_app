// "Modelos": who sees it, picking a model, building a scenario and reading
// the forecast, its equation and what each variable means. The stand-in API
// answers as the real one does; its forecast is a fixed line, so the test
// can tell which values were sent. The app computes nothing of its own.
import { configure, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  Forecast,
  ForecastInputs,
  ModelDetail,
  ModelFeature,
  ModelSummary,
} from "../api/models";
import es from "../i18n/es.json";
import { paths } from "../paths";
import { json, person, renderAppAt, stubApi, viewer } from "../test/render";

const texts = es.models;

afterEach(() => {
  vi.unstubAllGlobals();
});

configure({ asyncUtilTimeout: 5000 });

const summary = (key: string, granularity: "month" | "day"): ModelSummary => ({
  key,
  area: key.startsWith("orec") ? "OREC" : "F&A",
  granularity,
  target: "kwh",
  unit: "kWh",
  trained_at: "2026-10-20T12:00:00Z",
  training_start: granularity === "month" ? "2026-04-01" : "2026-09-24",
  training_end: granularity === "month" ? "2026-06-01" : "2026-09-30",
  n_training: 3,
  coverage: 1,
  validation_wape: 4.89,
  validation_mase: 0.48,
});

const metrics = {
  train_r2: 0.75,
  train_wape: 3.82,
  validation_r2: 0.91,
  validation_mae: 27085,
  validation_wape: 4.89,
  validation_mase: 0.48,
  naive_wape: 10.21,
  diebold_mariano_p: 0.027,
  alert_band: 50000,
  validation_periods: 2,
};

const orec: ModelDetail = {
  ...summary("orec_mensual", "month"),
  sources: ["data/Base_de_datos.xlsx, hoja OREC"],
  equation: "kwh = 100,000 + 5.00 * eq + 6,000 * (dias - 30)",
  intercept: 100000,
  intercept_p_value: 0.0001,
  intercept_significant: true,
  features: [
    {
      name: "eq",
      kind: "numeric",
      entered: true,
      minimum: 0,
      maximum: null,
      reference: null,
      training_minimum: 40000,
      training_maximum: 90000,
      default: 70000,
      coefficient: 5,
      p_value: 0.00001,
      significant: true,
    },
    {
      name: "dias",
      kind: "numeric",
      entered: true,
      minimum: 28,
      maximum: 31,
      reference: 30,
      training_minimum: 28,
      training_maximum: 31,
      default: 31,
      coefficient: 6000,
      p_value: 0.108,
      significant: false,
    },
  ],
  metrics,
  next_period: "2026-07-01",
  history: [
    { period: "2026-04-01", actual: 400000, predicted: null, naive: null, inputs: { eq: 60000 } },
    {
      period: "2026-05-01",
      actual: 450000,
      predicted: 451000,
      naive: 400000,
      inputs: { eq: 70000 },
    },
    {
      period: "2026-06-01",
      actual: 500000,
      predicted: 501000,
      naive: 450000,
      inputs: { eq: 80000 },
    },
  ],
};

const daily: ModelDetail = {
  ...summary("fa_diario", "day"),
  sources: ["data/SCADA-Base de datos F&A.xlsx"],
  equation: "kwh = 10,000 + 15.00 * eq - 8,000 * domingo - 1,500 * sin_eq",
  intercept: 10000,
  intercept_p_value: 0.0001,
  intercept_significant: true,
  features: [
    {
      ...(orec.features[0] as ModelFeature),
      training_maximum: 3000,
      default: 2050,
      coefficient: 15,
    },
    {
      name: "domingo",
      kind: "binary",
      entered: true,
      minimum: 0,
      maximum: 1,
      reference: null,
      training_minimum: 0,
      training_maximum: 1,
      default: 0,
      coefficient: -8000,
      p_value: 0.012,
      significant: true,
    },
    {
      name: "sin_eq",
      kind: "binary",
      entered: false,
      minimum: 0,
      maximum: 1,
      reference: null,
      training_minimum: 0,
      training_maximum: 1,
      default: null,
      coefficient: -1500,
      p_value: 0.544,
      significant: false,
    },
  ],
  metrics: { ...metrics, alert_band: 9000, validation_periods: 30 },
  next_period: "2026-10-01",
  history: [
    { period: "2026-09-29", actual: 40000, predicted: 41000, naive: 38000, inputs: { eq: 2000 } },
    { period: "2026-09-30", actual: 38500, predicted: 39000, naive: 40000, inputs: { eq: 1900 } },
  ],
};

/** What the stand-in API forecasts: the models' lines, as the real API would compute them. */
function forecastOf(key: string, inputs: ForecastInputs): Forecast {
  if (key === "orec_mensual") {
    const eq = inputs.eq ?? 0;
    const days = inputs.dias ?? 30;
    const terms = [
      { feature: "eq", coefficient: 5, value: eq, reference: null, contribution: 5 * eq },
      {
        feature: "dias",
        coefficient: 6000,
        value: days,
        reference: 30,
        contribution: 6000 * (days - 30),
      },
    ];
    const prediction = 100000 + 5 * eq + 6000 * (days - 30);
    return {
      key,
      unit: "kWh",
      period: "2026-07-01",
      prediction,
      low: prediction - 50000,
      high: prediction + 50000,
      intercept: 100000,
      terms,
      per_unit: eq > 0 ? prediction / eq : null,
      outside_training: eq > 90000 ? ["eq"] : [],
    };
  }
  const eq = inputs.eq ?? 0;
  const sunday = inputs.domingo ?? 0;
  const idle = eq <= 0 ? 1 : 0;
  const prediction = 10000 + 15 * eq - 8000 * sunday - 1500 * idle;
  return {
    key,
    unit: "kWh",
    period: "2026-10-01",
    prediction,
    low: prediction - 9000,
    high: prediction + 9000,
    intercept: 10000,
    terms: [
      { feature: "eq", coefficient: 15, value: eq, reference: null, contribution: 15 * eq },
      {
        feature: "domingo",
        coefficient: -8000,
        value: sunday,
        reference: null,
        contribution: -8000 * sunday,
      },
      {
        feature: "sin_eq",
        coefficient: -1500,
        value: idle,
        reference: null,
        contribution: -1500 * idle,
      },
    ],
    per_unit: eq > 0 ? prediction / eq : null,
    outside_training: [],
  };
}

function stubModels(
  listed: ModelSummary[] = [summary("orec_mensual", "month"), summary("fa_diario", "day")],
) {
  const forecast = (key: string) => (init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as { inputs: ForecastInputs };
    return json(forecastOf(key, body.inputs));
  };
  return stubApi({
    me: viewer,
    answers: {
      "/api/models": () => json(listed),
      "/api/models/orec_mensual": () => json(orec),
      "/api/models/fa_diario": () => json(daily),
      "/api/models/orec_mensual/forecast": forecast("orec_mensual"),
      "/api/models/fa_diario/forecast": forecast("fa_diario"),
    },
  });
}

/** The inputs of every forecast the page asked for, in order. */
function sentInputs(fetchStub: ReturnType<typeof stubApi>, key: string): ForecastInputs[] {
  return fetchStub.mock.calls
    .filter(([input]) => String(input) === `/api/models/${key}/forecast`)
    .map(
      ([, init]) =>
        (JSON.parse(String((init as RequestInit).body)) as { inputs: ForecastInputs }).inputs,
    );
}

describe("who sees the models", () => {
  it("is not in the navigation without models:view", async () => {
    stubApi({ me: person(["viewer"], ["chat:use", "dashboard:view"]) });
    renderAppAt(paths.models);

    expect(await screen.findByRole("heading", { name: es.forbidden.title })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: es.nav.models })).toBeNull();
  });

  it("says so when no model has been trained", async () => {
    stubModels([]);
    renderAppAt(paths.models);

    expect(await screen.findByText(texts.empty)).toBeInTheDocument();
  });
});

describe("a monthly model", () => {
  it("opens the first model, says what it predicts and forecasts from the API's values", async () => {
    const fetchStub = stubModels();
    renderAppAt(paths.models);

    expect(await screen.findByRole("heading", { name: texts.title })).toBeInTheDocument();
    expect(await screen.findByText(texts.predicts.orec_mensual)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: texts.names.orec_mensual })).toBeChecked();

    // The defaults the API gave: 70,000 EQ in a 31-day July.
    expect(await screen.findByText("456.000")).toBeInTheDocument();
    expect(sentInputs(fetchStub, "orec_mensual")[0]).toEqual({ eq: 70000, dias: 31 });
    expect(screen.getByText(/julio de 2026/)).toBeInTheDocument();
  });

  it("writes the equation in general and with the scenario's values", async () => {
    stubModels();
    renderAppAt(paths.models);

    expect(await screen.findByTestId("model-equation")).toHaveTextContent(
      "kWh = 100.000 + 5,00 × EQ + 6.000 × (Días del mes − 30)",
    );
    const table = (await screen.findByText(texts.equation.contribution)).closest(
      "table",
    ) as HTMLElement;
    await waitFor(() => expect(within(table).getByText("+350.000")).toBeInTheDocument());
    // Six thousand twice: the coefficient of a day, and one day over 30.
    expect(within(table).getAllByText("+6.000")).toHaveLength(2);
    expect(within(table).getAllByText(/456\.000/).length).toBeGreaterThan(0);
  });

  it("sends the new value when the person changes the EQ, and warns of an extrapolation", async () => {
    const user = userEvent.setup();
    const fetchStub = stubModels();
    renderAppAt(paths.models);

    const box = await screen.findByRole("textbox", { name: es.models.features.eq.label });
    await user.clear(box);
    await user.type(box, "95000");

    await waitFor(() =>
      expect(sentInputs(fetchStub, "orec_mensual").at(-1)).toEqual({ eq: 95000, dias: 31 }),
    );
    expect(await screen.findByText(/Fuera del rango con que se entrenó/)).toBeInTheDocument();
  });

  it("explains every variable, with its coefficient and whether it is significant", async () => {
    stubModels();
    renderAppAt(paths.models);

    expect(await screen.findByText(texts.features.heading)).toBeInTheDocument();
    expect(
      screen.getByText("Cada EQ adicional cambia el consumo en +5,00 kWh."),
    ).toBeInTheDocument();
    expect(screen.getByText("100.000 kWh fijos.")).toBeInTheDocument();
    expect(screen.getByText(/Efecto no significativo \(p = 0,108\)/)).toBeInTheDocument();
  });

  it("shows training and validation figures apart", async () => {
    stubModels();
    renderAppAt(paths.models);

    const row = (await screen.findByText(texts.facts.wape)).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("3,82 %")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("4,89 %")).toBeInTheDocument();
  });
});

describe("the daily model", () => {
  it("is chosen in the picker, kept in the address, and derives a day without production", async () => {
    const user = userEvent.setup();
    const fetchStub = stubModels();
    renderAppAt(`${paths.models}?modelo=fa_diario`);

    expect(await screen.findByText(texts.predicts.fa_diario)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: texts.names.fa_diario })).toBeChecked();
    expect(await screen.findByText("40.750")).toBeInTheDocument();
    expect(sentInputs(fetchStub, "fa_diario")[0]).toEqual({ eq: 2050, domingo: 0 });

    await user.click(screen.getByRole("switch", { name: es.models.features.domingo.label }));
    await waitFor(() =>
      expect(sentInputs(fetchStub, "fa_diario").at(-1)).toEqual({ eq: 2050, domingo: 1 }),
    );
    // "Sin producción" is never sent: the API derives it from the EQ.
    expect(sentInputs(fetchStub, "fa_diario").every((inputs) => !("sin_eq" in inputs))).toBe(true);
    expect(screen.getByText("Se deduce de los EQ: no")).toBeInTheDocument();
  });

  it("switches from the monthly model", async () => {
    const user = userEvent.setup();
    stubModels();
    renderAppAt(paths.models);

    await screen.findByText(texts.predicts.orec_mensual);
    await user.click(screen.getByRole("radio", { name: texts.names.fa_diario }));

    expect(await screen.findByText(texts.predicts.fa_diario)).toBeInTheDocument();
    expect(screen.queryByText(texts.predicts.orec_mensual)).toBeNull();
  });
});
