// SeveralUDO Sims 3 Clock Sync
// Protocol reference for the future automatic Sims 3 package reporter.
//
// A true in-game reporter must be compiled against the exact Sims 3 game
// assemblies and packed as a DBPF .package. Those proprietary assemblies and
// package build tools are intentionally not bundled with Decades Tracker.
//
// The resulting reporter should write one envelope file to:
//   Documents\Electronic Arts\The Sims 3\Mods\SeveralUDOClockSync\report_queue
//
// Envelope JSON:
// {
//   "receiver_url": ".../api/clock/report",
//   "sync_token": "private token",
//   "report_sequence": 1,
//   "payload": {
//      "protocol_version": 2,
//      "game_edition": "sims3",
//      "clock_sync_version": "Sims3-automatic",
//      "report_sequence": 1,
//      "report_checksum": "sha256 canonical JSON",
//      "save_identity": "stable Sims 3 save identifier",
//      "game_day": 1, "hour": 12, "minute": 0, "second": 0,
//      "household_members": [], "population_complete": false
//   }
// }
//
// The tracker already accepts this protocol. Until a compiled package is
// distributed, Report Sims 3 Clock Now.ps1 creates a protocol-v1 clock-only
// envelope without any in-game code.
