// SeveralUDO Sims 3 Clock Sync 1.1.0
//
// The package reads the loaded Sims 3 town and writes a local snapshot.
// It never changes a .sims3 save and it never performs network traffic. The
// separate user-started relay sends the snapshot to the private tracker link.

using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
using Sims3.Gameplay.Utilities;
using Sims3.Gameplay.Core;
using Sims3.Gameplay.CAS;
using Sims3.Gameplay.Actors;
using Sims3.Gameplay.ActorSystems;
using Sims3.Gameplay.Socializing;
using Sims3.Gameplay.Skills;
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
        private const int TelemetryVersion = 1;

        static ClockSync()
        {
            World.OnWorldLoadFinishedEventHandler += new EventHandler(OnWorldLoadFinished);
        }

        private static void OnWorldLoadFinished(object sender, EventArgs e)
        {
            AlarmManager.Global.AddAlarm(1f, TimeUnit.Seconds, new AlarmTimerCallback(StartReporting), "SeveralUDO Sims 3 Clock Sync start", AlarmType.NeverPersisted, null);
        }

        private static void StartReporting()
        {
            WriteGameSnapshot();
            AlarmManager.Global.AddAlarmRepeating(5f, TimeUnit.Minutes, new AlarmTimerCallback(WriteGameSnapshot), 30f, TimeUnit.Minutes, "SeveralUDO Sims 3 Clock Sync", AlarmType.NeverPersisted, null);
        }

        private static void WriteGameSnapshot()
        {
            try
            {
                float clockHour = SimClock.CurrentTime().Hour;
                int hour = (int)Math.Floor(clockHour);
                int minute = (int)Math.Floor((clockHour - hour) * 60f + 0.01f);
                if (minute >= 60) { hour += 1; minute = 0; }
                hour = Math.Max(0, Math.Min(23, hour));
                minute = Math.Max(0, Math.Min(59, minute));
                int gameDay = Math.Max(1, (int)Math.Floor(SimClock.ElapsedTime(TimeUnit.Days)) + 1);

                string documents = System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments);
                string bridge = Path.Combine(documents, @"Electronic Arts\The Sims 3\Mods\" + BridgeFolder);
                Directory.CreateDirectory(bridge);
                string snapshot = Path.Combine(bridge, SnapshotName);
                string temporary = snapshot + ".tmp";
                File.WriteAllText(temporary, BuildSnapshotJson(gameDay, hour, minute), new UTF8Encoding(false));
                if (File.Exists(snapshot)) File.Delete(snapshot);
                File.Move(temporary, snapshot);
            }
            catch
            {
                // The reporter must never interrupt the game. A temporary
                // data or file-system failure simply retries on the next check.
            }
        }

        private static string BuildSnapshotJson(int gameDay, int hour, int minute)
        {
            // Whole-town mode intentionally scans every Sims 3 SimDescription.
            // The generated report is authoritative for this save's town and
            // not limited to whichever household is currently selected.
            List<SimDescription> members = new List<SimDescription>(Household.EverySimDescription());
            Household activeHousehold = Household.ActiveHousehold;
            string activeHouseholdId = activeHousehold == null ? "" : activeHousehold.HouseholdId.ToString();
            string activeHouseholdName = activeHousehold == null ? "" : activeHousehold.Name;
            int activeHouseholdFunds = activeHousehold == null ? 0 : activeHousehold.FamilyFunds;

            StringBuilder json = new StringBuilder();
            json.Append("{\"schema\":2,\"source\":\"SeveralUDO Sims 3 Clock Sync\",\"clock_sync_version\":\"1.1.0\",\"game_edition\":\"sims3\",\"telemetry_version\":");
            json.Append(TelemetryVersion);
            json.Append(",\"game_day\":").Append(gameDay);
            json.Append(",\"hour\":").Append(hour);
            json.Append(",\"minute\":").Append(minute);
            json.Append(",\"population_complete\":true,\"population_scope\":\"town\",\"telemetry_capabilities\":{");
            json.Append("\"clock\":true,\"town_population\":true,\"households\":true,\"sims\":true,\"life_stage\":true,\"genealogy\":true,\"pregnancy\":true,\"traits\":true,\"skills\":true,\"careers\":true,\"relationships\":true,\"occults\":true,\"deaths\":true,\"illnesses\":false,\"portraits\":false,\"milestones\":false}");

            List<string> memberIds = new List<string>();
            foreach (SimDescription member in members)
            {
                if (member != null) memberIds.Add(member.SimDescriptionId.ToString());
            }

            json.Append(",\"household_id\":"); AppendJsonString(json, activeHouseholdId);
            json.Append(",\"household_name\":"); AppendJsonString(json, activeHouseholdName);
            json.Append(",\"household_funds\":").Append(activeHouseholdFunds);
            json.Append(",\"population_sim_ids\":"); AppendStringArray(json, memberIds);
            json.Append(",\"town_population_count\":").Append(memberIds.Count);
            json.Append(",\"household_members\":[");
            bool first = true;
            foreach (SimDescription member in members)
            {
                if (member == null) continue;
                if (!first) json.Append(",");
                first = false;
                AppendSimJson(json, member);
            }
            json.Append("],\"snapshot_signature\":");
            AppendJsonString(json, SnapshotSignature(gameDay, hour, minute, members));
            json.Append("}");
            return json.ToString();
        }

        private static void AppendSimJson(StringBuilder json, SimDescription sim)
        {
            Household household = sim.Household;
            string householdId = household == null ? "" : household.HouseholdId.ToString();
            string householdName = household == null ? "" : household.Name;
            int householdFunds = household == null ? 0 : household.FamilyFunds;
            List<string> householdMemberIds = new List<string>();
            if (household != null)
            {
                foreach (SimDescription member in household.AllSimDescriptions)
                {
                    if (member != null) householdMemberIds.Add(member.SimDescriptionId.ToString());
                }
            }

            string id = sim.SimDescriptionId.ToString();
            json.Append("{\"game_sim_id\":"); AppendJsonString(json, id);
            json.Append(",\"first_name\":"); AppendJsonString(json, sim.FirstName);
            json.Append(",\"last_name\":"); AppendJsonString(json, sim.LastName);
            json.Append(",\"sex\":"); AppendJsonString(json, sim.IsFemale ? "Female" : "Male");
            json.Append(",\"life_stage\":"); AppendJsonString(json, sim.Age.ToString());
            json.Append(",\"is_baby\":").Append(sim.Baby ? "true" : "false");
            json.Append(",\"household_id\":"); AppendJsonString(json, householdId);
            json.Append(",\"household_name\":"); AppendJsonString(json, householdName);
            json.Append(",\"household_funds\":").Append(householdFunds);
            json.Append(",\"is_household_head\":false");
            json.Append(",\"household_is_player\":").Append(household != null && household.IsActive ? "true" : "false");
            json.Append(",\"household_is_unplayed\":").Append(household == null || !household.IsActive ? "true" : "false");
            json.Append(",\"household_member_game_ids\":"); AppendStringArray(json, householdMemberIds);
            json.Append(",\"is_dead\":").Append(sim.IsDead ? "true" : "false");
            json.Append(",\"death_type\":"); AppendJsonString(json, sim.DeathStyle.ToString());

            SimDescription partner = sim.Partner;
            if (partner != null)
            {
                json.Append(",\"significant_other_game_id\":"); AppendJsonString(json, partner.SimDescriptionId.ToString());
            }

            AppendGenealogyFields(json, sim);
            AppendPregnancyFields(json, sim);
            AppendTraitFields(json, sim);
            AppendSkillFields(json, sim);
            AppendCareerFields(json, sim);
            AppendOccultFields(json, sim);
            AppendRelationshipFields(json, sim);
            // Sims 3 exposes ordinary moodlets but no dependable illness API
            // that can identify medical mods. Do not mistake cold, clothing,
            // or mood buffs for a disease.
            json.Append(",\"health_scan_supported\":false,\"health_buffs\":[],\"symptoms\":[]}");
        }

        private static void AppendGenealogyFields(StringBuilder json, SimDescription sim)
        {
            List<string> parents = new List<string>();
            List<string> children = new List<string>();
            List<string> siblings = new List<string>();
            if (sim.Genealogy != null)
            {
                CollectGenealogyIds(sim.Genealogy.Parents, parents);
                CollectGenealogyIds(sim.Genealogy.Children, children);
            }
            json.Append(",\"parent_game_sim_ids\":"); AppendStringArray(json, parents);
            json.Append(",\"child_game_sim_ids\":"); AppendStringArray(json, children);
            json.Append(",\"sibling_game_sim_ids\":"); AppendStringArray(json, siblings);
        }

        private static void CollectGenealogyIds(IEnumerable<Genealogy> entries, List<string> values)
        {
            if (entries == null) return;
            foreach (Genealogy entry in entries)
            {
                if (entry == null || entry.SimDescription == null) continue;
                string id = entry.SimDescription.SimDescriptionId.ToString();
                if (!values.Contains(id)) values.Add(id);
            }
        }

        private static void AppendPregnancyFields(StringBuilder json, SimDescription sim)
        {
            Pregnancy pregnancy = sim.Pregnancy;
            if (pregnancy == null) { json.Append(",\"is_pregnant\":false"); return; }
            json.Append(",\"is_pregnant\":true");
            if (pregnancy.DadDescriptionId != 0)
            {
                json.Append(",\"pregnancy_partner_game_sim_id\":"); AppendJsonString(json, pregnancy.DadDescriptionId.ToString());
                json.Append(",\"other_parent_game_sim_id\":"); AppendJsonString(json, pregnancy.DadDescriptionId.ToString());
            }
            // Pregnancy length can be changed by other Sims 3 mods, so only
            // send the state we can certify rather than inventing a percentage.
            json.Append(",\"pregnancy_progress_supported\":false");
        }

        private static void AppendTraitFields(StringBuilder json, SimDescription sim)
        {
            json.Append(",\"traits\":[");
            bool first = true;
            if (sim.TraitManager != null)
            {
                foreach (Trait trait in sim.TraitManager.List)
                {
                    if (trait == null || !trait.IsVisible) continue;
                    if (!first) json.Append(",");
                    first = false;
                    json.Append("{\"name\":"); AppendJsonString(json, trait.TraitName(sim.IsFemale));
                    json.Append(",\"id\":"); AppendJsonString(json, trait.Guid.ToString());
                    json.Append("}");
                }
            }
            json.Append("]");
        }

        private static void AppendSkillFields(StringBuilder json, SimDescription sim)
        {
            json.Append(",\"skills\":[");
            bool first = true;
            if (sim.SkillManager != null)
            {
                foreach (Skill skill in sim.SkillManager.List)
                {
                    if (skill == null) continue;
                    if (!first) json.Append(",");
                    first = false;
                    json.Append("{\"name\":"); AppendJsonString(json, skill.Name);
                    json.Append(",\"id\":"); AppendJsonString(json, skill.Guid.ToString());
                    json.Append(",\"level\":").Append(skill.SkillLevel).Append("}");
                }
            }
            json.Append("]");
        }

        private static void AppendCareerFields(StringBuilder json, SimDescription sim)
        {
            if (sim.Occupation == null) return;
            json.Append(",\"career\":"); AppendJsonString(json, sim.Occupation.CareerName);
            json.Append(",\"careers\":[{\"name\":"); AppendJsonString(json, sim.Occupation.CareerName);
            json.Append(",\"level\":").Append(sim.Occupation.CareerLevel);
            json.Append(",\"id\":"); AppendJsonString(json, sim.Occupation.Guid.ToString());
            json.Append("}]");
        }

        private static void AppendOccultFields(StringBuilder json, SimDescription sim)
        {
            if (sim.OccultManager == null || sim.OccultManager.CurrentOccultTypes.ToString() == "None") return;
            string[] parts = sim.OccultManager.CurrentOccultTypes.ToString().Split(new char[] { ',' });
            json.Append(",\"occult_types\":[");
            bool first = true;
            foreach (string part in parts)
            {
                string item = part.Trim();
                if (item.Length == 0 || item == "None") continue;
                if (!first) json.Append(",");
                first = false;
                AppendJsonString(json, item);
            }
            json.Append("]");
        }

        private static void AppendRelationshipFields(StringBuilder json, SimDescription sim)
        {
            json.Append(",\"relationships\":[");
            bool first = true;
            try
            {
                List<Relationship> relationships = new List<Relationship>(Relationship.GetRelationships(sim));
                foreach (Relationship relationship in relationships)
                {
                    if (relationship == null) continue;
                    SimDescription other = relationship.GetOtherSimDescription(sim);
                    if (other == null || other.SimDescriptionId == sim.SimDescriptionId) continue;
                    float friendship = relationship.LTR == null ? 0f : relationship.LTR.Liking;
                    bool romantic = relationship.AreRomantic();
                    bool isPartner = sim.Partner != null && sim.Partner.SimDescriptionId == other.SimDescriptionId;
                    // Report romance, partners, and strong friendships/enmities;
                    // omit casual acquaintances to keep automation review useful.
                    if (!romantic && !isPartner && Math.Abs(friendship) < 50f) continue;
                    if (!first) json.Append(",");
                    first = false;
                    json.Append("{\"other_game_sim_id\":"); AppendJsonString(json, other.SimDescriptionId.ToString());
                    json.Append(",\"other_name\":"); AppendJsonString(json, other.FirstName + " " + other.LastName);
                    json.Append(",\"category\":");
                    if (isPartner) AppendJsonString(json, "Partner");
                    else if (romantic) AppendJsonString(json, "Romantic");
                    else AppendJsonString(json, friendship >= 50f ? "Friendship" : "Enemy");
                    json.Append(",\"friendship_score\":").Append(friendship.ToString(System.Globalization.CultureInfo.InvariantCulture));
                    json.Append(",\"romance_score\":").Append(romantic ? "1" : "0").Append("}");
                }
            }
            catch
            {
                // Relationship data varies with game expansions. Leave the
                // field empty rather than risking a game exception.
            }
            json.Append("]");
        }

        private static string SnapshotSignature(int gameDay, int hour, int minute, List<SimDescription> members)
        {
            StringBuilder result = new StringBuilder();
            result.Append(gameDay).Append("/").Append(hour).Append("/").Append(minute);
            foreach (SimDescription sim in members)
            {
                if (sim == null) continue;
                result.Append("|").Append(sim.SimDescriptionId);
                result.Append(":").Append(sim.IsDead ? "D" : "L");
                result.Append(":").Append(sim.Age);
                result.Append(":").Append(sim.Pregnancy == null ? "N" : "P");
                if (sim.Partner != null) result.Append(":").Append(sim.Partner.SimDescriptionId);
            }
            return result.ToString();
        }

        private static void AppendStringArray(StringBuilder json, IEnumerable<string> values)
        {
            json.Append("[");
            bool first = true;
            if (values != null)
            {
                foreach (string value in values)
                {
                    if (string.IsNullOrEmpty(value)) continue;
                    if (!first) json.Append(",");
                    first = false;
                    AppendJsonString(json, value);
                }
            }
            json.Append("]");
        }

        private static void AppendJsonString(StringBuilder json, string value)
        {
            json.Append("\"");
            if (value != null)
            {
                foreach (char character in value)
                {
                    if (character == '\\' || character == '"') json.Append("\\").Append(character);
                    else if (character == '\n') json.Append("\\n");
                    else if (character == '\r') json.Append("\\r");
                    else if (character == '\t') json.Append("\\t");
                    else if (character < 32) json.Append("\\u").Append(((int)character).ToString("x4"));
                    else json.Append(character);
                }
            }
            json.Append("\"");
        }
    }
}
