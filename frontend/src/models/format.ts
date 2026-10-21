// Numbers and periods as Spanish writes them, for the models' section. The
// API sends every number already computed: these only write them.
import type { Granularity } from "../api/models";

const MINUS = "−";

function digitsFor(value: number): number {
  const size = Math.abs(value);
  if (size >= 100) return 0;
  if (size >= 1) return 2;
  return 3;
}

/** A quantity: 1.000.409 kWh rounded to units, 4,79 to two decimals. */
export function formatNumber(value: number, digits = digitsFor(value)): string {
  return new Intl.NumberFormat("es", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
    // "always": Spanish otherwise leaves four-digit numbers ungrouped (1234).
    useGrouping: "always" as unknown as boolean,
  })
    .format(value)
    .replace("-", MINUS);
}

/** A number with its sign always written: +4,79, −8.335. */
export function formatSigned(value: number, digits = digitsFor(value)): string {
  const written = formatNumber(Math.abs(value), digits);
  return value < 0 ? `${MINUS}${written}` : `+${written}`;
}

/** A share in %: 4,89 %. */
export function formatPercent(value: number): string {
  return `${formatNumber(value, 2)} %`;
}

/** A p-value: 0,027, or < 0,001. */
export function formatP(value: number): string {
  return value < 0.001 ? `< ${formatNumber(0.001, 3)}` : formatNumber(value, 3);
}

function day(iso: string): Date {
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`);
}

/** A period in full: "julio de 2026", or "jueves, 1 de octubre de 2026". */
export function formatPeriod(iso: string, granularity: Granularity): string {
  const options: Intl.DateTimeFormatOptions =
    granularity === "month"
      ? { month: "long", year: "numeric", timeZone: "UTC" }
      : { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" };
  return new Intl.DateTimeFormat("es", options).format(day(iso));
}

/** A period on an axis: "jul 2026", or "1 oct". */
export function formatTick(iso: string, granularity: Granularity): string {
  const options: Intl.DateTimeFormatOptions =
    granularity === "month"
      ? { month: "short", year: "numeric", timeZone: "UTC" }
      : { day: "numeric", month: "short", timeZone: "UTC" };
  return new Intl.DateTimeFormat("es", options).format(day(iso)).replace(".", "");
}

/** A date without the time: "20 oct 2026". */
export function formatDate(iso: string): string {
  return new Intl.DateTimeFormat("es", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  })
    .format(day(iso))
    .replace(".", "");
}
