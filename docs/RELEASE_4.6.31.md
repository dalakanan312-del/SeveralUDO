# 4.6.31 — Living Sims only for new branch splits

- The departing-Sim picker excludes deceased, unborn, and frozen Sims.
- A scheduled future death does not hide a Sim who is still alive at the split date.
- Stale or manually submitted selections containing deceased Sims are rejected before creating a branch or changing its membership.
- Existing branch history, dynasty member lists, starting-world checkpoints, and decade snapshots are unchanged.

Verification: 48 focused tests passed, including new split-picker, stale-form, and future-death cases alongside existing dynasty and album coverage.
