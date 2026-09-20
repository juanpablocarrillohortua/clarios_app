# 3. A modular monolith over microservices

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** Platform team

## Context

The platform has several clearly separable responsibilities: ingesting from
external systems, validating and consolidating data, computing KPIs, serving an
API, rendering dashboards, and running statistical models. On an architecture
diagram these look like six services.

Three things argue against actually making them six services:

**The team is small.** Microservices trade local complexity for distributed
complexity. That trade pays off when independent teams need to deploy
independently. With one team, it mostly buys network calls where function calls
would do, plus a deployment topology that needs its own expertise.

**The data is shared and highly coupled.** Every component reads the same
medallion tables. A KPI service that cannot read Silver is not a service, it is
a client of a database that other services also write — which is the distributed
monolith anti-pattern, with all the coupling and none of the transactional
guarantees.

**The transactional boundaries cross the apparent service boundaries.** Promoting
a batch into Silver writes accepted rows, quarantine rows, and consolidation
audit rows atomically. Split across services, that becomes a distributed
transaction or an eventual-consistency scheme, and the invariant "rows in = rows
landed + rows quarantined" stops being checkable.

At the same time, the reason microservices are attractive is real: without
enforced boundaries, a codebase becomes a ball of mud in which nothing can be
changed safely or extracted later.

## Decision

We will build a **modular monolith**: one deployable codebase with internal
boundaries that are enforced automatically rather than merely documented.

The boundaries are:

1. **A hexagonal structure.** `domain` depends on nothing. `ports` declares
   interfaces. `adapters` implements them. Everything external is reached through
   a port.

2. **Three sibling entry points.** `api`, `dashboards` and `pipelines` may each
   depend on `domain`, `ports` and `services`, and **none may import another**.
   This is the specific rule that keeps extraction possible: each is already
   independent of its siblings.

3. **Automated enforcement.** `tests/unit/test_architecture.py` parses every
   module's imports with `ast` and fails the build on a violation. Static
   analysis rather than runtime observation, so a violation hidden inside a
   `TYPE_CHECKING` block is caught too.

The images are already split by entry point — the Dockerfile has `api` and `dash`
targets — so the deployment units exist even though the codebase does not.

## Consequences

**Easier:**

- One repository, one test suite, one `make check`. A change spanning the domain
  model and an endpoint is one commit and one review.
- Transactional integrity across the medallion layers is a local concern.
- Refactoring across boundaries is a type-checked rename rather than a
  coordinated multi-service release with a compatibility window.
- Local development is `docker compose up`, not a service mesh.
- Adding a data source touches `adapters/` and one YAML file. Nothing is
  deployed, versioned, or coordinated.

**Harder:**

- The boundaries are only as good as their enforcement. If the architecture test
  is ever weakened to make a change pass, the design degrades silently. That test
  is load-bearing and should be treated as such in review.
- Everything scales together. A CPU-heavy analytics run competes with API
  traffic in the same process pool. Mitigated for now by running pipelines in
  Prefect workers, which are separate containers from the same image.
- A single deployment means a single blast radius. A bad release affects
  ingestion, API and dashboards at once.

**Accepted:**

- We may need to extract a component later — most plausibly `analytics`, whose
  resource profile differs most from the rest. The layer rule is what makes that
  a packaging exercise rather than an archaeology project.
- "Modular monolith" is only meaningful while the modularity is real. The day the
  architecture test is deleted, this ADR becomes false.

## Alternatives considered

**Microservices from the start.** Rejected on team size and data coupling. The
canonical failure mode — services sharing a database, coupled by schema but
uncoupled by transactions — is exactly what this system's data model would
produce.

**An unstructured monolith.** The default outcome, and the honest alternative:
just write the code and let the structure emerge. Rejected because the structure
that emerges is usually one where the domain logic has grown into the request
handlers and cannot be tested without a database. The layer rule costs little to
maintain and prevents specifically that.

**A monolith with services extracted later "when needed".** This is in fact the
plan; the disagreement is only about whether to prepare for it. Preparing costs
one test file and a discipline about imports, and is far cheaper than
retrofitting boundaries into code that never had them.

## References

- [ADR 0002](0002-postgres-timescale-over-nosql.md) — the shared-database
  decision this one depends on.
- `docs/architecture.md` — the layer dependency table.
- `tests/unit/test_architecture.py` — the enforcement.
