# Tracker 3.3.0

## 3.3.1 session fix

- Independent page updates now restore the active private workspace before accessing save data.
- If a workspace is intentionally locked, an older page fragment exits cleanly instead of showing a traceback.

## 3.3.2 Clock Sync instructions

- Added complete installation, everyday-use, command, status, privacy, ModGuard, and troubleshooting instructions directly to the Automatic Game Clock page.

## 3.3.3 detected-Sim dialog fix

- Detected baby and Sim review dialogs now restore the active private workspace during independent dialog reruns.
- Estimated birth dates and the Add Sim form submit button render normally instead of showing workspace and missing-submit errors.

## 3.3.4 unique detected-Sim IDs

- Automatically detected babies and Sims now receive canonical `SIM-####` IDs without an extra hyphen.
- ID allocation is protected by a PostgreSQL transaction lock so simultaneous additions cannot choose the same next number.
- Trailing hyphens are normalized defensively before any ID is generated.

## Life-stage portraits

- Every Sim can have a separate portrait for Newborn, Infant, Toddler, Child, Preteen, Teen, Young Adult, Adult, and Elder.
- The tracker automatically chooses the portrait matching the Sim's current age everywhere portraits appear.
- Deceased Sims keep the portrait matching the life stage they reached at death.
- Existing portraits remain available as the default fallback, so no current photos are lost.
- A new **Life-stage portraits** tab on each Sim profile shows portrait coverage and lets you add, replace, or remove each stage independently.
- Life-stage portraits are included in save exports, imports, and deletion cleanup.
