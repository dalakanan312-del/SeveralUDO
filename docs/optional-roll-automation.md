# Optional roll automation

Open **Settings → Roll Automation**, or use its link on Today / Roll Tables.
All seven new category switches default off for existing saves. Enable the
desired category and the source rulepack/module. Per-rule switches are separate
from the module's enabled state. Master pause and frozen branches take priority.

## Scheduling

- Pregnancy allowance: once per historical year for living, fertile, capable
  Sims within configurable historical ages. Married-only and side-household-only
  filters default on. Missing main-household identity does not guess a household.
- HP: failed witch-hunt occurrence in 1300–1691 → HP-T02; HP-19 result 1–2 from
  1692 → HP-T03. One consequence per originating household event.
- Changeling: newborn human sharing a household or recorded country with a
  living fairy. A suspicion success uses the existing Child-stage truth chain.
- Mermaid dehydration: two independent checks from the current selected-core
  age table; a lethal result schedules dehydration as the cause. No substituted
  custom occult aging table, guessed die or unspecified bad result.
- Feeding: a recorded situation, or an accepted structured report with type
  `vampire_unwilling_feeding`, `confirmed: true`, matching `actor_sim_id` and
  tracker `sim_id`. Buff text does not count. Older Clock Sync reports without
  this evidence still require the player to record the situation.
- Avatar: recorded ancestry/birth, Child stage, confirmed manifestation,
  appropriately confirmed mastery/teacher/ancestry, and captured/missing status.
  Birth, element, strength, healing and other outcomes remain reviewable rolls;
  record resulting identity/mastery on the Avatar page when it becomes known.
- GoT: recorded House, feud/court conditions, winter/war exposure, Night's Watch,
  missing-person status, First Men birth, end-of-year irregular seasons, and
  five-year marriages without a recorded living child. Winter event and hardship
  tables are alternatives, not doubled death checks. Household narrative deaths
  require reviewing the affected person; the representative is never killed
  automatically. Update resolved missing/captivity situations in the profile.

## Situations the tracker cannot observe

Confirm a participant/household and prerequisites once under **Confirm a
situation**. Annual sources repeat while monitored. One-off sources require a
new situation, not a yearly repeat. Dynamic tables require choosing the actual
step and its source-specified die and outcomes; unspecified source numbers are
not invented. Source-defined connected steps (such as battle injury, tourney
injury, First Men gift type, hostile spirit possession and the second required
combustion aptitude check) queue automatically after a triggering result.

This is not blanket permission to attempt resurrection, enter tournaments,
perform rituals or expose every household to every war. Those remain choices.
Resurrection/exorcism/binding and unknown ancestry remain in their original
action workbenches. Advanced scenarios with unavailable prerequisites do not
silently run from calendar dates alone.

## History and safety

Scheduling starts on opt-in day, not at the beginning of the save. Repeated
reports/page loads reuse stable identities. Turning an option off retires its
unfinished generated checks, including linked optional follow-ups; results
already completed are preserved. Re-enabling may resume those paused checks,
but manual dismissals are not revived. Stop a monitored situation when resolved.
Correct completed children before reopening their parent. Consequence previews
include the actual generated rolls and detect changed automation settings.

No new database record types, schema migrations, game writes or network services.
