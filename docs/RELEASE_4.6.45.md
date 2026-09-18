# Decades Tracker 4.6.45 — Unknown-ancestry d5

Harry Potter saves now offer **Roll unknown ancestry · d5** in the Sim's Blood
status panel and Harry Potter's Magical identity and life section.

- 1: Muggle
- 2: Muggle-Born
- 3: Squib
- 4: Half-Blood
- 5: Pureblood

Review the proposed identity before confirming. Declining releases the result
so the next roll is a fresh throw. Confirmed results stay in history and are
labeled **Rolled ancestry**, separate from the four-grandparent calculation.
No game data or relatives are invented. Keep the rolled result, enter a manual
correction, or return to automatic ancestry using the Harry Potter form.

Repeated clicks reuse the pending obligation. Known ancestry, deceased Sims,
frozen branches, and other saves are protected. Unfinished birth-identity checks
for the same Sim are retired after confirmation so they cannot overwrite the
chosen fallback; completed history is preserved.

This release includes the complete 4.6.44 tracker update: all-branch statistics,
grandparent-based magical ancestry, birth measurements and labor tracking,
maternal follow-ups, and the previously published safety and workflow fixes.

Validation: 1,034 Python tests passed against a fresh isolated test database;
41 interface tests passed. No new game mod or database migration is needed for
the ancestry d5. Install over the existing desktop version; saves remain in the
separate application-data folder. Back up saves before updating.
