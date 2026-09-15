# 4.6.32 — Choose which dynasty branch to play

- Infinite Decades has a branch chooser for any waiting or paused line; players no longer have to play the newest split first.
- Players can pause an unfinished line and resume it later at its latest recorded day. The chooser requires a name for the current in-game checkpoint and confirmation that it was saved.
- Branch choices show their preserved date and matching game save. Completed lines remain read-only.
- Current records, portraits, and automation reviews are preserved when pausing. Shared decade snapshots stay outside the branch timeline.
- Switching creates a restorable backup, changes the stale-tab guard, and disables game reporting until the matching game checkpoint is reconnected and confirmed. The tracker does not save or load Sims game files.
- Failed restores roll back the entire switch rather than leaving a partly changed branch. Old checkpoints and the existing newest-branch action remain compatible.

Verification: 54 focused tests passed, including choosing an older branch, repeated pause/resume, inbox and portrait preservation, source-save ownership, completed-branch protection, confirmation requirements, stale tabs, failed-restore rollback, and existing dynasty/snapshot tests.
