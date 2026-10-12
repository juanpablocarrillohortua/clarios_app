// The dashboard section. Empty until increment 3 of docs/frontend-plan.md
// embeds the Power BI report here.
import { useTranslation } from "react-i18next";

import { SectionPlaceholder } from "../components/SectionPlaceholder";

export function DashboardPage() {
  const { t } = useTranslation();
  return <SectionPlaceholder title={t("dashboard.title")} text={t("dashboard.empty")} />;
}
