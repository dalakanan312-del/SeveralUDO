# Decades Tracker 4.6.47 — Scheduled death reminders

- A “Deaths due today” popup names each Sim, the scheduled Global Day, and cause of death, with links to review and confirm the details.
- Overdue unconfirmed deaths remain visible, including after the tracker advances a day.
- Dismissing the popup leaves a “Review deaths” banner available. Unchanged reminders do not reopen on every Clock Sync report.
- Reminders follow the active save and branch, exclude confirmed deaths and paused branch records, and pause during crash recovery.
- Reminders wait while another confirmation dialog is open. They never confirm deaths or edit Sim records themselves.
- Works across tracker pages, with responsive layouts for mobile and the selected visual theme.

This update preserves saved results, rules and the installed game mod. It does not include the unrelated Sims 3 experiments.

Validation: 92 Python regression tests, 26 JavaScript checks, four popup browser scenarios, and six existing Today layout/control checks across dark/light themes and desktop/tablet/phone widths.
