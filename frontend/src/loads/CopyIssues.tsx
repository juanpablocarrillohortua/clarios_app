// "Copiar para Excel": the issues of the current filter, copied as a table the
// user pastes into a new sheet beside the workbook being fixed. Tab-separated
// text through the clipboard: Excel splits it into columns, and nothing goes
// through a file or an encoding. The sentences are the screen's own.
//
// At most COPY_LIMIT issues, asked for a page at a time. A value Excel would
// read as a formula (=, +, -, @ in front) is kept as text.
import { Button, Group, Text } from "@mantine/core";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchIssues, type Issue, type IssueQuery } from "../api/drafts";
import { useSession } from "../auth/Session";
import { formatCount } from "./formatBytes";
import { cellLabel, firstReason, reasonFix, reasonWhy, ruleName } from "./texts";

export const COPY_LIMIT = 5_000;
const COPY_PAGE = 500;
const FORMULA_START = /^[=+\-@]/;
const NUMBER = /^-?\d+([.,]\d+)?$/;

/** A value as one cell of a tab-separated line: no tabs or breaks, never a formula. */
export function asCell(value: string): string {
  const flat = value.replace(/[\t\r\n]+/g, " ");
  return FORMULA_START.test(flat) && !NUMBER.test(flat) ? `'${flat}` : flat;
}

type Copying =
  | { phase: "idle" }
  | { phase: "copying" }
  | { phase: "done"; n: number }
  | { phase: "failed" };

export function CopyIssues({ draftId, query }: { draftId: string; query: IssueQuery }) {
  const { t } = useTranslation();
  const { api } = useSession();
  const [copying, setCopying] = useState<Copying>({ phase: "idle" });

  function line(issue: Issue): string {
    const reason = firstReason(issue);
    return [
      t(`loads.severity.${issue.severity}`),
      cellLabel(t, issue),
      issue.column_key ?? "",
      issue.value ?? "",
      ruleName(t, issue.rule),
      reasonWhy(t, issue, reason),
      reasonFix(t, issue, reason) ?? "",
      issue.change === "new" ? t("loads.issues.new") : "",
    ]
      .map(asCell)
      .join("\t");
  }

  async function copy() {
    setCopying({ phase: "copying" });
    try {
      const issues: Issue[] = [];
      for (let offset = 0; offset < COPY_LIMIT; offset += COPY_PAGE) {
        const page = await fetchIssues(api, draftId, { ...query, offset, limit: COPY_PAGE });
        issues.push(...page.items);
        if (offset + page.items.length >= page.total || page.items.length === 0) break;
      }
      const header = [
        t("loads.copy.columns.severity"),
        t("loads.copy.columns.cell"),
        t("loads.copy.columns.column"),
        t("loads.copy.columns.value"),
        t("loads.copy.columns.rule"),
        t("loads.copy.columns.cause"),
        t("loads.copy.columns.fix"),
        t("loads.copy.columns.change"),
      ];
      const text = [header.join("\t"), ...issues.map(line)].join("\r\n");
      await navigator.clipboard.writeText(text);
      setCopying({ phase: "done", n: issues.length });
    } catch {
      setCopying({ phase: "failed" });
    }
  }

  return (
    <Group gap="sm" wrap="wrap">
      {copying.phase === "done" && (
        <Text size="sm" c="dimmed" role="status">
          {t("loads.copy.done", { count: copying.n, n: formatCount(copying.n) })}
        </Text>
      )}
      {copying.phase === "failed" && (
        <Text size="sm" c="var(--ce-error)" role="alert">
          {t("loads.copy.failed")}
        </Text>
      )}
      <Button
        variant="default"
        size="compact-sm"
        disabled={copying.phase === "copying"}
        onClick={() => void copy()}
      >
        {copying.phase === "copying" ? t("loads.copy.copying") : t("loads.copy.button")}
      </Button>
    </Group>
  );
}
