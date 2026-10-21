// The energy baseline models (src/energy/api/routers/models.py, ADR 0012): the
// ones in force, one in full, and its forecast for the values a person
// enters. The API does every calculation, the forecast's arithmetic included;
// the app draws what it is given.
import type { Api } from "./client";

export type Granularity = "month" | "day";

/** One model in force. */
export interface ModelSummary {
  key: string;
  area: string;
  granularity: Granularity;
  target: string;
  unit: string;
  trained_at: string;
  training_start: string;
  training_end: string;
  n_training: number;
  coverage: number;
  validation_wape: number;
  validation_mase: number;
}

/** One variable of a model, and what the model learned about it. */
export interface ModelFeature {
  name: string;
  kind: "numeric" | "binary";
  /** False for a variable that follows from another (sin_eq follows from eq). */
  entered: boolean;
  minimum: number | null;
  maximum: number | null;
  /** The value the variable is measured from in the equation (días from 30). */
  reference: number | null;
  training_minimum: number | null;
  training_maximum: number | null;
  /** Where a forecast starts; null for a derived variable. */
  default: number | null;
  coefficient: number;
  p_value: number;
  significant: boolean;
}

/** Training figures are the folds' own fit; validation, the one-step forecasts. */
export interface ModelMetrics {
  train_r2: number;
  train_wape: number;
  validation_r2: number;
  validation_mae: number;
  validation_wape: number;
  validation_mase: number;
  naive_wape: number;
  diebold_mariano_p: number;
  alert_band: number;
  validation_periods: number;
}

export interface HistoryPoint {
  period: string;
  actual: number;
  /** The validation's one-step forecast; null before the validation window. */
  predicted: number | null;
  naive: number | null;
  inputs: Record<string, number>;
}

export interface ModelDetail extends ModelSummary {
  sources: string[];
  equation: string;
  intercept: number;
  intercept_p_value: number;
  intercept_significant: boolean;
  features: ModelFeature[];
  metrics: ModelMetrics;
  next_period: string;
  history: HistoryPoint[];
}

export interface ForecastTerm {
  feature: string;
  coefficient: number;
  value: number;
  reference: number | null;
  contribution: number;
}

export interface Forecast {
  key: string;
  unit: string;
  period: string;
  prediction: number;
  low: number;
  high: number;
  intercept: number;
  terms: ForecastTerm[];
  per_unit: number | null;
  outside_training: string[];
}

/** The values a person enters, by variable name. */
export type ForecastInputs = Record<string, number>;

export function fetchModels(api: Api, signal?: AbortSignal): Promise<ModelSummary[]> {
  return api.get<ModelSummary[]>("/models", signal);
}

export function fetchModel(api: Api, key: string, signal?: AbortSignal): Promise<ModelDetail> {
  return api.get<ModelDetail>(`/models/${encodeURIComponent(key)}`, signal);
}

export async function forecastModel(
  api: Api,
  key: string,
  inputs: ForecastInputs,
  signal?: AbortSignal,
): Promise<Forecast> {
  const sent = await api.postJson<Forecast>(
    `/models/${encodeURIComponent(key)}/forecast`,
    { inputs },
    signal,
  );
  return sent.body;
}
