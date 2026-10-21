// The forecast, beside the scenario that produces it: what the person changes
// sits next to what it changes. The big number is the forecast of the next
// period; under it, its 90 % band and its kWh per EQ. Below, one control per
// variable the person enters, each with what the variable means; a variable
// that follows from another (sin_eq) is shown, not entered.
//
// Every number shown comes from the API. A value outside the range the model
// was trained on is allowed, and the API says which: the panel then warns
// that the forecast is an extrapolation.
import {
  Button,
  Group,
  NumberInput,
  SegmentedControl,
  Slider,
  Stack,
  Switch,
  Text,
  Title,
} from "@mantine/core";
import { useTranslation } from "react-i18next";

import type { Forecast, ForecastInputs, ModelDetail, ModelFeature } from "../api/models";
import { Panel } from "../components/Panel";
import { formatNumber, formatPeriod } from "./format";
import classes from "./models.module.css";
import { featureLabel, featureWhat } from "./texts";

const MONTH_LENGTHS = ["28", "29", "30", "31"];
/** How far past the trained range the slider reaches, so an extrapolation can be tried. */
const SLIDER_REACH = 1.3;

/** A slider step that keeps about a thousand stops over the range. */
function stepFor(maximum: number): number {
  return Math.max(1, 10 ** (Math.floor(Math.log10(Math.max(maximum, 1))) - 3));
}

interface ScenarioPanelProps {
  model: ModelDetail;
  inputs: ForecastInputs;
  onChange: (name: string, value: number) => void;
  onReset: () => void;
  forecast: Forecast | undefined;
  updating: boolean;
  failed: boolean;
}

export function ScenarioPanel({
  model,
  inputs,
  onChange,
  onReset,
  forecast,
  updating,
  failed,
}: ScenarioPanelProps) {
  const { t } = useTranslation();
  const period = forecast?.period ?? model.next_period;
  const outside = forecast?.outside_training ?? [];

  return (
    <Panel h="100%">
      <Stack gap="md">
        <Stack gap={4}>
          <Text className={classes.eyebrow}>
            {model.granularity === "month"
              ? t("models.forecast.month", { period: formatPeriod(period, "month") })
              : t("models.forecast.day", { period: formatPeriod(period, "day") })}
          </Text>
          <p className={classes.figure} data-updating={updating || undefined} aria-live="polite">
            {forecast ? formatNumber(forecast.prediction, 0) : "—"}
            <span className={classes.unit}>{model.unit}</span>
          </p>
          {forecast && (
            <Text size="sm" c="var(--ce-text-secondary)" className={classes.numbers}>
              {t("models.forecast.band", {
                low: formatNumber(forecast.low, 0),
                high: formatNumber(forecast.high, 0),
                unit: model.unit,
              })}
              {" · "}
              {forecast.per_unit === null
                ? t("models.forecast.noProduction")
                : t("models.forecast.perUnit", {
                    value: formatNumber(forecast.per_unit, 2),
                    unit: model.unit,
                  })}
            </Text>
          )}
          <Text size="xs" c="var(--ce-text-muted)">
            {t("models.forecast.bandHint")}
          </Text>
        </Stack>

        {failed && (
          <Panel tone="error" p="sm">
            <Text size="sm">{t("models.forecast.failed")}</Text>
          </Panel>
        )}
        {outside.length > 0 && (
          <Panel tone="warning" p="sm">
            <Text size="sm">
              {t("models.forecast.outside", {
                features: outside.map((name) => featureLabel(t, name)).join(", "),
              })}
            </Text>
          </Panel>
        )}

        <Stack gap="sm">
          <Group justify="space-between" align="baseline">
            <Title order={3} size="h5">
              {t("models.inputs.heading")}
            </Title>
            <Button variant="subtle" size="compact-sm" onClick={onReset}>
              {t("models.inputs.reset")}
            </Button>
          </Group>
          <Text size="xs" c="var(--ce-text-muted)">
            {t("models.inputs.hint")}
          </Text>
          {model.features.map((feature) => (
            <Control
              key={feature.name}
              feature={feature}
              value={inputs[feature.name]}
              derivedValue={forecast?.terms.find((term) => term.feature === feature.name)?.value}
              onChange={(value) => onChange(feature.name, value)}
            />
          ))}
        </Stack>
      </Stack>
    </Panel>
  );
}

interface ControlProps {
  feature: ModelFeature;
  value: number | undefined;
  derivedValue: number | undefined;
  onChange: (value: number) => void;
}

function Control({ feature, value, derivedValue, onChange }: ControlProps) {
  const { t } = useTranslation();
  const label = featureLabel(t, feature.name);
  const what = featureWhat(t, feature.name);

  if (!feature.entered) {
    const yes = derivedValue === 1;
    return (
      <Stack gap={2}>
        <Text size="sm" fw={500}>
          {label}
        </Text>
        <Text size="sm" c="var(--ce-text-secondary)">
          {t("models.inputs.derived", { value: t(yes ? "models.inputs.yes" : "models.inputs.no") })}
        </Text>
        <Text size="xs" c="var(--ce-text-muted)">
          {what}
        </Text>
      </Stack>
    );
  }

  if (feature.kind === "binary") {
    // The meaning sits under the switch, not in its label, so the switch's
    // name stays the variable's.
    const described = `models-${feature.name}-what`;
    return (
      <Stack gap={4}>
        <Switch
          label={label}
          aria-describedby={described}
          checked={value === 1}
          onChange={(event) => onChange(event.currentTarget.checked ? 1 : 0)}
        />
        <Text id={described} size="xs" c="var(--ce-text-muted)">
          {what}
        </Text>
      </Stack>
    );
  }

  if (feature.name === "dias") {
    return (
      <Stack gap={4}>
        <Text size="sm" fw={500} id="models-days-label">
          {label}
        </Text>
        <SegmentedControl
          aria-labelledby="models-days-label"
          data={MONTH_LENGTHS}
          value={String(value ?? 30)}
          onChange={(next) => onChange(Number(next))}
          fullWidth
        />
        <Text size="xs" c="var(--ce-text-muted)">
          {what}
        </Text>
      </Stack>
    );
  }

  const trainedMin = feature.training_minimum ?? 0;
  const trainedMax = feature.training_maximum ?? Math.max(value ?? 0, 1);
  const top = Math.ceil(trainedMax * SLIDER_REACH);
  const step = stepFor(top);
  return (
    <Stack gap={6}>
      <NumberInput
        label={label}
        description={what}
        value={value ?? ""}
        min={feature.minimum ?? 0}
        step={step}
        thousandSeparator="."
        decimalSeparator=","
        allowNegative={false}
        onChange={(next) => {
          if (typeof next === "number" && Number.isFinite(next)) onChange(next);
        }}
      />
      <Slider
        aria-label={label}
        min={feature.minimum ?? 0}
        max={top}
        step={step}
        value={Math.min(value ?? 0, top)}
        onChange={onChange}
        label={(current) => formatNumber(current, 0)}
        marks={[{ value: trainedMin }, { value: trainedMax }]}
      />
      <Text size="xs" c="var(--ce-text-muted)" className={classes.numbers}>
        {t("models.inputs.trainedRange", {
          min: formatNumber(trainedMin, 0),
          max: formatNumber(trainedMax, 0),
        })}
      </Text>
    </Stack>
  );
}
