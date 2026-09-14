# Decades Tracker 4.6.28 — Clock backlog performance

- Add a missing combined lookup index to older local databases on startup. Existing tables were not receiving the newer index through create_all(), causing small queries to scan records across every save. No saved record is changed by this upgrade.
- Reuse the Sim identity lookup within each received report instead of repeatedly querying the same relatives and relationship targets. It is discarded between reports, preserves newly attached identities, and never crosses saves.
- Process reports on a receiver worker so database work and first-use name loading do not block the main page/live-status request loop.
- Refresh clock anchors under the save lock before applying a queued report.
- Deliver the next queued report after 25 milliseconds rather than 400 milliseconds. An idle relay still waits three seconds.
- Enumerate queued filenames directly instead of rebuilding thousands of file metadata objects for each delivery and heartbeat; sequence order stays unchanged.
- Mark in-flight requests as processing, and allow the relay's bounded request timeout before the desktop supervisor restarts it.
- Keep every report in sequence. Files are removed only after the tracker acknowledges them; birth/death/pregnancy transitions and crash-recovery checks are unchanged.

This local maintenance build does not require replacing the in-game script or closing The Sims 4. The external relay must be restarted with its updated script.
