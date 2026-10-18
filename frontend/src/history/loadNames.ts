// How a load is named on screen: by its dataset, when the server knows which
// (every load from the app, and a command-line load that landed one sheet a
// spec describes); otherwise by the sheets it landed. Who loaded it: a
// person's display name, or the command line.
import type { TFunction } from "i18next";

import type { Dataset } from "../api/datasets";
import type { LoadSummary } from "../api/loads";
import { named } from "../loads/texts";

export function loadName(t: TFunction, datasets: Dataset[] | undefined, load: LoadSummary): string {
  const dataset = datasets?.find((candidate) => candidate.key === load.dataset_key);
  if (dataset) return named(t, dataset.label, dataset.key);
  if (load.dataset_key) return load.dataset_key;
  return t("history.sheets", { names: load.sheets.map((sheet) => sheet.name).join(", ") });
}

export function loaderName(t: TFunction, load: LoadSummary): string {
  return load.origin === "cli"
    ? t("history.commandLine")
    : (load.uploaded_by_name ?? t("history.origins.app"));
}
