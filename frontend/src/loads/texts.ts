// The words of what the server names by key: a dataset's name, a rule, an
// issue. The server sends keys and values (a rule's ID, a pattern, a count);
// the sentences are in es.json, under the key the server's name points to. A
// test of the server checks that every rule it can send has its sentence.
//
// The keys arrive at run time, so they cannot be checked by the compiler as
// the app's own keys are: this module is the one place that looks them up
// untyped, and a key es.json lacks reads as a plain fallback, never a blank.

import type { TFunction } from "i18next";
import i18n from "i18next";

import type { Issue, Reason } from "../api/drafts";
import { formatBytes, formatCount } from "./formatBytes";

type Lookup = (key: string, options?: Record<string, unknown>) => string;

function lookup(t: TFunction): Lookup {
  return t as unknown as Lookup;
}

/** A text of es.json whose key the server named, or `fallback`. */
export function named(t: TFunction, key: string, fallback: string): string {
  return i18n.exists(key) ? lookup(t)(key) : fallback;
}

/**
 * What Silver does with a row a catalogue's new version leaves out: the
 * dataset's own sentence when es.json has one, otherwise the general one of
 * what the server says the build does (``kept`` or ``dropped``).
 */
export function removedRowsText(
  t: TFunction,
  datasetKey: string,
  removedRows: "kept" | "dropped",
): string {
  const own = `datasets.${datasetKey}.removedRows`;
  return i18n.exists(own)
    ? lookup(t)(own)
    : lookup(t)(`loads.difference.removedRows.${removedRows}`);
}

/** The short name of a rule, for a filter or a table cell. */
export function ruleName(t: TFunction, rule: string): string {
  const key = `loads.rules.${rule}.name`;
  return i18n.exists(key) ? lookup(t)(key) : lookup(t)("loads.issues.unknownRule", { rule });
}

/** The values an issue's sentence needs, written for people. */
function values(t: TFunction, issue: Issue): Record<string, string | number> {
  const read = lookup(t);
  const written: Record<string, string | number> = {};
  for (const [name, value] of Object.entries(issue.params)) {
    if (Array.isArray(value)) {
      written[name] = value.join(", ");
    } else if (name.endsWith("_bytes") && typeof value === "number") {
      written[name] = formatBytes(value);
    } else if (typeof value === "number") {
      written[name] = formatCount(value);
    } else {
      written[name] = value;
    }
  }
  const params = issue.params;
  if (issue.rule === "cell.type" && typeof params.type === "string") {
    written.type = read(`loads.types.${params.type}`);
  }
  if (issue.rule === "cell.range") {
    const which = "min" in params && "max" in params ? "both" : "min" in params ? "min" : "max";
    written.range = read(`loads.range.${which}`, { min: params.min, max: params.max });
  }
  return written;
}

/** The sentence that says what is wrong. */
export function issueMessage(t: TFunction, issue: Issue): string {
  const key = `loads.rules.${issue.rule}.message`;
  if (!i18n.exists(key)) {
    return lookup(t)("loads.issues.unknownRule", { rule: issue.rule });
  }
  return lookup(t)(key, values(t, issue));
}

/** Where an issue is, as Excel names a cell ("C5"), or its column or row alone. */
export function cellName(issue: Issue): string | null {
  if (issue.column && issue.row !== null) return `${issue.column}${issue.row}`;
  return issue.column ?? (issue.row !== null ? String(issue.row) : null);
}

/** Where an issue is, for the eye: the cell, the row alone, or the whole file. */
export function cellLabel(t: TFunction, issue: Issue): string {
  if (issue.column === null && issue.row !== null) {
    return t("loads.issues.rowOnly", { row: issue.row });
  }
  return cellName(issue) ?? t("loads.issues.wholeFile");
}

/** An issue's most likely reason; a rule's own when the server gave none. */
export function firstReason(issue: Issue): Reason {
  return issue.reasons[0] ?? { reason: `default.${issue.rule}`, params: {} };
}

/** The other likely reasons, when there are any worth saying: never the rule's own. */
export function otherReasons(issue: Issue): Reason[] {
  return issue.reasons.slice(1).filter((reason) => !reason.reason.startsWith("default."));
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** The values a reason's sentences need: the issue's own, then the reason's. */
function reasonValues(t: TFunction, issue: Issue, reason: Reason): Record<string, string | number> {
  const read = lookup(t);
  const written: Record<string, string | number> = {
    cell: cellName(issue) ?? "",
    row: issue.row !== null ? String(issue.row) : "",
    column: issue.column ?? "",
    header:
      (typeof issue.params.header === "string" ? issue.params.header : issue.column_key) ?? "",
    value: issue.value?.trim() ? issue.value : read("loads.issues.empty"),
    ...values(t, issue),
  };
  for (const [name, value] of Object.entries(reason.params)) {
    if (name === "characters" && Array.isArray(value)) {
      written[name] = value.map((each) => named(t, `loads.characters.${each}`, each)).join(", ");
    } else if (name === "difference" && typeof value === "string") {
      written[name] = named(t, `loads.differences.${value}`, value);
    } else if (name === "date" && typeof value === "string" && ISO_DATE.test(value)) {
      written[name] = new Intl.DateTimeFormat("es", { dateStyle: "short", timeZone: "UTC" }).format(
        new Date(`${value}T00:00:00Z`),
      );
    } else if (typeof value === "number") {
      written[name] = formatCount(value);
    } else {
      written[name] = Array.isArray(value) ? value.join(", ") : value;
    }
  }
  return written;
}

/**
 * Why an issue probably happened. A detector's reason has a sentence of its
 * own; a rule's own reason is the rule's message.
 */
export function reasonWhy(t: TFunction, issue: Issue, reason: Reason = firstReason(issue)): string {
  const key = `loads.reasons.${reason.reason}.why`;
  if (!i18n.exists(key)) return issueMessage(t, issue);
  const all = reasonValues(t, issue, reason);
  const why = lookup(t)(key, all);
  const repaired = `loads.reasons.${reason.reason}.repaired`;
  return typeof reason.params.repaired === "string" && i18n.exists(repaired)
    ? `${why} ${lookup(t)(repaired, all)}`
    : why;
}

/** What to do in Excel. A reason this app does not know falls back to its rule's own fix. */
export function reasonFix(
  t: TFunction,
  issue: Issue,
  reason: Reason = firstReason(issue),
): string | null {
  const all = reasonValues(t, issue, reason);
  for (const key of [
    `loads.reasons.${reason.reason}.fix`,
    `loads.reasons.default.${issue.rule}.fix`,
  ]) {
    if (i18n.exists(key)) return lookup(t)(key, all);
  }
  return null;
}

/** A second line, when there is more to say: the format expected, or why a file cannot be read. */
export function issueDetail(t: TFunction, issue: Issue): string | null {
  const params = issue.params;
  if (issue.rule === "cell.pattern" && typeof params.pattern === "string") {
    return lookup(t)("loads.issues.expected", { pattern: params.pattern });
  }
  if (issue.rule === "file.unreadable" && typeof params.reason === "string") {
    const key = `loads.unreadable.${params.reason}`;
    return i18n.exists(key) ? lookup(t)(key) : null;
  }
  return null;
}
