// What each variable of the model means: what it is and where it comes from,
// what its coefficient means for the plant ("each extra EQ adds 4.79 kWh"),
// and whether the data show the effect at all. The intercept is listed too:
// it is the energy the area uses without producing.
import { Stack, Text, Title } from "@mantine/core";
import { useTranslation } from "react-i18next";

import type { ModelDetail } from "../api/models";
import { Panel } from "../components/Panel";
import { formatNumber, formatP, formatSigned } from "./format";
import classes from "./models.module.css";
import { featureEffect, featureLabel, featureWhat } from "./texts";

interface Entry {
  name: string;
  value: string;
  pValue: number;
  significant: boolean;
}

export function FeatureGlossary({ model }: { model: ModelDetail }) {
  const { t } = useTranslation();
  const entries: Entry[] = [
    {
      name: "intercepto",
      value: formatNumber(model.intercept, 0),
      pValue: model.intercept_p_value,
      significant: model.intercept_significant,
    },
    ...model.features.map((feature) => ({
      name: feature.name,
      value: formatSigned(feature.coefficient),
      pValue: feature.p_value,
      significant: feature.significant,
    })),
  ];

  return (
    <Panel h="100%">
      <Stack gap="sm">
        <Title order={3} size="h5">
          {t("models.features.heading")}
        </Title>
        <dl className={classes.glossary}>
          {entries.map((entry) => (
            <div key={entry.name}>
              <dt>{featureLabel(t, entry.name)}</dt>
              <dd>
                <span>{featureWhat(t, entry.name)}</span>
                <span className={classes.effect}>{featureEffect(t, entry.name, entry.value)}</span>
                <Text span size="xs" c="var(--ce-text-muted)">
                  {entry.significant
                    ? t("models.features.significant", { p: formatP(entry.pValue) })
                    : t("models.features.notSignificant", { p: formatP(entry.pValue) })}
                </Text>
              </dd>
            </div>
          ))}
        </dl>
      </Stack>
    </Panel>
  );
}
