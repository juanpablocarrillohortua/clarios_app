## Summary

<!-- What does this change do, and why? Link the issue it closes. -->

Closes #

## Type of change

- [ ] Bug fix (`fix:`)
- [ ] New feature (`feat:`)
- [ ] Breaking change (`feat!:` / `fix!:`)
- [ ] Refactor (`refactor:`)
- [ ] Documentation (`docs:`)
- [ ] Infrastructure / tooling (`chore:` / `ci:`)

## Quality gate

- [ ] `make check` passes locally

`make check` runs format-check, lint, strict type-checking, security scanning and
the full test suite. It is exactly what CI enforces, so a green run here means a
green run there.

## Architecture

- [ ] The layer dependency rule is respected
      (`tests/unit/test_architecture.py` enforces it, but confirm the intent is
      right, not just that the test passes)
- [ ] No business logic was added to `api`, `dashboards`, or `adapters`
- [ ] New external systems are reached through a port, not imported directly
- [ ] `domain` still imports nothing from the rest of the application

## Data and migrations

- [ ] No migration needed

If a migration is included:

- [ ] `downgrade()` is implemented and was actually run
- [ ] The migration applies cleanly to a database at the current head
- [ ] Any new time-series table is registered as a hypertable
- [ ] `docs/data_dictionary.md` is updated

## Data correctness

<!-- Delete this section if the change cannot affect a reported number. -->

- [ ] Timestamps remain timezone-aware UTC and grid-aligned
- [ ] Missing data stays distinguishable from zero
- [ ] `coverage` is populated for any new Gold row
- [ ] Rejected rows are quarantined, never dropped
- [ ] Any change to a KPI definition states whether history is recomputed

## Testing

<!-- What did you add, and what does it prove? -->

- [ ] Unit tests for new domain logic
- [ ] Integration tests for new database or API behaviour
- [ ] Existing tests updated rather than deleted

## Documentation

- [ ] README updated if the setup or workflow changed
- [ ] An ADR added for a significant architectural decision
- [ ] `.env.example` updated if a new environment variable was introduced
- [ ] Docstrings updated on any placeholder this change implements

## Notes for reviewers

<!-- Anything worth looking at closely, or a decision you are unsure about. -->
