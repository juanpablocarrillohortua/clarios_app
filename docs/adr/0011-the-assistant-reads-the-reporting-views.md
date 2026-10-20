# 11. The assistant reads the reporting views with read-only SQL

- **Status:** Proposed. Simulated mode is in use; database mode waits for the
  plant's agreement.
- **Date:** 2026-10-19
- **Deciders:** Platform team, with the owner
- **Amends:** [ADR 0007](0007-figures-reach-people-through-power-bi.md)
  (figures reach people through Power BI only)

## Context

The chat assistant ([docs/chatbot-plan.md](../chatbot-plan.md)) answers
questions about the platform. Phase 2 lets it answer from data: the model
writes its own SQL, the platform runs it, and the model reads the rows.

That conflicts with ADR 0007, which says figures reach people through the
`reporting` views and Power BI, and nowhere else, so that two places cannot
show two different numbers.

There is a second concern. The rows the assistant reads are sent to the model's
vendor (OpenAI). The plant has not yet agreed to that.

## Decision

1. **The assistant reads the same views as Power BI, and nothing else.** Its
   queries run with the read-only reporting login (`reporting_reader`), the one
   `energy reporting create-reader` makes. That login:
   - may only `SELECT` on the 12 views of schema `reporting`;
   - has read-only sessions with `search_path = reporting`.

   A figure the assistant gives therefore comes from the same views as the
   report's. ADR 0007 is amended accordingly: figures reach people through
   Power BI and through the assistant, both on `reporting`. The API still
   serves no figure of its own.
2. **Free SQL, guarded by the database.** The model writes any `SELECT`. The
   guards are, in order:
   - a check that it is one statement starting with `SELECT` or `WITH`
     (`domain/chat_sql.py`);
   - the login's rights;
   - a read-only transaction that is always rolled back;
   - a time limit per query;
   - a cap on the rows returned.
3. **Data that has no table yet is said, not guessed.**
   `config/chat/sources.yaml` lists the plant's files as implemented (with
   their views) or pending. The catalogue goes with every question. A
   question about a pending source, or a query on a table that does not
   exist, gets the sentence "No encontrado: la tabla está pendiente de
   implementación."
4. **Simulated data until the plant agrees.** `CHAT_DATA_SOURCE` chooses where
   the queries run:
   - `simulated` (the default): synthetic rows with the views' names and
     columns, in an in-memory DuckDB. Every answer with a figure says the
     figures are simulated.
   - `database`: the real views.

   Switching is one setting. A test against a real database keeps the
   simulated columns equal to the real ones.

## Consequences

- **The two numbers cannot disagree.** The assistant and Power BI read the
  same rows, so a figure in both agrees, as far as the model reads it
  correctly. A valid query can still be the wrong query. The guards make SQL
  harmless, not right.
- **Rows leave the premises in database mode.** That is why it stays off.
  Turning it on is the plant's decision. When the plant agrees, this ADR's
  status becomes Accepted.
- **DuckDB is a new dependency,** for the simulation only.
- **ADR 0007's endpoints are untouched.** The KPI routes are still to be
  removed in Phase 4, and nothing here serves figures through them.
