# Tracker audit repairs — 4.6.44

## Save safety

Whole-dynasty copies now remap the compressed payload inside pending people transfers, not only their outer Sim IDs. Receipt validates every embedded record and portrait before changing anything. An older copy with mismatched internal IDs is refused; export a fresh copy from the original save using this release. Do not guess which original person an invalid copy should change.

Hosted sync validates the authenticated device, save, record type and duplicate-change ownership. Desktop sync rejects records belonging to other local saves before comparing versions and does not advance its cursor on rejection. A supplied remote save ID must match the token. Infinite Decades still uses whole-dynasty exports rather than partial cloud sync.

## Security

Cross-site browser mutations and cross-site requests to the local tracker are rejected. Only the actual bearer-authenticated clock report/ping and sync push/pull endpoints bypass the browser-origin guard; their own token verification remains mandatory. Same-origin forms, no-JavaScript forms and native relay requests remain supported.

Hosted startup now requires a private SESSION_SECRET or the existing OWNER_ACCESS_KEY fallback. It refuses the known development signing key. Deployment must set one of those values before startup. Secrets in nested settings are filtered consistently in all exports.

## History and backups

- Change payloads use checksummed, lossless compression. Record IDs, change IDs, sequence numbers, versions and every historical value are preserved. Legacy plain JSON remains readable; cloud requests and exports still contain normal JSON.
- Data & Rules Health has bounded 250-entry compression batches and a stop/resume bulk button. New history is compressed automatically. Existing history is not silently rewritten at startup.
- Make a full database backup before bulk compression. A save export is not a backup of the complete change journal. Compressed database history requires 4.6.44 or newer; restore the full pre-update database before downgrading. Freed SQLite pages are reusable but the database file may not immediately get smaller. No automatic VACUUM or history deletion is performed.
- Backup listings and retention queries no longer load package binaries. Exports stream record serialization and read portraits in batches. Normal retention remains 14 backups per save; other `infinite:` backups are capped at 14 while original `infinite:before-enable` safety points are protected.
- Imports enforce compressed size, total expanded size, manifest size, entry count, and duplicate record/member limits before restoring records.
- Background failures now produce credential-free status messages in Sync and Health and a notice on the next page load. These are operational, in-process statuses, not a new game-history ledger.

## Consistent records and navigation

Active life/death decisions share one helper. Historical world views explicitly evaluate the selected past date, so a later death does not erase someone from an earlier census.

Linked heirlooms use the dated dynasty history as the ownership source for Historical Life. Handovers update the base record; earlier branches project the owner at their own day. The old direct-transfer control directs linked objects to the dated handover review rather than maintaining a competing owner.

The sidebar groups existing tools into Household planning, Inheritance & family legacy, and History & writing. Their pages have shared workflow navigation and purpose descriptions. All 61 feature routes remain reachable and searchable; no specialist tool or player record is removed. The two Today views now share scheduling, though their distinct presentations and filters remain for compatibility.

The installer fallback now opens the latest published release, rather than pinning 4.6.27. DESKTOP_INSTALLER_URL can still point to a specific published installer.

## Save-a-Sim undo addition

The Save-a-Sims credit ledger now includes **Undo Save-a-Sim** for spent credits. Undo returns one credit, restores the original unconfirmed scheduled death and re-retires the future rolls that the rescue restored. It does not confirm a death or control the game. Both the spend and its refund remain visible; refunds do not inflate earned-credit totals.

New rescues retain portable before/after snapshots of the affected fields and records. Older rescues can be reconstructed when the local change journal retains their original versions. An incomplete old history is refused rather than guessed. Later death/rescue changes, completed or changed related rolls, confirmed game deaths, and inactive branches prevent an unsafe undo. Unrelated Sim edits are preserved. Repeated requests cannot return the credit twice, and a reversed rescue can be used again.

## Maternal Mortality additional rolls

Implemented from the player's supplied Maternal Mortality screenshot. A failed first maternal check now creates one distinct death-or-infertility follow-up for that baby, without immediately scheduling death or retiring future obligations.

| Maternal table | Additional roll | Death result | Other results |
| --- | --- | --- | --- |
| Preteen | d6 | 1 | Survives traumatic birth; infertile |
| Teen | d4 | 1 | Survives traumatic birth; infertile |
| Young Adult | Coin flip, represented by d2 | 1 = heads | 2 = tails; survives traumatic birth; infertile |
| Adult | d4 | 1 | Survives traumatic birth; infertile |
| Elder | d10 | 1 | Survives traumatic birth; infertile |

