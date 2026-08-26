SEVERALUDO CLOCK SYNC 2.2.6
Complete Windows installation guide
===================================

WHAT THIS KIT DOES
Clock Sync reads supported information from the active or played Sims 4 household and sends a report to one selected Decades Tracker save. The tracker never edits your Sims 4 save. New Sims and uncertain changes are placed in Automation Inbox for review.

THE INCLUDED TOOLS
1. SeveralUDOClockSync.ts4script - the Sims 4 Script Mod.
2. SeveralUDOClockRelay.ps1 - sends locally prepared reports to the tracker.
3. Start SeveralUDO Clock Relay.bat - starts the relay in the background.
4. Install or Update SeveralUDO Clock Sync.bat - backs up and installs this exact kit, while moving exact-name duplicate Script Mods into a recoverable backup.
5. Test SeveralUDO Clock Sync.bat - checks the install, private config, queue, and tracker receiver without changing tracker time.
6. config.json - your private connection to one tracker save. A reusable kit contains config-template.json instead.
7. This guide and TROUBLESHOOTING.txt.

VERIFY THE DOWNLOAD BEFORE INSTALLING
- Open the SeveralUDOClockSync folder inside the ZIP. The relay and starter are inside that folder, not beside it.
- Confirm SeveralUDOClockRelay.ps1 and Start SeveralUDO Clock Relay.bat are present.
- If Windows security hides either command file, use the matching .backup.txt recovery copy included in the same folder. Rename it by removing only .backup.txt, then confirm the restored name ends in .ps1 or .bat.
- KIT CONTENTS - VERIFY.txt lists every expected file and its checksum.

FIRST INSTALLATION - READY-TO-INSTALL PRIVATE KIT
1. Close The Sims 4 before installing or replacing a .ts4script file.
2. Download the private ready-to-install kit from the tracker Game Clock page. Download it only from the save you want the game to update. Creating this kit replaces that save's previous clock token.
3. Extract the ZIP, open its SeveralUDOClockSync folder, and double-click Install or Update SeveralUDO Clock Sync.bat. The installer backs up the prior folder before copying the new files.
4. Confirm this exact file exists no more than one folder deep:
   Mods\SeveralUDOClockSync\SeveralUDOClockSync.ts4script
5. In The Sims 4, open Game Options > Other. Turn on both Custom Content and Mods and Script Mods Allowed. Restart the game if you changed either setting.
6. Run Test SeveralUDO Clock Sync.bat. When it passes, double-click Start SeveralUDO Clock Relay.bat. A window may flash and disappear; that is normal because the relay runs hidden.
7. Start The Sims 4, load a household, enter Live Mode, and let in-game time move for a moment. Changing lots also triggers a fresh report.
8. Open the tracker Game Clock page. Receiver should show Active and Last game day should stop saying Waiting.
9. Review new Sims, pregnancy starts and endings, deaths, resurrections, relationships and illnesses in Automation Inbox before accepting them. Skills, milestones, life-stage progress, careers, education, occult progress, aspirations and other safe profile details synchronize directly.

CLOCK SYNC 2.2 DETAIL
- The first complete report establishes a played-population baseline. Later unchanged reports are omitted or sent as smaller deltas, with one guarded full population check each game day.
- Every report has an ordered sequence and SHA-256 checksum. The relay stores reports in report_queue while offline and sends them oldest first after reconnection.
- The tracker binds one Clock Sync link to one Sims save slot. A different save is rejected before tracker data changes unless you explicitly clear the pairing on Game Clock.
- Reports include exact game seconds and stable tuning IDs when the game exposes them. Labels remain readable when IDs are unavailable.
- Missing relationships, recovered illnesses, ended pregnancies, moves and Sims absent from the full played population become guarded updates or review items rather than silent deletion.
- Pregnancy reports can include stage, time remaining, labor and expected babies. A pregnancy ending with zero detected newborns stays zero for review instead of being changed to one.
- Genealogy includes available parents, children, siblings, grandparents and grandchildren. The tracker can fill a missing parent link from the parent's child list.
- Relationships can include game relationship bits plus available friendship and romance scores. Family-changing transitions remain reviewable.
- Death reports include the game cause and available place details. A previously dead Sim reported alive creates a resurrection review instead of silently changing history.
- Health telemetry promotes recognized illness and symptom buffs into reviewable illness episodes. Missing optional health mods remain safe.
- Life-stage progress, career and school details, degrees, occult ranks and powers, aspirations, lifestyles, fears, character values and preferences appear on Sim profiles when the game exposes them.
- The Game Clock page shows a self-diagnostic capability report for your game build, installed packs and supported telemetry.
- Portrait capture is best effort because some Sims 4 builds expose only a resource reference. Set "capture_portraits" to false in config.json to disable it.

INSTALLATION - REUSABLE KIT
1. Follow steps 1 through 5 above.
2. On the tracker Game Clock page, press Create or replace private clock link.
3. Download the generated private config.json. Do not rename it.
4. Put config.json beside the .ts4script and relay files. Delete or leave config-template.json; the mod reads config.json only.
5. Follow steps 6 through 9 above.

UPDATING CLOCK SYNC
1. Close The Sims 4.
2. Download the newest reusable complete kit.
3. Extract it and run Install or Update SeveralUDO Clock Sync.bat. The old folder is backed up, the private config is preserved unless the new kit contains a replacement, and exact-name duplicate Script Mods are moved into the backup.
4. Run Test SeveralUDO Clock Sync.bat, then start the relay and restart the game.
5. Verify the paired slot, ordered report number and Last game day on the tracker.

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
3. The hosted tracker may be closed or temporarily unreachable. The relay keeps reports in order locally until it reconnects. The desktop tracker must be running for its local receiver to accept reports.
4. Review Automation Inbox before accepting uncertain changes.

Clock Sync reports game data; it does not install packs, change game saves, or bypass missing optional mods.

OPTIONAL HEALTHCARE REDUX SUPPORT
- Healthcare Redux is never required and Clock Sync never imports its Python modules.
- When Healthcare Redux is installed, Clock Sync reads active disease buffs and diagnosed traits through the normal Sims trackers. Supported names include Influenza, Cold, Ear Infection, Gastroenteritis, Bronchitis, Sinusitis, Pneumonia, Malaria, Meningitis, Tonsillitis, Tuberculosis, urinary tract and yeast infections, plus supported chronic conditions.
- Immunity, vaccination, recent-illness, medication, treatment, removal, broadcaster and testing markers are ignored so they do not create false illness episodes.
- When Healthcare Redux is absent, these optional checks return no matches and all other Clock Sync features continue normally.
