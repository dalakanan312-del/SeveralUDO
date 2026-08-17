SeveralUDO Automatic Game Clock
===============================

1. In the tracker, open Game Clock Sync and create a private link.
2. Download both the .ts4script file and config.json.
3. Create this folder if it does not exist:
   Documents\Electronic Arts\The Sims 4\Mods\SeveralUDOClockSync
4. Put both downloaded files directly inside that folder.
5. In The Sims 4, enable Custom Content and Mods and Script Mods Allowed.
6. Restart the game and load the household you want to track.

The first report anchors the current in-game day to the tracker's current
Global Day. Each later in-game day advances the tracker once. Restoring an
older Sims save never rewinds the tracker automatically.

If the automatic first report does not appear, open the cheat console and use:
  severaludo.clock.status
  severaludo.clock.report
The second command sends immediately and prints either the accepted HTTP status
or the exact error returned by the tracker.

Keep config.json private. Use Revoke link in the tracker if it is shared.
