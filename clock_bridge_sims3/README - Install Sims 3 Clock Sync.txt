SeveralUDO Sims 3 Clock Sync 1.1.0
===================================

This private kit links one Sims 3 Decades Tracker save to its Clock Sync link.
It never edits a .sims3 save, never changes gameplay, and never sends network
traffic from inside The Sims 3.

WHAT IT DOES
------------
SeveralUDOSims3ClockSync.package is a Sims 3 script mod that reads the loaded
town and writes a local snapshot. The separate relay uses your private
config.json to send that snapshot to the tracker.

The automatic town snapshot includes:
- Current Sims 3 day, hour, and minute
- Every Sim known to the loaded town, including their life stage and status
- Households, household names, funds, members, and played/unplayed status
- Pregnancy state and other parent, when the game provides it
- Parent and child genealogy links
- Romantic partners, romances, and strong friendships or enmities
- Visible traits, skill names and levels, careers, occult types, and deaths

To avoid falsely creating illnesses, the Sims 3 bridge does not treat ordinary
moodlets, temperature, or clothing as medical conditions. It also cannot
supply Sims 4-only milestones or portraits.

INSTALL (WITH SIMS 3 CLOSED)
----------------------------
1. Download the private Sims 3 kit from the Game Clock page for the tracker
   save you want to link, then extract the entire ZIP.
2. Open the SeveralUDOSims3ClockSync folder from the extracted kit.
3. Double-click Install or Update SeveralUDO Sims 3 Clock Sync.bat.
4. Run Test SeveralUDO Sims 3 Clock Sync.bat. It should say the private
   tracker link is ready.
5. Start SeveralUDO Sims 3 Clock Relay.bat once and leave its window open
   while playing. It is safe to minimize the window.
6. Open the Sims 3 save. Once the town has finished loading, the package
   writes a first full-town snapshot. It refreshes the snapshot every
   thirty in-game minutes while time is running.
7. In the tracker, refresh Game Clock after a minute or two. The first
   report anchors the calendars; later higher Sims 3 days advance Global Day.

The first full-town report can create review items for people the tracker has
not met before. This is expected: review or dismiss them in Automation rather
than creating a second clock link.

WHERE THE FILES GO
------------------
- Relay and private config:
  Documents\Electronic Arts\The Sims 3\Mods\SeveralUDOClockSync
- Automatic script package:
  Documents\Electronic Arts\The Sims 3\Mods\Packages\
  SeveralUDOSims3ClockSync.package

The installer places both automatically. Do not rename or share config.json:
it contains the private link for one tracker save.

MANUAL FALLBACK
---------------
Report Sims 3 Clock Now.bat is only for a one-off clock recovery report.
Normally, keep the relay running and let the automatic package report the
whole town.

IF NO REPORT ARRIVES
--------------------
- Confirm the package is visible in Mods\Packages.
- In Sims 3, open Options > Other, enable Script Mods, and restart the game
  after changing that option.
- Confirm the relay is still running and its self-test says the receiver is
  reachable.
- After loading a town, look for sims3_game_clock.json in:
  Documents\Electronic Arts\The Sims 3\Mods\SeveralUDOClockSync
- Read TROUBLESHOOTING.txt before creating another private link.
