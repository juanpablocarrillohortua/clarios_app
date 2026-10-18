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

import type { Issue } from "../api/drafts";
import { formatBytes, formatCount } from "./formatBytes";

type Lookup = (key: string, options?: Record<string, unknown>) => string;

function lookup(t: TFunction): Lookup {
  return t as unknown as Lookup;
}

/** A text of es.json whose key the server named, or `fallback`. */
export function named(t: TFunction, key: string, fallback: string): string {
  return i18n.exists(key) ? lookup(t)(key) : fallback;
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
