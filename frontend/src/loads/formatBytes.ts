// A size as people read it on Windows: binary units labelled KB, MB and GB,
// so a file Explorer shows as "300 MB" reads "300 MB" here too, and the
// server's limit of 300 MiB is written "300 MB".
const UNITS = ["B", "KB", "MB", "GB"] as const;
const STEP = 1024;

export function formatBytes(bytes: number): string {
  let value = bytes;
  let unit = 0;
  while (value >= STEP && unit < UNITS.length - 1) {
    value /= STEP;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  const number = new Intl.NumberFormat("es", { maximumFractionDigits: digits }).format(value);
  return `${number} ${UNITS[unit]}`;
}

/** A count as Spanish writes it: 1234, 12.345. */
export function formatCount(count: number): string {
  return new Intl.NumberFormat("es").format(count);
}
