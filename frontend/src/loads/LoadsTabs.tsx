// The two pages of the loading section, side by side under its heading:
// "Nueva carga" and "Historial". Links, so the browser's back button and a
// new tab work as on any page. The current one is marked for screen readers
// too (aria-current), never by colour alone. A draft's page belongs to
// "Nueva carga"; a load's detail to "Historial".
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router";

import { paths } from "../paths";
import classes from "../styles/app.module.css";

export function LoadsTabs() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  const inHistory = pathname === paths.history || pathname.startsWith(`${paths.history}/`);
  return (
    <nav aria-label={t("loads.tabs.label")} className={classes.subnav}>
      <Link
        to={paths.loads}
        className={classes.subnavLink}
        aria-current={inHistory ? undefined : "page"}
      >
        {t("loads.tabs.new")}
      </Link>
      <Link
        to={paths.history}
        className={classes.subnavLink}
        aria-current={inHistory ? "page" : undefined}
      >
        {t("loads.tabs.history")}
      </Link>
    </nav>
  );
}
