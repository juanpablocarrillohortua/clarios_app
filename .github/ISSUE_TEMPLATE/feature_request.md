---
name: Feature request
about: Propose a new capability or an improvement
title: "[Feature] "
labels: ["enhancement", "triage"]
assignees: []
---

## Problem

<!--
Describe the problem, not the solution. What can someone not do today, and what
does that cost them?
-->

## Proposed solution

<!-- What would you like the platform to do? -->

## Which layer does this belong to?

<!--
The architecture constrains where things can live. Ticking the wrong box is
fine - it is a useful discussion to have on the issue rather than in review.
-->

- [ ] `domain` - a business rule or formula with no I/O
- [ ] `ports` - a new interface to an external system
- [ ] `adapters` - a new implementation of an existing port (for example a new data source)
- [ ] `pipelines` - orchestration, scheduling, or a new flow
- [ ] `services` - a new application use case
- [ ] `api` - a new endpoint or a change to an existing contract
- [ ] `dashboards` - a new view or chart
- [ ] `analytics` - a new model or statistical method
- [ ] Infrastructure / tooling

## Is this a new data source?

If so, it should need no changes outside `adapters` and `config/sources.yaml`.
If it appears to need changes elsewhere, say where - that usually means the
`EnergySource` port is missing something, which is worth knowing.

- Source system:
- Native cadence:
- Timezone it reports in:
- Does it support incremental queries?
- Proposed priority relative to existing sources:

## Impact on existing data

- [ ] Requires a database migration
- [ ] Changes an existing KPI definition or formula
- [ ] Requires a backfill or recomputation of historical periods
- [ ] Changes an existing API contract (breaking)
- [ ] None of the above

<!--
If a KPI definition changes, say explicitly whether historical values should be
recomputed or left as reported. Both are defensible; silently doing either is not.
-->

## Alternatives considered

<!-- What else would solve the problem, and why is this better? -->

## Additional context
