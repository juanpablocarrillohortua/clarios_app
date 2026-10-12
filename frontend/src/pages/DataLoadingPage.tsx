// The data-loading section. Empty until increments 4 to 9 of
// docs/frontend-plan.md build the loading wizard here.
import { useTranslation } from "react-i18next";

import { SectionPlaceholder } from "../components/SectionPlaceholder";

export function DataLoadingPage() {
  const { t } = useTranslation();
  return <SectionPlaceholder title={t("loads.title")} text={t("loads.empty")} />;
}
