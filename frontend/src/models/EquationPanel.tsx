// The model's equation, so every forecast can be traced: first its general
// form, with the coefficients the model learned; then the same equation with
// the scenario's values written in, one row per term (coefficient × value =
// contribution), adding up to the forecast. The rows and the sum are the
// API's; nothing is added up here.
import { Stack, Table, Text, Title, Tooltip } from "@mantine/core";
import { useTranslation } from "react-i18next";

import type { Forecast, ModelDetail } from "../api/models";
import { Panel } from "../components/Panel";
import { formatNumber, formatSigned } from "./format";
import classes from "./models.module.css";
import { featureLabel, featureWhat } from "./texts";

/** "EQ", or "(días − 30)" for a variable measured from a reference. */
function variable(label: string, reference: number | null): string {
  return reference === null ? label : `(${label} − ${formatNumber(reference, 0)})`;
}

interface EquationPanelProps {
  model: ModelDetail;
  forecast: Forecast | undefined;
}

export function EquationPanel({ model, forecast }: EquationPanelProps) {
  const { t } = useTranslation();
  const general = [
    `${model.unit} = ${formatNumber(model.intercept, 0)}`,
    ...model.features.map((feature) => {
      const sign = feature.coefficient < 0 ? "−" : "+";
      return `${sign} ${formatNumber(Math.abs(feature.coefficient))} × ${variable(
        featureLabel(t, feature.name),
        feature.reference,
      )}`;
    }),
  ].join(" ");

  return (
    <Panel h="100%">
      <Stack gap="sm">
        <Title order={3} size="h5">
          {t("models.equation.heading")}
        </Title>
        <Text size="xs" c="var(--ce-text-muted)">
          {t("models.equation.general")}
        </Text>
        <p className={classes.equation} data-testid="model-equation">
          {general}
        </p>
        {forecast && (
          <>
            <Text size="xs" c="var(--ce-text-muted)">
              {t("models.equation.filled")}
            </Text>
            <Table.ScrollContainer minWidth={420}>
              <Table className={classes.terms} verticalSpacing={6}>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t("models.equation.term")}</Table.Th>
                    <Table.Th data-numeric>{t("models.equation.coefficient")}</Table.Th>
                    <Table.Th data-numeric>{t("models.equation.value")}</Table.Th>
                    <Table.Th data-numeric>{t("models.equation.contribution")}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  <Table.Tr>
                    <Table.Td>
                      <Tooltip label={featureWhat(t, "intercepto")} multiline w={280} withArrow>
                        <span className={classes.term}>{t("models.equation.intercept")}</span>
                      </Tooltip>
                    </Table.Td>
                    <Table.Td data-numeric>—</Table.Td>
                    <Table.Td data-numeric>—</Table.Td>
                    <Table.Td data-numeric>{formatNumber(forecast.intercept, 0)}</Table.Td>
                  </Table.Tr>
                  {forecast.terms.map((term) => (
                    <Table.Tr key={term.feature}>
                      <Table.Td>
                        <Tooltip label={featureWhat(t, term.feature)} multiline w={280} withArrow>
                          <span className={classes.term}>
                            {variable(featureLabel(t, term.feature), term.reference)}
                          </span>
                        </Tooltip>
                      </Table.Td>
                      <Table.Td data-numeric>{formatSigned(term.coefficient)}</Table.Td>
                      <Table.Td data-numeric>{formatNumber(term.value, 0)}</Table.Td>
                      <Table.Td data-numeric>{formatSigned(term.contribution, 0)}</Table.Td>
                    </Table.Tr>
                  ))}
                  <Table.Tr data-total>
                    <Table.Td>{t("models.equation.total")}</Table.Td>
                    <Table.Td />
                    <Table.Td />
                    <Table.Td data-numeric>
                      {formatNumber(forecast.prediction, 0)} {forecast.unit}
                    </Table.Td>
                  </Table.Tr>
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            <Text size="xs" c="var(--ce-text-muted)">
              {t("models.equation.note")}
            </Text>
          </>
        )}
      </Stack>
    </Panel>
  );
}
