// Every text the user sees comes from es.json, by key. The interface is
// Spanish; keys, code and comments are English. Importing this module once, at
// the entry point, is enough.
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import es from "./es.json";

void i18n.use(initReactI18next).init({
  resources: { es: { translation: es } },
  lng: "es",
  fallbackLng: "es",
  // The texts are bundled, so they are ready before the first render.
  initAsync: false,
  // React already escapes what it renders.
  interpolation: { escapeValue: false },
});

export default i18n;
