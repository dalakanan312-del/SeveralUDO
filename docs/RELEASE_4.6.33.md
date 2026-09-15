# 4.6.33 — Clock reporting after a branch switch

- Clock reports now append only to a live journal in the current Infinite Decades branch. A paused branch can have the same game day and Global Day without blocking reports or having its history changed.
- Archived journals are not reused, and frozen Sims cannot become the new branch's journal narrator.
- All existing branch protections, report ordering, and duplicate-report checks remain intact. No game mod update is needed.

Verification: 56 focused branch, dynasty, and snapshot tests passed. An isolated, in-memory copy of the affected save accepted all 16 waiting reports and advanced correctly on a simulated next-day report; the actual save was not advanced by this test.
