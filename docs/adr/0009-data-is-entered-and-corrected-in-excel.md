# 9. Data is entered and corrected in Excel, not in the app

- **Status:** Accepted
- **Date:** 2026-10-18
- **Deciders:** Platform team, with the owner
- **Amends:** [ADR 0004](0004-react-spa-entra-and-embedded-power-bi.md),
  decision 1 (the grid) and its consequence that "the form and the grid are
  drawn from the dataset specification"

## Context

The plan of the web app ([docs/frontend-plan.md](../frontend-plan.md)) had
three ways to enter data: uploading a workbook, a one-row form, and an
Excel-like grid. The grid had two uses: typing or pasting a catalogue (increment
9), and fixing the cells of an uploaded file in place, then validating again
without uploading anything (increment 6). Increment 6 was to choose the grid by
a short spike among MIT-licensed grids, since there is no licence budget
(question 8 of the plan).

Increment 5 built the upload and the checks. Every issue names its row, column
and rule, and the original file is archived under its hash. Before increment 6
started, the owner looked at what the grid would cost and what it would bring:

- **The data already lives in Excel.** The QAD exports come out of QAD as
  workbooks; the catalogues are sheets of the plant's own workbooks, kept in
  Excel today. People fix data where they already work.
- **A grid is the largest frontend risk of the plan**: pasting blocks from
  Excel with their types intact, dates and numbers in the Spanish locale, a new
  dependency, and the memory of a large file held in the browser.
- **Edits in the app would make a second source of truth.** An edited upload
  needed a generated workbook beside the original, staged edits kept beside the
  original values, and an audit of both (sections 6 and 7 of the plan).

## Decision

**Data is entered and corrected in Excel. The app only uploads, checks,
explains and compares.**

1. **No cell is edited in the app.** A file with problems is fixed in Excel and
   uploaded again as a *correction* of its draft
   (`POST /api/drafts/{draft_id}/corrections`). The correction is a draft of its
   own, linked to the one it corrects, and the page says what it cleared, what
   remains and what is new. The grid spike and cell editing are dropped from
   increment 6.
2. **No grid and no form, for any dataset.** Catalogues are uploaded like
   exports: the template filled in, or the users' own workbook. Every spec says
   `entry_modes: [upload]`, and the spec checker refuses any other mode.
   Conversion cost, the one dataset that had no file, gets a template and is
   uploaded (increment 12).
3. **Every issue says what to do in Excel.** Each issue carries its likely
   reasons, each with a fix written as Excel actions, in Spanish-language Excel
   for Windows, the plant's (increment 6).
4. **Increment 9 (form and grid entry) is removed.** Its number is kept so the
   later increments keep theirs. A catalogue's difference from the current
   version, which it was to show, belongs to increment 7's confirmation.

## Consequences

**Easier:**

- No grid library, no licence question: question 8 of the plan is moot.
- One kind of input. Every row Bronze lands comes from an archived original,
  so a load's evidence is always one file, and the write path has no generated
  workbook, no staged edits and no second audit trail.
- The frontend stays thin: the issues list and the upload it already has.

**Harder:**

- A one-row change to a catalogue means opening the workbook, changing the
  row and uploading the whole file again. For catalogues of 50 to 1,800 rows
  this is minutes, not hours.
- People need Excel to correct data. They have it: the plant works in it.
- The fixes name the menus of Spanish-language Excel for Windows. Someone on
  another language or on a Mac reads menu names that differ from theirs.
- A correction is a second upload of the whole file, and a second archived
  original: at most twice the storage of a load.

## Alternatives considered

- **Edit cells of an uploaded draft in an MIT grid (the plan).** Rejected: the
  risk and the second source of truth above, for a task Excel already does.
- **Keep the one-row form for catalogues, drop only the grid.** Offered with
  increment 6's plan; the owner chose to remove both, so that every dataset is
  entered one way.
- **Replace the file of the same draft instead of linking a new one.** Rejected
  in increment 6: an archived original must never change, and the comparison
  needs both files.

## References

- [docs/frontend-plan.md](../frontend-plan.md), section 4 (the wizard), section
  5 (failure reasons), section 8 (rows 6, 7, 9 and 12), section 9 (questions 5
  and 8)
- [docs/api.md](../api.md): `POST /api/drafts/{draft_id}/corrections`,
  `GET /api/drafts/{draft_id}/cleared`
- [docs/dataset-specs.md](../dataset-specs.md): `entry_modes`
