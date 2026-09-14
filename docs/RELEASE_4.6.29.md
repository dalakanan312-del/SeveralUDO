# 4.6.29 — Same-name Sims in decade snapshots

- Tray scans retain all same-name Sims in the newest saved household, instead of collapsing them to one name entry.
- Duplicate names can match by a unique game life stage in both directions (for example, a parent and child sharing a name). One remaining pair may retain an earlier-stage Library portrait after the others are resolved. Same-name, same-stage ambiguity remains protected; scans never guess between twins.
- Per-Sim scans check the whole save for ambiguous names, and imported photos retain their Tray life stage.
- Decade snapshots record the pictured count and missing names, which remain visible in the archive. Updated images use versioned links to avoid stale browser images.
- Archive repair can retain its original Global Day, including members whose recorded deaths occurred later, without advancing or rewinding the save.

Tray age flags follow EA's CAS AgeGender values as documented in [TS4 Sim Ripper's enums](https://github.com/CmarNYC-Tools/TS4SimRipper/blob/main/src/Enums.cs). Library identifiers are not treated as persistent live-game Sim identifiers.

Validation: eleven focused matching/archive regressions plus existing Tray import and manual-portrait protection tests.
