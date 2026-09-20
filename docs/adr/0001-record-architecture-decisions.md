# 1. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-09-18
- **Deciders:** Platform team

## Context

Architectural decisions on a platform like this one are rarely obvious in
hindsight. Six months after the fact, a choice such as "store everything in UTC"
or "keep Bronze immutable" looks either self-evident or arbitrary, and in both
cases the *reasoning* is gone. What remains is the code, which records what was
decided but never why.

The practical consequence is well known: someone encounters a constraint that
seems unnecessary, removes it, and rediscovers the original problem the hard
way. Or — just as costly — someone preserves a constraint that no longer applies
because nobody can say what it was for.

Phase 1 sets up a system that several people will extend over four more phases.
The decisions being made now are precisely the ones whose rationale will be
hardest to reconstruct later.

## Decision

We will record significant architectural decisions as **Architecture Decision
Records** (ADRs), stored in `docs/adr/` and numbered sequentially.

An ADR is warranted when a decision:

- constrains how future code must be written (the layer dependency rule);
- is expensive to reverse (the choice of database engine);
- is likely to be questioned by someone new (why a monolith rather than
  services);
- was contested, and the losing option is still reasonable.

Routine choices — a library version, a variable name, a file layout — do not get
an ADR. If the reasoning fits in a code comment, it belongs in a code comment.

Each ADR follows this structure:

| Section | Content |
|---|---|
| Status | Proposed / Accepted / Deprecated / Superseded by ADR-NNNN |
| Context | The forces at play. What made this a decision rather than a default? |
| Decision | What we are doing, in the active voice. |
| Consequences | What becomes easier, what becomes harder, what we accept. |
| Alternatives considered | What else was on the table, and why it lost. |

**ADRs are immutable once accepted.** A decision that changes is not edited — a
new ADR supersedes it, and the old one is marked `Superseded by ADR-NNNN`. The
record of having believed something different is part of the value.

## Consequences

**Easier:**

- A new contributor can read four short documents and understand the shape of the
  system, rather than inferring it from the code.
- Code review can reference a decision instead of relitigating it.
- Revisiting a decision starts from its original reasoning, so the discussion is
  about what changed rather than about what we were thinking.

**Harder:**

- Writing an ADR takes time, and it is tempting to skip during a busy phase. The
  pull request template asks about it explicitly for that reason.
- There is a judgement call about what deserves one. Erring toward writing too
  few is the usual failure; a handful of unnecessary ADRs costs very little.

**Accepted:**

- ADRs describe intent at a point in time. They are not a substitute for
  documentation of current behaviour, which lives in `docs/architecture.md` and
  `docs/data_dictionary.md`.

## Alternatives considered

**A wiki.** Rejected because it drifts from the code. An ADR lives in the same
repository, moves through the same review, and is versioned with the change it
describes.

**Commit messages and pull request descriptions.** These record decisions but
are effectively unsearchable a year later, and a decision is often spread across
several of them.

**Nothing.** The status quo for most projects. Rejected because this platform
will be extended by people who were not present for Phase 1, which is exactly
the situation ADRs are for.

## References

- Michael Nygard, "Documenting Architecture Decisions" (2011), the original
  formulation of this pattern.
