# Contributing

Thanks for working on Clarios Energy. This document covers the conventions that
are not obvious from the code.

Everything in this repository — code, comments, docstrings, identifiers, commit
messages, documentation, and CI output — is written in **English**. This is not a
style preference; it is what lets the platform be maintained by people across
sites.

---

## Getting set up

See the [Getting Started](README.md#-getting-started) section of the README. In
short:

```bash
git clone <repository-url> && cd clarios-energy
cp .env.example .env
make install
make up
make db-upgrade
make check
```

---

## The quality gate

```bash
make check
```

This runs format-check → lint → strict type-checking → security scanning → the
frontend gate → the full test suite, and it is **exactly what CI enforces**. A
green `make check` locally means a green CI run. The frontend gate needs Node
and one `make frontend-install`.

Individual targets are documented in the README; `make help` lists everything.

`make install` installs pre-commit hooks, so most of this runs automatically on
commit. Do not bypass them with `--no-verify`: the hook that would have caught
the problem is the one you are skipping.

---

## Branching

| Branch | Purpose |
|---|---|
| `main` | Always deployable. Protected. |
| `develop` | Integration branch for a phase. |
| `feat/<short-description>` | A new capability. |
| `fix/<short-description>` | A bug fix. |
| `chore/<short-description>` | Tooling, dependencies, CI. |
| `docs/<short-description>` | Documentation only. |

Branch from `develop` for feature work; branch from `main` for a hotfix.

---

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/). The
`commitizen` pre-commit hook checks the format on every commit.

```
<type>(<optional scope>): <description>

[optional body]

[optional footer]
```

| Type | Use for |
|---|---|
| `feat` | A new capability |
| `fix` | A bug fix |
| `refactor` | A change that alters neither behaviour nor interface |
| `perf` | A performance improvement |
| `test` | Adding or correcting tests |
| `docs` | Documentation only |
| `chore` | Tooling, dependencies, build |
| `ci` | CI configuration |

Scopes match the layers: `domain`, `ports`, `adapters`, `pipelines`, `services`,
`api`, `dashboards`, `analytics`, `db`, `core`.

```
feat(adapters): add SCADA historian source for BOG-02

Implements EnergySource against the OPC-UA gateway. Readings are
time-weighted because the gateway reports instantaneous demand.

Closes #142
```

Append `!` for a breaking change and explain it in the footer:

```
feat(api)!: return coverage on every KPI response

BREAKING CHANGE: KPISeriesResponse.points gained a required `coverage`
field. Clients deserialising into a strict model must be updated.
```

**A commit that changes a KPI definition must say whether history is
recomputed.** Both answers are defensible; doing either silently is not.

---

## Adding a new data source

This is the most common extension, and the architecture is designed so that it
touches two places and nothing else. If your change needs more, the
`EnergySource` port is probably missing something — raise that rather than
working around it.

### 1. Implement the port

Create `src/energy/adapters/<name>_source.py` implementing
[`EnergySource`](src/energy/ports/energy_source.py):

```python
class MySource:
    @property
    def descriptor(self) -> SourceDescriptor: ...
    def health_check(self) -> bool: ...
    def fetch(self, window: TimeWindow | None = None) -> SourceBatch: ...
    def fetch_chunks(self, window=None, chunk_size=100_000) -> Iterator[SourceBatch]: ...
```

Ports are `Protocol` classes, so **do not inherit from `EnergySource`** —
structural typing means implementing the methods is enough.

Rules for any adapter:

- Return data **exactly as read**. No renaming beyond the configured column map,
  no type coercion, no unit conversion, no filtering. Bronze stores what arrived;
  transforming here destroys the evidence.
- Populate `content_hash` with the SHA-256 of the raw payload. That is what makes
  re-ingestion idempotent.
- Set `source_row` so a rejected value can be pointed at a specific row of the
  original file.
- Never raise on empty data. An empty batch is a valid result; only an
  unreachable source raises.

