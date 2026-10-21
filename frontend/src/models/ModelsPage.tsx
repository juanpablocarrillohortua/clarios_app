// "Modelos": the energy baseline models (ADR 0012). The person picks a model,
// builds a scenario (how many EQ, how many days, whether it is a Sunday) and
// sees at once the forecast it gives, where that forecast sits against the
// area's history, and the equation that produced it, term by term.
//
// The models are trained offline and read from the API; the API also does
// the forecast's arithmetic. The scenario's values go to the API a moment
// after the person stops changing them, and the last forecast stays on
// screen until the next arrives: the controls answer at once, the numbers
// follow, nothing blinks. The chosen model lives in the address
// (?modelo=), so a reload or a shared link opens it.
import { Box, Button, Grid, SegmentedControl, Skeleton, Stack, Text, Title } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";

import {
  type ForecastInputs,
  fetchModel,
  fetchModels,
  forecastModel,
  type ModelDetail,
} from "../api/models";
import { useSession } from "../auth/Session";
import { Panel } from "../components/Panel";
import { useDelayed } from "../hooks/useDelayed";
import { EquationPanel } from "./EquationPanel";
import { FeatureGlossary } from "./FeatureGlossary";
import { ForecastChart } from "./ForecastChart";
import { ModelFacts } from "./ModelFacts";
import classes from "./models.module.css";
import { ScenarioPanel } from "./ScenarioPanel";
import { modelName, modelPredicts } from "./texts";

/** How long the scenario waits for the person to stop before it asks the API. */
export const FORECAST_DELAY_MS = 120;
/** How long a forecast may take before the page says it is on its way. */
const UPDATING_AFTER_MS = 300;

export function ModelsPage() {
  const { t } = useTranslation();
  const { api } = useSession();
  const [search, setSearch] = useSearchParams();

  const models = useQuery({
    queryKey: ["models"],
    queryFn: ({ signal }) => fetchModels(api, signal),
    retry: 1,
  });
  const listed = models.data ?? [];
  const asked = search.get("modelo");
  const chosen = listed.find((model) => model.key === asked)?.key ?? listed[0]?.key ?? null;

  function choose(key: string) {
    const next = new URLSearchParams(search);
    next.set("modelo", key);
    setSearch(next, { replace: true });
  }

  return (
    <Box maw={1280}>
      <Stack gap="lg">
        <Stack gap={4}>
          <Title order={2}>{t("models.title")}</Title>
          <Text c="var(--ce-text-secondary)">{t("models.intro")}</Text>
        </Stack>
        {models.isPending && <Skeleton h={36} maw={480} radius="xl" />}
        {models.isError && (
          <Panel tone="error">
            <Stack gap="xs" align="flex-start">
              <Text>{t("models.failed")}</Text>
              <Button variant="light" onClick={() => void models.refetch()}>
                {t("models.retry")}
              </Button>
            </Stack>
          </Panel>
        )}
        {models.isSuccess && listed.length === 0 && (
          <Panel tone="info">
            <Text>{t("models.empty")}</Text>
          </Panel>
        )}
        {chosen && (
          <>
            <SegmentedControl
              aria-label={t("models.picker")}
              data={listed.map((model) => ({ value: model.key, label: modelName(t, model.key) }))}
              value={chosen}
              onChange={choose}
              maw={560}
            />
            {/* A new model is a new view: its scenario starts from its own values. */}
            <ModelView key={chosen} modelKey={chosen} />
          </>
        )}
      </Stack>
    </Box>
  );
}

function ModelView({ modelKey }: { modelKey: string }) {
  const { t } = useTranslation();
  const { api } = useSession();
  const detail = useQuery({
    queryKey: ["model", modelKey],
    queryFn: ({ signal }) => fetchModel(api, modelKey, signal),
    retry: 1,
  });

  if (detail.isPending) {
    return (
      <Stack gap="md" aria-busy="true">
        <Text c="var(--ce-text-muted)">{t("models.loading")}</Text>
        <Skeleton h={320} radius="md" />
      </Stack>
    );
  }
  if (detail.isError) {
    return (
      <Panel tone="error">
        <Stack gap="xs" align="flex-start">
          <Text>{t("models.failed")}</Text>
          <Button variant="light" onClick={() => void detail.refetch()}>
            {t("models.retry")}
          </Button>
        </Stack>
      </Panel>
    );
  }
  return <Scenario model={detail.data} />;
}

/** The values a scenario starts from: the API's, for every variable entered. */
function defaultsOf(model: ModelDetail): ForecastInputs {
  const inputs: ForecastInputs = {};
  for (const feature of model.features) {
    if (feature.entered && feature.default !== null) inputs[feature.name] = feature.default;
  }
  return inputs;
}

function Scenario({ model }: { model: ModelDetail }) {
  const { t } = useTranslation();
  const { api } = useSession();
  const [inputs, setInputs] = useState<ForecastInputs>(() => defaultsOf(model));
  const [sent] = useDebouncedValue(inputs, FORECAST_DELAY_MS);

  const forecast = useQuery({
    queryKey: ["model-forecast", model.key, sent],
    queryFn: ({ signal }) => forecastModel(api, model.key, sent, signal),
    placeholderData: keepPreviousData,
    retry: false,
  });
  const waiting = forecast.isFetching || sent !== inputs;
  const updating = useDelayed(waiting, UPDATING_AFTER_MS);
  const current = forecast.data;

  return (
    <Stack gap="lg" className={classes.view}>
      <Text className={classes.predicts}>{modelPredicts(t, model.key)}</Text>
      <Grid gap="lg" align="stretch">
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <ScenarioPanel
            model={model}
            inputs={inputs}
            onChange={(name, value) => setInputs((before) => ({ ...before, [name]: value }))}
            onReset={() => setInputs(defaultsOf(model))}
            forecast={current}
            updating={updating}
            failed={forecast.isError}
          />
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <Panel h="100%">
            <Stack gap="sm">
              <Title order={3} size="h5">
                {t("models.chart.heading")}
              </Title>
              <ForecastChart
                history={model.history}
                granularity={model.granularity}
                unit={model.unit}
                alertBand={model.metrics.alert_band}
                scenario={
                  current
                    ? {
                        period: current.period,
                        prediction: current.prediction,
                        low: current.low,
                        high: current.high,
                      }
                    : null
                }
              />
            </Stack>
          </Panel>
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 7 }}>
          <EquationPanel model={model} forecast={current} />
        </Grid.Col>
        <Grid.Col span={{ base: 12, lg: 5 }}>
          <FeatureGlossary model={model} />
        </Grid.Col>
        <Grid.Col span={12}>
          <ModelFacts model={model} />
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
