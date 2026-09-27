# Optional roll automation

Open **Settings → Roll Automation**, or use its link on Today / Roll Tables.
All optional category switches default off for existing saves. Enable the
desired category and the source rulepack/module. Per-rule switches are separate
from the module's enabled state. Master pause and frozen branches take priority.

## Scheduling

- Pregnancy allowance: once per Sim, setting a lifetime total using the era table
  when first rolled. Recorded pregnancies in every year consume that one total;
  twins/triplets use one allowance. Existing completed results stay in history,
  the currently recorded allowance is preserved, and duplicate unfinished count
  rolls are retired.
- Annual Baby Roll: a separate toggle, using the supplied D20 age bands and
  modifiers by default. Runs once per historical year for eligible Sims with a
  completed lifetime allowance remaining, no current pregnancy and no recorded
  conception that year. A success creates a Today task to have a baby that year,
  not a fictional pregnancy or a new allowance. Actual births still go through
  normal multiples, birth, newborn, infant-survival and maternal checks.
  Optional custom fixed odds and independent married-only/side-household
  restrictions are available. Missing main-household identity never guesses one.
  Explicit Sim or household restrictions take priority. Teen eligibility must
  be confirmed from the timeline/class/theme rules.
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

## Annual Baby Roll details

Age is measured at the beginning of the historical year and scales with the
save's days per year. The D20 base success range is 1 at 13–17, 1–7 at 18–24,
1–6 at 25–29, 1–5 at 30–34, 1–4 at 35–39, 1–2 at 40–44 and 1 at 45–49.
There is no natural annual roll below 13 or from 50 onward.

Add +1 for the heir household, +1 for marriage/established partnership, −4 for a
birth in the previous historical year, −2 for an Infant/Toddler in the household,
−2 for no partner/widowhood/separation, and +2 for a confirmed fertility treatment
or theme bonus. Twins count as one previous-year birth, and multiple young
children apply the household penalty once. Clamp the final upper bound to 1–10.
The married-27 example is 1–7, reduced to 1–3 after a previous-year birth.

Roll Automation's known-prerequisite editor records permission, earliest allowed
year, restriction notes, fertility bonus, and otherwise-unrecorded established
partnerships. Ordinary friendship/romance records are not assumed to establish a
partnership. Rolls display their source calculation and modifier breakdown.
Old unfinished yearly decisions expire; completed history is never repeated.
