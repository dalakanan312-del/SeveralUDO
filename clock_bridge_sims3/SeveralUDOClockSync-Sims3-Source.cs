// SeveralUDO Sims 3 Clock Sync 1.0.0
//
// This pure script mod reads only the active save's game clock. It never
// changes a .sims3 save and it does not send network traffic from the game.
// It writes a small local snapshot which the separate, user-started relay
// turns into the private tracker report.

using System;
using System.IO;
using System.Text;
using Sims3.Gameplay.Utilities;
using Sims3.SimIFace;

[assembly: Tunable]

namespace SeveralUDO.Sims3
{
    public class ClockSync
    {
        [Tunable]
        protected static bool kInstantiator = false;

        private const string BridgeFolder = "SeveralUDOClockSync";
        private const string SnapshotName = "sims3_game_clock.json";

        static ClockSync()
        {
            World.OnWorldLoadFinishedEventHandler += new EventHandler(OnWorldLoadFinished);
        }

        private static void OnWorldLoadFinished(object sender, EventArgs e)
        {
            // The world clock is not always ready in the first callback.
            AlarmManager.Global.AddAlarm(
                1f,
                TimeUnit.Seconds,
                new AlarmTimerCallback(StartReporting),
                "SeveralUDO Sims 3 Clock Sync start",
                AlarmType.NeverPersisted,
                null
            );
        }

        private static void StartReporting()
        {
            WriteClockSnapshot();
            AlarmManager.Global.AddAlarmRepeating(
                5f,
                TimeUnit.Minutes,
                new AlarmTimerCallback(WriteClockSnapshot),
                10f,
                TimeUnit.Minutes,
                "SeveralUDO Sims 3 Clock Sync",
                AlarmType.NeverPersisted,
                null
            );
        }

        private static void WriteClockSnapshot()
        {
            try
            {
                float clockHour = SimClock.CurrentTime().Hour;
                int hour = (int)Math.Floor(clockHour);
                int minute = (int)Math.Floor((clockHour - hour) * 60f + 0.01f);
                if (minute >= 60)
                {
                    hour += 1;
                    minute = 0;
                }
                hour = Math.Max(0, Math.Min(23, hour));
                minute = Math.Max(0, Math.Min(59, minute));
                int gameDay = Math.Max(1, (int)Math.Floor(SimClock.ElapsedTime(TimeUnit.Days)) + 1);

                string documents = System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments);
                string bridge = Path.Combine(documents, @"Electronic Arts\The Sims 3\Mods\" + BridgeFolder);
                Directory.CreateDirectory(bridge);
                string snapshot = Path.Combine(bridge, SnapshotName);
                string temporary = snapshot + ".tmp";
                string json = "{\"schema\":1,\"source\":\"SeveralUDO Sims 3 Clock Sync\",\"game_edition\":\"sims3\",\"game_day\":"
                    + gameDay + ",\"hour\":" + hour + ",\"minute\":" + minute + "}";

                File.WriteAllText(temporary, json, new UTF8Encoding(false));
                if (File.Exists(snapshot)) File.Delete(snapshot);
                File.Move(temporary, snapshot);
            }
            catch
            {
                // A clock reporter must never interrupt the game for a
                // temporary file-system issue. The next scheduled check tries
                // again, and the relay will retain the last valid snapshot.
            }
        }
    }
}