Morbid's maternal tables use only the Young Adult additional table (coin flip), regardless of age. Classic 2023 is not given SeveralUDO follow-ups. Existing age-specific tables are used at the delivery date, not the date on which a delayed follow-up is resolved.

The normal roll preview shows the follow-up or fertility consequence before confirmation. Infertility appears on the profile and blocks new/unfinished pregnancy-count planning; existing pregnancies and birth records remain. Reopening the follow-up reverses only its recorded infertility contribution; reopening a parent with a completed complication result is blocked until that follow-up is reviewed first.

Completed legacy mortality results and spent Save-a-Sim credits are not silently rewritten. Previous live save corrections remain unchanged. This source update is not yet installed or deployed.

## Verification history

- Harry Potter blood status now uses all four grandparent positions rather than
  the old two-magical-parent birth shortcut. Profiles (including frozen dynasty
  profiles) explain the evidence; automatic persistence respects HP-04/master
  switches, manual overrides and frozen-record protection. See
  `harry-potter-blood-status.md`. All 1,023 Python tests passed, including 24 new
  bloodline tests; affected templates compiled and scoped whitespace checks
  passed. No installed save or Clock Sync files were changed for this feature.

- Statistics now defaults to the whole dynasty, including waiting, paused and
  finished branches. Read-only branch filters and comparisons use canonical
  record IDs instead of adding checkpoint copies. Each retained person's age,
  scheduled death and pending obligations are evaluated at their observed date;
  these are combined last-recorded states, not a common-year census. Starting
  world is shown only when it still owns people. Shared households count once
  overall. Viewing a branch does not switch play or restore a checkpoint.
  Spoiler-free history limits, hidden events and retired obligations are honored.
  Only branch metadata is queried; portrait-bearing snapshots and journals are
  not unpacked. No schema or live-save changes are required.
- Statistics includes birth-size and labor averages with sample sizes and
  provenance. Missing values are excluded, units normalized, manual labor values
  take priority, and twins share one pregnancy-duration observation. Completed
  multiples use actual births instead of forecasts. Age summaries now show years
  and days; the childhood-survival measure scales the age-18 threshold for the
  selected year length and does not count future scheduled deaths as losses.
  Confirmed deaths without dates remain deceased without inventing ages.
- Statistics verification: all 999 Python tests passed, including 16 new
  statistics tests; templates compiled and scoped whitespace checks passed.
  A read-only Black HP check returned 31 unique Sims across six branch groups
  (branch population sum also 31), in approximately 0.06 seconds. This is a
  calculation timing, not a guarantee of full-page latency. No live records were
  edited. These changes remain in the pending, uninstalled source update.

- Labor length added to pregnancy records using maternal labor signals and
  in-game timestamps, with protected manual overrides and conservative
  pregnancy-cycle / clock-recovery handling. Full suite: 981 passed; final
  combined labor, measurements and Clock Sync focused suite: 47 passed. This
  includes two tests added after the full run started. Script rebuilt, not installed.

- Optional PandaSama birth-certificate weight/length support is now included;
  see `pandasama-birth-measurements.md`. Clock Sync 2.2.13 was rebuilt, not
  installed. The expanded full suite passed 962 tests; the final focused birth
  suite passed all 16, including two subsequently added integration checks.

- With the maternal additional-roll implementation, all 945 Python tests passed, including the initial 15 maternal regression tests. Tests use disposable databases, not the installed save.
- The final maternal-focused run passed all 18 tests, including added missing-age, confirmed-death, wrong-parent and reopen-route checks.

- 913 Python tests passed against an isolated temporary database, including 23 audit regression tests. Background integrations were disabled; the unrelated experimental Sims 3 clock-only tests were excluded.
- After adding Save-a-Sim undo, the broader 928-test run passed. The final focused Save-a-Sim run passed all 20 tests, including two additional edge cases for repeated awards and older rescues that affected another Sim's rolls. These counts overlap; they are separate runs.
- All 40 JavaScript tests passed. Updated browser scripts passed syntax checks, and tracker changes passed whitespace checks.
- A read-only sample of 100 existing Sim-history entries compressed from 4,453,155 to 1,168,132 bytes (73.8% smaller), with exact decoded equality checked for every entry. This is a sample, not a guarantee for every save. No existing user history was rewritten.

## Release boundary

These are tracker source changes. The unrelated Sims 3 experimental files are excluded. Installation/deployment and compression of an existing user database are separate operations; this repair work does not perform them automatically.