### 2. Register it

Add an entry to `config/sources.yaml`:

```yaml
sources:
  my_new_source:
    plant: MTY-01
    kind: my_kind
    priority: 25           # lower wins when sources conflict
    timezone: America/Monterrey
    native_frequency: PT15M
    supports_incremental: true
    resample:
      rule: sum            # sum | time_weighted | last | mean | max | first
      commodity: electricity
```

Get `priority` and `resample.rule` right — they are the two settings most likely
to produce plausible-looking wrong numbers:

- **`priority`** decides which value wins when sources overlap. It must be unique
  per plant; a tie makes the winner depend on dictionary ordering.
- **`resample.rule`** must match what the source measures. `sum` for interval
  consumption counters, `time_weighted` for instantaneous readings, `last` for
  cumulative registers. Using `sum` on an instantaneous series inflates the total
  by the number of samples per hour.

### 3. Add the adapter kind

Register the class in `ADAPTER_KINDS` in
[`adapters/registry.py`](src/energy/adapters/registry.py).

### 4. Test it

- A unit test for parsing, using a fixture file in `tests/fixtures/`.
- An integration test for `fetch()` against a stub endpoint.
- Verify with `make db-upgrade && docker compose run --rm api python -m energy check-sources`.

---

## Other common changes

### Adding a KPI

1. Declare it in `config/kpis.yaml`. Set `kind` correctly: `extensive` for a
   total (rolls up by summation), `intensive` for a ratio (rolls up by a weighted
   mean over `roll_up_weight`). Getting this wrong makes monthly figures subtly
   incorrect while daily ones look fine.
2. If the arithmetic is new, add a pure function to `domain/kpi_formulas.py` with
   a property test.
3. Add a `skip_when` condition for any denominator that can be zero.

### Adding a migration

```bash
make db-revision msg="add compressor submeter hierarchy"
```

- Implement `downgrade()` and **actually run it**. An untested downgrade is
  discovered during an incident.
- A new time-series table must be registered as a hypertable, and its primary key
  must include the partitioning column.
- Update `docs/data_dictionary.md`.

Note that `--autogenerate` produces nothing useful until Phase 2 defines the
SQLModel tables; `migrations/env.py` deliberately refuses to generate drops while
the metadata is empty, which would otherwise propose destroying the schema.

### Adding an endpoint

Routers stay thin: parse, delegate to a service, serialise. No business rules, no
SQL, no direct adapter construction. Request and response models live in
`api/schemas/` and are deliberately separate from both the domain entities and
the ORM tables — a database column rename must never become a breaking API change.

**Every route says who may call it.** Mount the router in `api/main.py` with the
permission it needs:

```python
app.include_router(things.router, dependencies=[Depends(requires(Permission.LOADS_WRITE))])
```

- A route is protected unless its path is in `PUBLIC_PATHS`. A router mounted
  without a permission fails `tests/unit/test_api_auth.py`.
- Ask for a **permission**, never for a role. If none fits, add one to
  `Permission` and a row to `GRANTS` in `api/security.py`, with its test. That
  table is the only place where a role is given a meaning.
- A handler that needs the caller takes `principal: PrincipalDep`.
- Never log a token, and never put the reason for a refusal in the response:
  it goes to the log.
- Add the endpoint's row to the table of [docs/api.md](docs/api.md#2-endpoints).
  A missing row fails `tests/unit/test_api_reference.py`.

[docs/api.md](docs/api.md) follows a request through the API and lists every
endpoint; [docs/sign-in.md](docs/sign-in.md) has the Entra ID side.

### Adding a dataset spec

A kind of data people load through the web app is a file in
`config/datasets/`, named after its key. [docs/dataset-specs.md](docs/dataset-specs.md)
has the format and the steps.

- **Copy the headers from the workbook by script,** never by typing them:
  Bronze keys every column by its exact header text.
