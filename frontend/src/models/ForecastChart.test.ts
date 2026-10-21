// Where the chart puts things: periods evenly across, the scenario one slot
// after the last, the band around the validation forecasts, and a scale that
// holds every value drawn.
import { describe, expect, it } from "vitest";

import type { HistoryPoint } from "../api/models";
import { chartGeometry, WIDTH } from "./ForecastChart";

const history: HistoryPoint[] = [
  { period: "2026-04-01", actual: 400, predicted: null, naive: null, inputs: {} },
  { period: "2026-05-01", actual: 450, predicted: 460, naive: 400, inputs: {} },
  { period: "2026-06-01", actual: 500, predicted: 490, naive: 450, inputs: {} },
];
const scenario = { period: "2026-07-01", prediction: 520, low: 470, high: 570 };

describe("the chart's geometry", () => {
  it("puts the scenario one slot after the last period", () => {
    const geometry = chartGeometry(history, 50, scenario);

    expect(geometry.scenarioX).toBeCloseTo(geometry.x(3));
    expect(geometry.x(1) - geometry.x(0)).toBeCloseTo(geometry.step);
    expect(geometry.x(3)).toBeLessThanOrEqual(WIDTH);
  });

  it("draws the band around the validated periods only", () => {
    const geometry = chartGeometry(history, 50, null);

    // Two validated periods: two points above, two below, closed.
    expect(geometry.band.match(/[ML]/g)).toHaveLength(4);
    expect(geometry.band.endsWith("Z")).toBe(true);
    expect(geometry.validation.match(/[ML]/g)).toHaveLength(2);
    expect(geometry.actual.match(/[ML]/g)).toHaveLength(3);
    expect(geometry.scenarioX).toBeNull();
  });

  it("holds every value drawn, the band and the scenario included", () => {
    const geometry = chartGeometry(history, 50, scenario);

    expect(geometry.scale.min).toBeLessThanOrEqual(400);
    expect(geometry.scale.max).toBeGreaterThanOrEqual(570);
    // Higher values sit higher on the page.
    expect(geometry.y(570)).toBeLessThan(geometry.y(400));
    expect(geometry.yTicks[0]).toBe(geometry.scale.min);
  });
});
