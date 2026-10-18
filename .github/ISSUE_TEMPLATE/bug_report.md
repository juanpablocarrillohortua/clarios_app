---
name: Bug report
about: Report incorrect behaviour, a wrong number, or a failure
title: "[Bug] "
labels: ["bug", "triage"]
assignees: []
---

## Summary

<!-- One or two sentences. What is wrong? -->

## Affected area

<!-- Tick everything that applies. -->

- [ ] Ingestion (Bronze) - a source, an upload, or a push endpoint
- [ ] Validation / quarantine (Bronze to Silver)
- [ ] Consolidation (multi-source merge onto the canonical grid)
- [ ] KPI computation (Gold)
- [ ] API
- [ ] Dashboards
- [ ] Analytics (baseline, anomaly, attribution)
- [ ] Infrastructure (Docker, migrations, CI)

## Environment

| Field | Value |
|---|---|
| Deployment | local / staging / production |
| Version or commit | |
| Plant code(s) | |
| Affected period (UTC) | |

## Steps to reproduce

1.
2.
3.

## Expected behaviour

<!-- What should have happened? -->

## Actual behaviour

<!-- What happened instead? -->

## If a reported number looks wrong

A wrong figure is almost always traceable, so please include what you can:

- **Run id** (from the history of loads in the app, `/api/loads`, or the `run_id` column on the Gold row):
- **Coverage** reported alongside the value:
- **Quarantined rows** for that run (`silver.quarantine`, by `run_id`):
- **Sources** that supplied the period, and whether the consolidation audit
  records a conflict:

## Logs

<!--
Paste the relevant log lines. Include the `request_id` if the problem came
through the API - it correlates every log line for that request.
-->

```text

```

## Additional context

<!-- Screenshots, a sample source file, anything else that helps. -->