- **Quote every allowed value.** YAML reads an unquoted `No` as false.
- **Check it with `energy datasets check`** before `make test`. A malformed spec
  stops the API at start.
- `tests/unit/test_dataset_specs_committed.py` ties each spec to the Bronze
  loader's routes, the Silver SQL, the translation file and, when present, the
  real workbook.

### Adding a validation rule

The checks of an uploaded file are pure functions in
`src/energy/domain/draft_rules.py` (and `workbook_limits.py` for the archive);
[docs/dataset-specs.md](docs/dataset-specs.md#what-a-file-is-checked-against)
lists them.

- **Give it an ID in `RuleId`,** `<group>.<name>`, and a severity. A rule a spec
  configures takes the column's `severity`.
- **Judge the text Bronze would store, as the Silver build reads it.** The
  rows come from `landing.sheet_payloads`; a value is trimmed of spaces, as
  `pg_temp.eq_txt` does. A rule about numbers or dates must agree with the
  Silver build's parsing, and a test keeps `SILVER_NUMBER` equal to it.
- **Write its sentence in `frontend/src/i18n/es.json`,** under
  `loads.rules.<group>.<name>`, with a `name` and a `message`. The API sends
  the ID and the values the message needs, never prose; a test fails for a
  rule without its sentence.
- **Test it in `tests/unit/test_draft_rules.py`** with the rows that break it,
  and add a row to the broken files of `tests/fixtures/workbooks.py` when it
  belongs in the end-to-end check.
- **Keep it bounded.** Report through `IssueCollector`, which lists the first
  1,000 issues of each rule in each column and counts the rest, so a file
  broken on every row costs counters, not memory.

### Changing the web app

The app in `frontend/` (React, TypeScript, Vite) is what users open at `/`.
[docs/frontend-plan.md](docs/frontend-plan.md) has its design and increments.

```bash
make frontend-install   # once, and after the lockfile changes
make frontend-dev       # http://localhost:5173 with live reload; needs `make up`
make frontend-format    # format, and apply safe lint fixes
make frontend-check     # the gate: format, lint, type-check, test, build
```

- **No business rule in TypeScript.** Validation, messages about data and every
  calculation come from the API. A rule that exists only in the browser is a
  defect. That includes access: the app shows a section when `GET /api/me`
  lists its permission, and never works out from a role what a user may do.
- **Sign-in is behind `AuthClient`** (`frontend/src/auth/authClient.ts`). Only
  `msalAuthClient.ts` imports Microsoft's library; tests hand the app a
  stand-in. A new section is a row in `frontend/src/sections.ts`, with its
  permission, and a route wrapped in `RequirePermission`.
- **The Power BI report is behind `EmbedReport`**
  (`frontend/src/powerbi/reportEmbed.ts`), in the same way. Only
  `powerBiReportEmbed.ts` imports Microsoft's embedding library, and the app
  loads that file when the dashboard opens. The app sends Power BI nothing but
  `GET` requests, and what each of its answers means is written in one place,
  `reportProblem.ts` ([docs/powerbi.md](docs/powerbi.md#the-report-inside-the-web-app)).
- **Every text the user sees is a key in `frontend/src/i18n/es.json`.** The
  interface is Spanish; keys, code and comments are English. A key that does not
  exist fails the type-check.
- **Addresses.** A new page gets a path in `frontend/src/paths.ts`, without a
  file extension and not under `/api`, `/auth`, `/dash`, `/minio` or `/prefect`:
  the `Caddyfile` routes those elsewhere.
- **Packages.** Add or upgrade with a cooling-off week, so a release pulled
  for being malicious never reaches the lockfile:
  `npm install --before=<a date a week ago> <package>` inside `frontend/`.
  Versions are exact, install scripts are off (`frontend/.npmrc`), and the
  lockfile is committed.
- **Folder names.** The root `.gitignore` ignores `lib/`, `build/`, `dist/`,
  `env/` and `var/` anywhere, so a source folder with one of those names would
  silently stay out of git.
- **Node.** Use the version in `frontend/.nvmrc` (24 LTS); CI and the image do.

---

## Architecture rules

The layer dependency rule is enforced by
[`tests/unit/test_architecture.py`](tests/unit/test_architecture.py), which
parses imports statically.

| Layer | May import |
|---|---|
| `core`, `domain` | nothing inside `energy` |
| `ports` | `domain` |
| `adapters` | `domain`, `ports`, `core`, `db` |
| `services` | `domain`, `ports`, `core` |
| `api` / `dashboards` / `pipelines` | as tabled in [docs/architecture.md](docs/architecture.md) — **never each other** |

One exception, by module:
[ADR 0005](docs/adr/0005-api-composition-root.md) lets `api/deps.py`, and no
other `api` module, import adapters. It is where the API binds adapters to
ports. The test names that module and fails for an adapter import anywhere else
in `api`. Another exception needs a decision record of its own.

**If a change requires weakening that test, stop and discuss it in the pull
request.** The test is load-bearing: the day it is relaxed to make something
pass, the modularity it protects starts degrading silently.

---

## Data correctness conventions

These are the ones that cause quiet, expensive bugs:

| Convention | Why |
|---|---|
| Timestamps are timezone-aware UTC, always | A naive datetime that gets assumed to be UTC shifts a plant's readings by hours |
| Convert timezone *before* resampling | Resampling in local time misplaces every interval across a DST transition |
| Missing is never zero | A zero deflates every average computed over the period |
| Every Gold row carries `coverage` | A number without it reads as certain regardless of how little data it rests on |
| Rejected rows are quarantined, never dropped | Rows in must equal rows landed plus rows quarantined |
| Corrections supersede, never overwrite | History must stay reconstructible |
| `Decimal` for money-grade totals | Float drift accumulates over millions of rows |
| Positive `deviation_pct` means worse | Reversing it turns every overconsumption into a reported saving |

---

## Testing

| Marker | Scope | Run with |
|---|---|---|
| `unit` | No containers, no network, no database | `make test-unit` |
| `integration` | Needs PostgreSQL (service container or testcontainer) | `make test-integration` |
| `e2e` | Needs the full compose stack running | `pytest -m e2e` |

Integration tests use `DATABASE_URL` when it is reachable and start a
testcontainer otherwise; they skip rather than fail when neither is available, so
`make check` stays green on a machine without Docker.

Coverage must stay at or above **70%**. Note that `raise NotImplementedError` is
excluded from coverage, so implementing a placeholder *adds* measured lines —
write the tests alongside it.

Domain logic deserves property tests. Hypothesis is available and is the right
tool for invariants such as "load factor and peak ratio are reciprocals".

---

## Implementing a placeholder

Most modules are Phase 1 placeholders whose docstring is the specification. When
you implement one:

1. **Read the docstring first.** It states what the module owns, what it must
   never do, and where it sits in the Bronze/Silver/Gold flow. Those constraints
   are the design.
2. Keep the docstring and update it to describe what the code now does.
3. Remove the `Implemented in: Phase N` line.
4. Un-skip the corresponding placeholder tests and make them pass.
5. If you disagree with the docstring, change it deliberately and say so in the
   pull request — do not silently implement something different.

---

## Pull requests

The [template](.github/pull_request_template.md) is a checklist, not a
formality; the data-correctness section in particular catches real problems.

- Keep pull requests focused. A migration plus an endpoint plus a refactor is
  three reviews wearing one hat.
- Describe *why*, not just what. The code shows what changed.
- Add an ADR for a significant architectural decision (see
  [ADR 0001](docs/adr/0001-record-architecture-decisions.md) for what qualifies).

## Reporting a security issue

Do not open a public issue. Contact the maintainers directly so a fix can be
prepared before the problem is disclosed.

## License

Contributions are licensed under the [MIT License](LICENSE).
