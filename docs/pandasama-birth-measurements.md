# Optional PandaSama birth measurements

Prepared for tracker 4.6.44 and Clock Sync 2.2.13. This source update and its
rebuilt downloadable script are not an installation into the user's game.

## Use

1. Update both the tracker and Sims 4 script. Close Sims 4 before replacing the
   script; restart and load the household afterwards. No PandaSama files are
   bundled, changed, or required for the tracker to run.
2. With PandaSama Realistic Childbirth, edit each baby's birth certificate in
   game and save its birth details. Keep the object on the loaded lot or in a
   Sim's personal inventory. Unloaded household-storage objects may not be
   accessible to the game object managers.
3. Use explicit units: `Born 20 inches and 7.5 lbs.`, `7 lb 8 oz; 20 inches`,
   or `Weight 3.4 kg; length 50.8 cm`. Other localized wording works when it
   retains supported units (g, kg, lb, oz, cm, in). Unsupported text can be
   entered manually in the tracker.
4. Hospital rewards carry a baby identity where available. An unbound home-birth
   certificate must have the baby's exact full name, unique among loaded Sim
   records. Duplicate names and conflicting certificates are deliberately not
   guessed. Each multiple-birth baby needs their own certificate.
5. Review a new baby in Automation Inbox, then open their profile > Birth weight
   & length. The values also appear with their linked pregnancy and Today birth
   checks. Existing tracked Sims receive readable certificate details on a new
   report. Older saved certificates can also be read; age is not a restriction.
6. Add/correct values on the profile when needed. Manual entries are protected
   by default. Check “Allow future certificates to update these values” to let
   game certificate entries update them again. Leave unknown fields blank.

## Evidence and boundaries

PandaSama's official instructions describe customizable birth certificates:
https://www.pandasama.com/child-birth-mod/

Read-only inspection of the locally installed Childbirth 1.965 tuning verified:

- Certificate definition IDs: 9454473550490189272 and 9454473550490189273;
  object tuning: 10745341224055388351.
- The certificate rename interaction (14930385376800339966) supplies suggested
  weight/length descriptions, then saves editable text in the game object's
  custom description. The tracker does not invoke that interaction or sample
  its random suggestions. An unedited certificate may contain no measurements.
- Hospital reward tuning stores TargetSim information; home-birth creation may
  lack that identity. Inventory owner and birth order are never baby identities.
- The installed game's name component uses `custom_name` / `custom_description`;
  stored-Sim component exposes `get_stored_sim_id`.

Values are labeled **Certificate entry**, not Game-confirmed physical data.
No body-weight statistic, pregnancy-risk marker, health diagnosis, or generic
“low birth weight” condition is converted into numeric measurements. No rolls,
health outcomes, or gameplay are changed by measurements. Reports missing these
optional fields never erase prior measurements. Manual form conflict checks
cover measurements only, so ordinary clock updates do not block saving.

The reader scans available lot/inventory objects once per population snapshot
(bounded at 10,000 objects), keeps distinct certificate object IDs, reads only
known PandaSama certificate definitions, and imports no third-party mod code.
Unavailable services/components are tolerated. Verified by isolated collector,
parser, ingestion, form and template tests; live in-game capture still requires
installing the new script and receiving a fresh game report.

## Labor length

The pending tracker 4.6.44 / Clock Sync 2.2.13 update also records labor length
on the pregnancy, once per delivery regardless of the number of babies.

- Recognizes PandaSama maternal InLabor / preterm InLabor and contraction-pain
  buffs verified from the installed 1.965 package. It does not match partners,
  visitors, generic discomfort, prelabor moodlets, or a percentage of dilation.
- Records the first labor report and first subsequent report where pregnancy
  has ended. Elapsed **in-game** hours/minutes are an estimate: polling and
  missing reports can miss the true onset or delivery. No real-time clock or
  challenge-year scaling is used. Starting mid-labor cannot reconstruct its
  earlier hours. Unknown historical durations remain unknown.
- Look under **Pregnancy > Labor length**, parents' pregnancy histories, and
  Today's grouped delivery panels. The baby's measurement section links to
  the shared pregnancy record.
- Enter a manual duration in hours and additional minutes (0–59). It overrides
  the display without erasing observed evidence and cannot be overwritten by
  reports. Select the observed estimate to remove the override.
- Pregnancy cycles remain separate. A cycle first observed while its Inbox
  review is pending can be attached when accepted. Older ambiguous detections
  without a pregnancy cycle require manual entry rather than borrowing the
  latest pregnancy's labor.
- Missing labor buffs alone do not finish timing; the pregnancy must report
  ended. Repeated reports do not restart or extend a completed interval.
  Clock-recovery / Infinite Decades epoch changes interrupt timing; observations
  from different epochs are not combined. No pregnancy outcome, survival roll,
  or Sim's birth/death status is changed by this feature.

## Birth-measurement verification

Labor addition verification: the full isolated tracker suite passed 981 tests.
The final combined labor / birth-measurement / Clock Sync focused run passed
all 47 tests, including two additional late-acceptance and ambiguous-legacy
pregnancy cases added after the full run began. All affected templates compile.
The script was rebuilt with Python 3.7. No installed game files or user saves
were changed; a fresh in-game report is still needed for live verification.

- Full isolated tracker suite: 962 tests passed (including the first 14 birth
  measurement tests). No user save was used; background integrations disabled.
- Final focused birth suite: all 16 passed, including full receiver → newborn
  review → accepted Sim storage, plus nested ZIP kit contents.
- Clock Sync rebuilt using the game's Python 3.7 compiler. The existing core,
  compatibility reader and embedded label dictionary remain in the archive.
