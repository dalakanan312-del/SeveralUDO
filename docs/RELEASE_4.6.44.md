# Decades Tracker 4.6.44

## Highlights

- Whole-dynasty statistics, branch comparisons and filters, preserved branch
  ages, scaled childhood-survival calculations, and birth/labor summaries.
- Harry Potter blood status requires four confirmed spellcaster grandparent
  positions for Pureblood. Profiles show the evidence, Half-Blood and
  Muggle-Born cases remain distinct, and missing ancestry stays Unknown.
  Clearly labeled manual overrides remain available.
- Save-a-Sim undo and restored maternal complication follow-ups, including
  separate baby-specific checks and infertility outcomes.
- Optional PandaSama birth-certificate weight and length tracking, plus estimated
  in-game labor duration and protected manual corrections. Clock Sync 2.2.13 is
  included in the downloadable kit. Install that game script with Sims 4 closed;
  updating the tracker alone does not replace an installed game script.
- Save ownership/sync validation, request-safety fixes, background status,
  bounded backup handling, and opt-in history-storage compaction.
- Includes the previously local-only dynasty register, growing decade albums,
  independent branch calendars, branch play tools and shared dated history.

## Upgrade notes

The desktop installer updates application files in place. Saves, accounts,
portraits and relay configuration remain in their existing data directories.
A database backup is recommended before upgrading. Existing completed death
rolls and spent Save-a-Sim credits are not silently rewritten.

History compaction is optional, not an automatic cleanup. Compacted local history
requires tracker 4.6.44 or later; use a pre-update backup to roll back to an older
tracker. The hosted sync interface continues to send ordinary record data.

No experimental Sims 3 package or replacement Sims 3 clock mod is included.

## Verification

All 1,023 Python tests passed against disposable databases, including 24 new
bloodline and 16 statistics tests. The unrelated experimental Sims 3 clock-only
tests were excluded. Affected templates and scoped whitespace checks passed.
See the audit, blood-status and birth-measurement documentation for details.
