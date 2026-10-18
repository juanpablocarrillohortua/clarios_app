// Types the keys of t() from es.json, so a key that does not exist is a
// compile error rather than a blank on the page.
import "i18next";

import type es from "./es.json";

declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "translation";
    resources: { translation: typeof es };
  }
}
