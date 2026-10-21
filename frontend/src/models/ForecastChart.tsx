// The chart of a model: what the area really used, period by period; what the
// model forecast one period ahead during its validation, with the 90 % alert
// band around it; and, one period after the last, the forecast of the
// scenario the person is building.
//
// Drawn by hand in SVG: one chart needs no library. The scenario's point and
// its band move on the app's spring when the forecast changes, from wherever
// they are on screen, so a value typed while they move simply redirects
// them. The history does not move: only what the person changes does.
// Pointing at the chart shows the values of the nearest period.
import { useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Granularity, HistoryPoint } from "../api/models";
import { formatNumber, formatPeriod, formatTick } from "./format";
import classes from "./models.module.css";

/** The drawing's own units; the SVG scales to its box. */
export const WIDTH = 760;
export const HEIGHT = 300;
const MARGIN = { top: 16, right: 28, bottom: 34, left: 72 } as const;
const Y_TICKS = 4;
const X_TICKS = 6;
const PADDING = 0.06;

export interface ScenarioPoint {
  period: string;
  prediction: number;
  low: number;
  high: number;
}

interface Scale {
  min: number;
  max: number;
  step: number;
}

/** A tick step people read easily: 1, 2, 2.5 or 5 times a power of ten. */
function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw));
  const factor = [1, 2, 2.5, 5, 10].find((candidate) => candidate * power >= raw) ?? 10;
  return factor * power;
}

function scaleOf(values: number[]): Scale {
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || Math.abs(high) || 1;
  const step = niceStep((span * (1 + 2 * PADDING)) / Y_TICKS);
  return {
    min: Math.floor((low - span * PADDING) / step) * step,
    max: Math.ceil((high + span * PADDING) / step) * step,
    step,
  };
}

function line(points: [number, number][]): string {
  return points
    .map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");
}

/** Where everything goes, in the drawing's units. Exported for its test. */
export function chartGeometry(
  history: HistoryPoint[],
  alertBand: number,
  scenario: ScenarioPoint | null,
) {
  const slots = history.length + (scenario ? 1 : 0);
  const innerWidth = WIDTH - MARGIN.left - MARGIN.right;
  const innerHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const step = innerWidth / Math.max(1, slots - 1);
  const values = history.flatMap((point) =>
    point.predicted === null
      ? [point.actual]
      : [point.actual, point.predicted - alertBand, point.predicted + alertBand],
  );
  if (scenario) values.push(scenario.low, scenario.high);
  const scale = scaleOf(values.length > 0 ? values : [0]);
  const x = (index: number) => MARGIN.left + index * step;
  const y = (value: number) =>
    MARGIN.top + ((scale.max - value) / (scale.max - scale.min)) * innerHeight;

  const validated = history
    .map((point, index) => ({ point, index }))
    .filter(({ point }) => point.predicted !== null);
  const upper = validated.map(
    ({ point, index }) => [x(index), y((point.predicted ?? 0) + alertBand)] as [number, number],
  );
  const lower = validated
    .map(
      ({ point, index }) => [x(index), y((point.predicted ?? 0) - alertBand)] as [number, number],
    )
    .reverse();

  const yTicks: number[] = [];
  for (let value = scale.min; value <= scale.max + scale.step / 2; value += scale.step) {
    yTicks.push(value);
  }
  const every = Math.max(1, Math.ceil(history.length / X_TICKS));
  const xTicks = history
    .map((point, index) => ({ period: point.period, index }))
    .filter(({ index }) => index % every === 0);

  return {
    x,
    y,
    step,
    scale,
    yTicks,
    xTicks,
    actual: line(history.map((point, index) => [x(index), y(point.actual)])),
    validation: line(validated.map(({ point, index }) => [x(index), y(point.predicted ?? 0)])),
    band: upper.length > 0 ? `${line([...upper, ...lower])} Z` : "",
    scenarioX: scenario ? x(history.length) : null,
    plot: { left: MARGIN.left, top: MARGIN.top, width: innerWidth, height: innerHeight },
  };
}

interface ForecastChartProps {
  history: HistoryPoint[];
  granularity: Granularity;
  unit: string;
  alertBand: number;
  scenario: ScenarioPoint | null;
}

