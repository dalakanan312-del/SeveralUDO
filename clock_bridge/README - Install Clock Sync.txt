SEVERALUDO CLOCK SYNC 2.0.2
Complete Windows installation guide
===================================

WHAT THIS KIT DOES
Clock Sync reads supported information from the active or played Sims 4 household and sends a report to one selected Decades Tracker save. The tracker never edits your Sims 4 save. New Sims and uncertain changes are placed in Automation Inbox for review.

THE FIVE FILES
1. SeveralUDOClockSync.ts4script - the Sims 4 Script Mod.
2. SeveralUDOClockRelay.ps1 - sends locally prepared reports to the tracker.
3. Start SeveralUDO Clock Relay.bat - starts the relay in the background.
4. config.json - your private connection to one tracker save. A reusable kit contains config-template.json instead.
5. This guide and TROUBLESHOOTING.txt.

VERIFY THE DOWNLOAD BEFORE INSTALLING
- Open the SeveralUDOClockSync folder inside the ZIP. The relay and starter are inside that folder, not beside it.
- Confirm SeveralUDOClockRelay.ps1 and Start SeveralUDO Clock Relay.bat are present.
- If Windows security hides either command file, use the matching .backup.txt recovery copy included in the same folder. Rename it by removing only .backup.txt, then confirm the restored name ends in .ps1 or .bat.
- KIT CONTENTS - VERIFY.txt lists every expected file and its checksum.

FIRST INSTALLATION - READY-TO-INSTALL PRIVATE KIT
1. Close The Sims 4 before installing or replacing a .ts4script file.
2. Download the private ready-to-install kit from the tracker Game Clock page. Download it only from the save you want the game to update. Creating this kit replaces that save's previous clock token.
3. Open the downloaded ZIP. Copy its SeveralUDOClockSync folder into:
   Documents\Electronic Arts\The Sims 4\Mods
4. Confirm this exact file exists no more than one folder deep:
   Mods\SeveralUDOClockSync\SeveralUDOClockSync.ts4script
5. In The Sims 4, open Game Options > Other. Turn on both Custom Content and Mods and Script Mods Allowed. Restart the game if you changed either setting.
6. Before opening the game, double-click Start SeveralUDO Clock Relay.bat. A window may flash and disappear; that is normal because the relay runs hidden.
7. Start The Sims 4, load a household, enter Live Mode, and let in-game time move for a moment. Changing lots also triggers a fresh report.
8. Open the tracker Game Clock page. Receiver should show Active and Last game day should stop saying Waiting.
9. Review new Sims, pregnancies, illnesses, deaths, relationships and other detected changes in Automation Inbox before accepting them.

INSTALLATION - REUSABLE KIT
1. Follow steps 1 through 5 above.
2. On the tracker Game Clock page, press Create or replace private clock link.
3. Download the generated private config.json. Do not rename it.
4. Put config.json beside the .ts4script and relay files. Delete or leave config-template.json; the mod reads config.json only.
5. Follow steps 6 through 9 above.

UPDATING CLOCK SYNC
1. Close The Sims 4.
2. Download the newest reusable complete kit.
3. Replace the .ts4script, relay and starter files in the existing SeveralUDOClockSync folder.
4. Keep your working config.json unless you intentionally created a new link for another tracker save.
5. Start the relay, restart the game and verify the Last game day on the tracker.

HOSTED AND DESKTOP EDITIONS
- Hosted tracker: config.json uses the private HTTPS Railway receiver.
- Desktop tracker: keep the desktop tracker and relay running while you play. Its config uses a local receiver.
- One config.json points to one tracker save. When changing to another tracker save, download and install that save's private config.
- Loading an older Sims 4 save never rewinds the tracker automatically.

SECURITY
- config.json contains a private token. Never upload it to GitHub, Discord, a mod-sharing site or a public support post.
- The reusable kit and config-template.json contain no secret.
- Creating or downloading another private kit disables the previous token for that tracker save.
- Save exports and tracker downloads do not include this token.

NORMAL DAILY USE
1. Start the relay.
2. Open The Sims 4 and play in Live Mode.
3. Keep the tracker open when convenient; reports can arrive while it is closed and will be visible when you return.
4. Review Automation Inbox before accepting uncertain changes.

Clock Sync reports game data; it does not install packs, change game saves, or bypass missing optional mods.
