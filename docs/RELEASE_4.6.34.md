# 4.6.34 — Event eligibility and household rolls

- Local instructions behind “Global / See Notes” no longer become worldwide rolls. Unidentified local targets wait for location evidence instead of matching everybody.
- Residence and dated migrations take precedence over birthplace and unrelated household/challenge defaults. Global events still respect class and other eligibility restrictions.
- Source instructions distinguish a single household check from per-Sim checks, including mixed and recurring tables. Livestock checks do not target human Sims, and specified farming/trading/bakery/fishing subjects require matching evidence.
- Conditional per-Sim follow-ups fan out from an affected household only when triggered. A household result explicitly requiring one Sim's death selects one eligible member using the existing reviewable RNG; other ambiguous lethal household instructions are marked for review without silently killing a representative.
- Main-household restrictions, calendar-scaled age stages, and separate sex-specific dice are retained during scheduling and refresh.
- Pending automatic obligations with invalid targets or scope are safely archived with reasons. Completed results, manual rolls, and frozen branches are preserved; completed legacy per-Sim results prevent a new household reroll of the same occurrence.
- Event editors offer “Who rolls?” and required occupation controls. Household cards identify the shared unit, and explanations retain eligibility evidence.

Validation includes isolated eligibility/scope cases, existing event and campaign coverage, branch isolation, roll preview confirmation, clock performance, and crash recovery. A read-only-derived in-memory preview identified four inappropriate pending obligations in the affected save, with completed and frozen history unchanged and no repeated repairs on a second pass.
