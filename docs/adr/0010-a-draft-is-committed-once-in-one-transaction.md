# 10. A draft is committed once, in one transaction

- **Status:** Accepted
- **Date:** 2026-10-18
- **Deciders:** Platform team, with the owner
- **Amends:** section 6 of [docs/frontend-plan.md](../frontend-plan.md) ("The
  write path", "Load metadata", "Idempotency")

## Context

Increment 7 of the web app lands a checked file in Bronze. When the person
confirms a draft, its staged rows (`staging.draft_rows`, built by the same
rules the Bronze loader uses) become rows of a landing table under a new run of
`bronze.ingestion_runs`, and the archived original is catalogued in
`bronze.raw_files`.

Section 6 of the plan, written before the code existed, said:

- the commit lands the rows through `BronzeLandingRepository.land()`;
- a draft moves through the states uploaded, validated, committing and
  committed;
- a failed commit leaves a `failed` run, "exactly as the loader behaves today";
- the run's parameters gain a key `original_content_hash`.

Reading the loader's adapter (`postgres_bronze_repo.py`) showed that the first
point cannot hold together with the others. `register_file`, `open_run` and
`land` each open and commit a transaction of their own: `open_run` commits the
`running` row at once, by contract, so that a failure can still be recorded.
The commit also has to update staging (the draft's state and its rows). Built
on those methods, a commit would be four transactions. A crash between them
would leave a run without its draft marked, or a draft marked without its run,
and a `committing` state would be needed to find and repair them.

The page has a button that people may press twice, and a network that may lose
an answer after the work is done.

## Decision

1. **The commit is one PostgreSQL transaction.** On one connection it locks the
   draft (`FOR UPDATE NOWAIT`); takes an advisory lock for the dataset (a key of
   its own, waited for 10 seconds); for a catalogue, checks that the version the
   person compared with is still the current one; catalogues the original
   unless `raw_files` has it; inserts the run; copies the staged rows into the
   landing table with one `INSERT … SELECT`; checks that the rows read, the rows
   staged, the rows landed and the run's `row_count` agree; completes the run;
   deletes the staged rows; and marks the draft committed. Then it commits.

2. **It runs the loader's SQL, not the loader's methods.** The statements that
   select and insert a file, insert a run and complete it, the landing-table
   map and the landing columns are shared constants of
   `postgres_bronze_repo.py`, made public without changing their text. A new
   adapter, `postgres_load_committer.py`, runs them. The command line keeps its
   methods, its transactions and its tests. An integration test lands one file
   both ways and finds the same rows and the same hashes.

3. **A failed commit leaves nothing.** Any failure, a lost connection or a
   crash included, rolls everything back: no row, no run (not even a `failed`
   one), and the draft stays validated with its rows. The person confirms
   again. This departs from the loader, whose failed runs stay in the ledger.

4. **A draft is committed at most once, and the draft is the key.** There is no
   `committing` state: no other transaction can see a commit half done. A
   second request while the first runs meets the row lock and answers 409
   `draft_busy`; any request after it answers 200 with the first load. No
   idempotency header is needed.

5. **The commit confirms what the person saw.** The request carries the
   validation digest the confirmation showed, the acknowledgment of its
   warnings when it has any, and, for a catalogue, the run it was compared
   with. A draft whose spec changed, or that was validated before the commit's
   facts were kept, is validated again first. Any difference answers 409, and
   nothing is written.

6. **The run records the load.** Its `parameters` hold the command line's own
   keys, with the same meaning, plus `input_mode`, `dataset_key`, `draft_id`,
   `previous_draft_id`, `spec_digest`, `uploaded_by_name`, `validation` (with
   the warnings accepted) and, for a catalogue, `difference`.
   `original_content_hash` is not added: every load is an upload
   ([ADR 0009](0009-data-is-entered-and-corrected-in-excel.md)), so it would
   repeat the command line's `content_hash`.

7. **Staging keeps the draft, not its rows.** The staged rows are deleted by
   the commit, since Bronze holds them. The draft, its issues and its counts
   stay 30 days, for the load's detail to link to, then are purged as any
   draft is. The archived original is never purged: Bronze catalogues it.

## Consequences

**Easier:**

- Nothing to recover. A commit either happened, with every row and its run,
  or did not happen at all. No state, job or person has to clean up after a
  crash.
- A double click, a second tab or a retry after a lost answer is safe by
  construction, and the page can offer "Comprobar" on a lost answer: it sends
  the same commit again.
- One definition of how a row lands. The CLI and the app cannot drift apart
  without a test failing.

**Harder:**

- **A failed commit is not in the ledger.** It is in the API's log and in the
  answer the person saw. If the plant wants failed attempts listed in the
  history, they need a table of their own, outside the transaction.
- **An app run's `started_at` equals its `finished_at`.** Both are the
  transaction's time. Commits of a dataset are serialized, and a catalogue's
  base version is checked, so Silver's "latest by `finished_at`" still picks
  the right version.
- **`raw_files.uploaded_at` is the commit's time**, not the upload's: the shared
  statement writes `now()`. The draft keeps when the file was uploaded.
- **The transaction grows with the file.** A file of 5 MiB, the largest
  checked today, holds about 20,000 rows of the receipts export, by the
  measurement of section 6 of the plan. A file of a million rows (increment
  12) holds its transaction and the dataset's lock for as long as the copy
  takes; increment 12 measures it, and may make the commit a job.
- **The shared constants are a contract.** A change to one changes both paths
  at once.

## Alternatives considered

- **Call the loader's methods (the plan).** Rejected: four transactions, a
  `committing` state, and repair work after a crash.
- **Give the loader's methods an optional outer connection.** Rejected: it
  changes the command line's code and transactions, which this increment was
  to leave alone, for the same result as sharing the statements.
- **Keep a `failed` run for a failed commit**, written in a second
  transaction after the rollback. Rejected: a failure the database itself
  causes (a lost connection, a crash) could not be recorded that way either,
  so the ledger would still not be complete, and the run would point at no
  rows.
- **An `Idempotency-Key` header.** Unnecessary: a draft commits once, so the
  draft is the key.

## References

- [docs/frontend-plan.md](../frontend-plan.md), section 6 and row 7 of section 8
- [docs/api.md](../api.md): `POST /api/drafts/{draft_id}/commit`,
  `GET /api/drafts/{draft_id}/confirmation`, `GET /api/loads`
- [docs/data_dictionary.md](../data_dictionary.md): `bronze.ingestion_runs`
  and the keys the commit adds to `parameters`; `staging.drafts`
- `src/energy/adapters/postgres_load_committer.py`,
  `src/energy/services/load_service.py`,
  `tests/integration/test_commit_end_to_end.py`