export function ForecastChart({
  history,
  granularity,
  unit,
  alertBand,
  scenario,
}: ForecastChartProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const box = useRef<SVGSVGElement>(null);
  const [pointed, setPointed] = useState<number | null>(null);
  const geometry = useMemo(
    () => chartGeometry(history, alertBand, scenario),
    [history, alertBand, scenario],
  );
  const slots = history.length + (scenario ? 1 : 0);

  function point(event: React.PointerEvent<SVGSVGElement>) {
    const rect = box.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    const drawingX = ((event.clientX - rect.left) / rect.width) * WIDTH;
    const index = Math.round((drawingX - geometry.plot.left) / geometry.step);
    setPointed(Math.min(slots - 1, Math.max(0, index)));
  }

  const isScenario = scenario !== null && pointed === history.length;
  const shown = pointed === null || isScenario ? null : history[pointed];
  const scenarioY = scenario ? geometry.y(scenario.prediction) : 0;
  const whiskerTop = scenario ? geometry.y(scenario.high) : 0;
  const whiskerHeight = scenario ? Math.max(1, geometry.y(scenario.low) - whiskerTop) : 0;

  return (
    <div className={classes.chart}>
      <svg
        ref={box}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-labelledby={titleId}
        onPointerMove={point}
        onPointerDown={point}
        onPointerLeave={() => setPointed(null)}
      >
        <title id={titleId}>{t("models.chart.label", { unit })}</title>
        {geometry.yTicks.map((value) => (
          <g key={value}>
            <line
              className={classes.gridLine}
              x1={geometry.plot.left}
              x2={geometry.plot.left + geometry.plot.width}
              y1={geometry.y(value)}
              y2={geometry.y(value)}
            />
            <text
              className={classes.axisText}
              x={geometry.plot.left - 10}
              y={geometry.y(value)}
              textAnchor="end"
              dominantBaseline="middle"
            >
              {formatNumber(value, 0)}
            </text>
          </g>
        ))}
        {geometry.xTicks.map(({ period, index }) => (
          <text
            key={period}
            className={classes.axisText}
            x={geometry.x(index)}
            y={HEIGHT - 10}
            textAnchor="middle"
          >
            {formatTick(period, granularity)}
          </text>
        ))}
        {geometry.band && <path className={classes.band} d={geometry.band} />}
        <path className={classes.actualLine} d={geometry.actual} />
        {geometry.validation && <path className={classes.validationLine} d={geometry.validation} />}
        {scenario && geometry.scenarioX !== null && (
          <g>
            <line
              className={classes.scenarioDivider}
              x1={geometry.scenarioX - geometry.step / 2}
              x2={geometry.scenarioX - geometry.step / 2}
              y1={geometry.plot.top}
              y2={geometry.plot.top + geometry.plot.height}
            />
            {/* A rect one unit tall, stretched: transform moves on the spring, attributes would jump. */}
            <rect
              className={classes.scenarioBand}
              data-testid="scenario-band"
              x={-4}
              y={0}
              width={8}
              height={1}
              style={{
                transform: `translate(${geometry.scenarioX}px, ${whiskerTop}px) scaleY(${whiskerHeight})`,
              }}
            />
            <circle
              className={classes.scenarioPoint}
              data-testid="scenario-point"
              cx={0}
              cy={0}
              r={6}
              style={{ transform: `translate(${geometry.scenarioX}px, ${scenarioY}px)` }}
            />
          </g>
        )}
        {pointed !== null && (
          <line
            className={classes.crosshair}
            x1={geometry.x(pointed)}
            x2={geometry.x(pointed)}
            y1={geometry.plot.top}
            y2={geometry.plot.top + geometry.plot.height}
          />
        )}
      </svg>
      {pointed !== null && (shown || isScenario) && (
        <div
          className={classes.tooltip}
          style={{ left: `${(geometry.x(pointed) / WIDTH) * 100}%` }}
          data-side={pointed > slots / 2 ? "left" : "right"}
          role="status"
        >
          {shown && (
            <>
              <strong>{formatPeriod(shown.period, granularity)}</strong>
              <span>
                {t("models.chart.actual")}: {formatNumber(shown.actual, 0)} {unit}
              </span>
              {shown.predicted !== null && (
                <span>
                  {t("models.chart.validationShort")}: {formatNumber(shown.predicted, 0)} {unit}
                </span>
              )}
              {"eq" in shown.inputs && <span>EQ: {formatNumber(shown.inputs.eq ?? 0, 0)}</span>}
            </>
          )}
          {isScenario && scenario && (
            <>
              <strong>{formatPeriod(scenario.period, granularity)}</strong>
              <span>
                {t("models.chart.forecast")}: {formatNumber(scenario.prediction, 0)} {unit}
              </span>
              <span>
                {formatNumber(scenario.low, 0)} – {formatNumber(scenario.high, 0)} {unit}
              </span>
            </>
          )}
        </div>
      )}
      <ul className={classes.legend}>
        <li data-series="actual">{t("models.chart.actual")}</li>
        <li data-series="validation">{t("models.chart.validation")}</li>
        <li data-series="band">{t("models.chart.band")}</li>
        {scenario && <li data-series="scenario">{t("models.chart.forecast")}</li>}
      </ul>
    </div>
  );
}
