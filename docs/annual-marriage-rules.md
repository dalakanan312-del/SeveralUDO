# Annual era-based marriage and remarriage

Relationships → **Yearly marriage & remarriage** (also in Roll Tables) lets each
save choose the supplied yearly table or keep its existing one-time/custom rules.
Changing mode previews pending obligations before confirmation; completed results
are not rewritten. Enabling the mode starts in the current calendar year, not at
the beginning of every Sim's life.

- Every eligible living Sim in the active branch, including heirs, gets at most
  one check per calendar year. The calendar uses the save's days-per-year value.
- Young Adult is 18–39, Adult is 40–59, and Elder is 60+. No Pre-Teen checks.
  The save's minimum marriage age is still enforced. Teen odds are not permission
  to lower that age.
- All nine supplied era rows are transcribed in `app/marriage_rules.py`. Where
  the supplied ranges meet, the newer row wins: -3000, 500, 1000, 1450, 1750,
  1900, 1946 and 1970. Years before -9000 are outside this table.
- Remarriage begins the calendar year after the latest prior marriage ended.
  Years 1–5 use early odds. An optional, explained practical extension uses 1–10.
  Elders always use late odds. A missing end date needs review; separation while
  still legally married is not permission to remarry.
- Player-configured regional/class/faith/custom blocks take precedence over
  permissions, and matching minimum ages can only raise the save baseline.
  These controls are not an inferred historical law database. A Sim can also be
  blocked individually, restricted until a year, or exempted for a timeline-required
  arranged/political marriage. Structured events may supply `required_marriage_sim_ids`.
- A successful result generates a same-year suggested date if an eligible spouse
  exists. Choose the spouse to create/update a betrothal; record the actual wedding
  after playing it. No Sim or in-game wedding is invented automatically.
- Without a spouse, the successful roll remains an arranged-match task and the
  Sim rolls again next year. Refusal is an explicit d6 follow-up: only 1 succeeds.
  A failed refusal cannot be requested repeatedly. Normal result correction is
  supported until a wedding plan relies on the result.
- Unresolved older yearly checks expire without an invented outcome. Existing
  completed history, other saves, frozen branches, and the old custom tables are
  preserved. The app-wide automation toggle is honored by scheduling.

There are no new sync record kinds or game-mod requirements. This is tracker-side
planning and uses the existing roll preview, journal, backup and sync systems.
