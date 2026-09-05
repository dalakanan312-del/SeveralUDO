SeveralUDO Sims 3 Clock Sync 0.1.0
====================================

This kit connects a Sims 3 Decades Tracker save to its private Clock Sync link. It never edits a Sims 3 save file.

IMPORTANT STATUS
----------------
This is the Sims 3 manual clock bridge. The tracker and relay are Sims 3-aware and safely pair one named Sims 3 save with one tracker chronicle. Because Sims 3 uses a different package-based scripting system, this release does NOT include a compiled .package game mod. It reports the exact game day and time you enter from the in-game clock.

INSTALL
-------
1. Download the private Sims 3 kit from a Sims 3 tracker save.
2. Extract the ZIP.
3. Open SeveralUDOSims3ClockSync and double-click Install or Update SeveralUDO Sims 3 Clock Sync.bat.
4. Run Test SeveralUDO Sims 3 Clock Sync.bat. It checks the private link without changing the tracker.
5. Start SeveralUDO Sims 3 Clock Relay.bat once. It can stay open in the background.

DAILY USE
---------
1. Open your Sims 3 save.
2. Read the in-game day and time.
3. Double-click Report Sims 3 Clock Now.bat and enter that day, hour, and minute.
4. On the first report, give the save a stable name. Reuse that exact name for future reports.
5. Refresh Game Clock in the tracker. The first report anchors the calendars; later higher game days advance tracker Global Day.

WHAT IT DOES
------------
- Safely links a named Sims 3 save to one Decades Tracker save.
- Queues reports offline and sends them in order through the local relay.
- Advances the tracker only forward from higher reported Sims 3 days.
- Keeps the manual reports separate from Sims 4 telemetry.

WHAT IT DOES NOT YET DO
-----------------------
- It does not read Sims, pregnancies, health, traits, or births automatically.
- It does not install or impersonate a Sims 4 .ts4script.
- It does not contain a compiled Sims 3 .package. A genuine automatic Sims 3 reporter must be compiled against the player's Sims 3 scripting assemblies and packaged as a .package before it can run in-game. The included source/protocol reference documents that future component.

PRIVATE CONFIG
--------------
config.json is private. Do not share it. Downloading another private kit rotates the link and stops the old config from working.
