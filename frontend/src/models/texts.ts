// The words of what the API names by key: a model, a variable. The API sends
// keys (orec_mensual, eq, dias) and numbers; the sentences are in es.json,
// under models.names, models.predicts and models.features. A test of the API
// checks that every model and variable it can send has its sentences.
//
// The keys arrive at run time, so this module looks them up untyped, as
// loads/texts.ts does, and a key es.json lacks reads as a plain fallback.
import type { TFunction } from "i18next";
import i18n from "i18next";

type Lookup = (key: string, options?: Record<string, unknown>) => string;

function lookup(t: TFunction): Lookup {
  return t as unknown as Lookup;
}

function text(t: TFunction, key: string, fallback: string, options?: Record<string, unknown>) {
  return i18n.exists(key) ? lookup(t)(key, options) : fallback;
}

/** The name of a model, for the picker: "OREC · mensual". */
export function modelName(t: TFunction, key: string): string {
  return text(t, `models.names.${key}`, key);
}

/** What a model predicts, in one or two sentences. */
export function modelPredicts(t: TFunction, key: string): string {
  return text(t, `models.predicts.${key}`, "");
}

/** The short name of a variable: "EQ", "Días del mes". */
export function featureLabel(t: TFunction, name: string): string {
  return text(t, `models.features.${name}.label`, name);
}

/** What a variable is and where it comes from. */
export function featureWhat(t: TFunction, name: string): string {
  return text(t, `models.features.${name}.what`, "");
}

/** What a variable's coefficient means for the plant, with the coefficient written in. */
export function featureEffect(t: TFunction, name: string, value: string): string {
  return text(t, `models.features.${name}.effect`, value, { value });
}
