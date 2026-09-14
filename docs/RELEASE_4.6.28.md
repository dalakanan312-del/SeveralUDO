# Decades Tracker 4.6.28 — Clock backlog performance

- Reuse the Sim identity lookup within each received report instead of repeatedly querying the same relatives and relationship targets. It is discarded between reports, preserves newly attached identities, and never crosses saves.
- Process reports on a receiver worker so database work and first-use name loading do not block the main page/live-status request loop.
- Refresh clock anchors under the save lock before applying a queued report.
- Deliver the next queued report after 25 milliseconds rather than 400 milliseconds. An idle relay still waits three seconds.
- Mark in-flight requests as processing, and allow the relay's bounded request timeout before the desktop supervisor restarts it.
- Keep every report in sequence. Files are removed only after the tracker acknowledges them; birth/death/pregnancy transitions and crash-recovery checks are unchanged.

This local maintenance build does not require replacing the in-game script or closing The Sims 4. The external relay must be restarted with its updated script.
