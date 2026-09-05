SeveralUDO Sims 3 Clock Sync 1.0.0
===================================

This kit links one Sims 3 Decades Tracker save to its private Clock Sync link.
It never edits a .sims3 save.

WHAT IS NEW
-----------
SeveralUDOSims3ClockSync.package is a real Sims 3 script mod. It reads only
the active save's in-game day and time, writes a small local snapshot, and
leaves the relay to send that snapshot safely to your private tracker link.

INSTALL (WITH SIMS 3 CLOSED)
----------------------------
1. Download the private Sims 3 kit from your tracker save and extract it.
2. Open SeveralUDOSims3ClockSync.
3. Double-click Install or Update SeveralUDO Sims 3 Clock Sync.bat.
4. Run Test SeveralUDO Sims 3 Clock Sync.bat.
5. Start SeveralUDO Sims 3 Clock Relay.bat once. Leave its window open while
   playing.
6. Open your Sims 3 save. The game package makes its first automatic snapshot
   just after the world finishes loading, then refreshes it every ten in-game
   minutes while the clock runs.
7. Refresh Game Clock in the tracker after a minute or two. The first report
   anchors the calendars; later higher game days advance Global Day once.

WHERE THE FILES GO
------------------
- The relay and private config go in:
  DocumentsElectronic ArtsThe Sims 3ModsSeveralUDOClockSync
- The automatic script package goes in:
  DocumentsElectronic ArtsThe Sims 3ModsPackages+    SeveralUDOSims3ClockSync.package

The installer places both of these for you. Do not rename config.json and do
not share it: it contains the private link for one tracker save.

WHAT THE AUTOMATIC PACKAGE READS
--------------------------------
- Current Sims 3 game day
- Current in-game hour and minute

It does not read or modify save files, Sims, pregnancies, health, traits,
portraits, or any other gameplay data. The relay—not the package—uses your
private config to send the clock report.

MANUAL FALLBACK
---------------
Report Sims 3 Clock Now.bat remains in the kit for a one-off recovery report.
Normally you do not need it: use the automatic package and leave the relay
running instead.

IF NO REPORT ARRIVES
--------------------
- Confirm the game package is visible under ModsPackages.
- Confirm script mods are enabled in Sims 3's Options > Other, then restart
  the game after changing that option.
- Confirm the relay remains open and its self-test shows the receiver is
  reachable.
- Read TROUBLESHOOTING.txt before creating another private link.
