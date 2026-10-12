# 5. `api/deps.py` is the API's composition root

- **Status:** Accepted
- **Date:** 2026-10-12
- **Deciders:** Platform team

## Context

[ADR 0003](0003-modular-monolith-over-microservices.md) set the layer rule, and
`tests/unit/test_architecture.py` enforces it. Under that rule the `api` layer
may import `domain`, `ports`, `services`, `schemas`, `db` and `core`, and **not
`adapters`**.

A hexagonal design still needs one place per entry point where concrete adapters
are bound to the ports the services depend on. For pipelines that place exists:
`pipelines/flows` may import adapters, and
`pipelines/flows/equs_bronze_load.py` builds the Bronze loader from its reader,
storage and repository.

The API has no such place. `src/energy/api/deps.py` describes itself as "the
single place where concrete adapters are bound to the abstract ports the routers
depend on", but the test forbids it from importing one. Until now nothing
exposed the contradiction, because every endpoint that needs an adapter is a
placeholder.

The frontend ends that. Two things must run inside a request:

- **checking a sign-in token** (increment 2 of
  [docs/frontend-plan.md](../frontend-plan.md)), which fetches Entra ID's
  signing keys over the network;
- **validating a small input** (increment 5): a form row or a pasted grid,
  checked against existing data and written to staging.

## Decision

**`energy.api.deps`, and no other module of the `api` layer, may import
`energy.adapters`.** It is the API's composition root, as `pipelines/flows` is
for pipelines.

- Routers, schemas, middleware and `api/security.py` keep the current rule. They
  receive ports and services through the `Annotated` aliases `deps.py` exports,
  and never name an adapter.
- `deps.py` only builds and hands over. It holds no business rule and makes no
  decision a service should make.
- The layer test gains an exception for that one module by name. It keeps
  failing for an adapter import anywhere else in `api`.
- **The test changes with the first binding, not before.** That is increment 2,
  with the adapter that verifies tokens. An exception nothing uses would be a
  loosened rule with no reason attached.

## Consequences

**Easier:**

- A router is tested by overriding a provider, never by patching an adapter.
- A form row is validated inside its request, in well under a second, by the
  same service code a pipeline runs.
- The wiring of the API is in one file that can be read top to bottom.

**Harder:**

- The layer rule is no longer one line per layer: it has one exception, by
  module. The test must say why it is there and point here.
- `deps.py` can grow. If it does, it becomes a package of provider modules, and
  the exception moves to that package and nowhere else.

**Accepted:**

- This is a deliberate, narrow change to a test that
  [ADR 0003](0003-modular-monolith-over-microservices.md) calls load-bearing. It
  is made by this record, not to make a failing change pass.

## Alternatives considered

**Run every validation as a Prefect job.** The layer rule would stay untouched,
since flows may already import adapters. Rejected: each form submission would
wait seconds for a worker, and checking a token on every request cannot be a
job at all.

**Let the whole `api` layer import adapters.** Rejected: routers would start
naming concrete classes, and tests would need a database to exercise a handler.
That is the outcome the layer rule exists to prevent.

**A separate wiring package** that both `api` and `pipelines` import. Rejected
for now: it adds a layer to express what one module already does, and the two
entry points bind different things. Worth revisiting if their wiring starts to
repeat.

## References

- [ADR 0003](0003-modular-monolith-over-microservices.md) — the layer rule.
- `tests/unit/test_architecture.py` — the enforcement, and where the exception
  will be written.
- `src/energy/api/deps.py` — the composition root.
- `src/energy/pipelines/flows/equs_bronze_load.py` — the same role for
  pipelines.
