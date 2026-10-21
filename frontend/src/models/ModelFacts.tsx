// How a model was trained, and how well it forecasts: the window it learned
// from, when, from which sources, and its figures side by side. Training
// figures are each fold's fit on its own data; validation figures come from
// forecasts that never saw their own value. The second column is the one
// that says how far to trust a forecast.
import { List, Stack, Table, Text, Title } from "@mantine/core";
import { useTranslation } from "react-i18next";

import type { ModelDetail } from "../api/models";
import { Panel } from "../components/Panel";
import { formatDate, formatNumber, formatPercent, formatTick } from "./format";
import classes from "./models.module.css";

export function ModelFacts({ model }: { model: ModelDetail }) {
  const { t } = useTranslation();
  const metrics = model.metrics;
  const rows: [string, string, string][] = [
    [
      t("models.facts.r2"),
      formatNumber(metrics.train_r2, 2),
      formatNumber(metrics.validation_r2, 2),
    ],
    [
      t("models.facts.wape"),
      formatPercent(metrics.train_wape),
      formatPercent(metrics.validation_wape),
    ],
    [t("models.facts.mase"), "—", formatNumber(metrics.validation_mase, 2)],
    [t("models.facts.naive"), "—", formatPercent(metrics.naive_wape)],
  ];

  return (
    <Panel>
      <Stack gap="sm">
        <Title order={3} size="h5">
          {t("models.facts.heading")}
        </Title>
        <Text size="sm" c="var(--ce-text-secondary)">
          {t("models.facts.window", {
            start: formatTick(model.training_start, model.granularity),
            end: formatTick(model.training_end, model.granularity),
            count: model.n_training,
          })}
          {" · "}
          {t("models.facts.trainedAt", { when: formatDate(model.trained_at) })}
        </Text>
        <Table.ScrollContainer minWidth={420}>
          <Table className={classes.terms} verticalSpacing={6}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t("models.facts.metric")}</Table.Th>
                <Table.Th data-numeric>{t("models.facts.training")}</Table.Th>
                <Table.Th data-numeric>{t("models.facts.validation")}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map(([name, training, validation]) => (
                <Table.Tr key={name}>
                  <Table.Td>{name}</Table.Td>
                  <Table.Td data-numeric>{training}</Table.Td>
                  <Table.Td data-numeric>{validation}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        <Text size="xs" c="var(--ce-text-muted)">
          {t("models.facts.explain", { count: metrics.validation_periods })}
        </Text>
        <Text size="xs" fw={600} c="var(--ce-text-secondary)">
          {t("models.facts.sources")}
        </Text>
        <List size="xs" c="var(--ce-text-muted)">
          {model.sources.map((source) => (
            <List.Item key={source}>{source}</List.Item>
          ))}
        </List>
      </Stack>
    </Panel>
  );
}
