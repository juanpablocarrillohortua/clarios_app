// Dates and moments as Spanish writes them on screen: 03/10/2025, and
// 18/10/26, 14:05 for a moment. A day the server sends (YYYY-MM-DD) is a
// calendar day, read in UTC so that no time zone moves it to the day before.
const DAY = new Intl.DateTimeFormat("es", { dateStyle: "short", timeZone: "UTC" });
const MOMENT = new Intl.DateTimeFormat("es", { dateStyle: "short", timeStyle: "short" });

/** A calendar day the server sent as YYYY-MM-DD. */
export function formatDay(day: string): string {
  return DAY.format(new Date(`${day}T00:00:00Z`));
}

/** A moment the server sent as an ISO timestamp, in the browser's time zone. */
export function formatWhen(moment: string): string {
  return MOMENT.format(new Date(moment));
}
