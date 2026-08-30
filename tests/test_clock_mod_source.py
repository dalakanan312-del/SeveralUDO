import importlib.util
import sys
import types
import unittest
import tempfile
import json
from zipfile import ZipFile
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "clock_bridge" / "mod_source" / "severaludo_clock_sync" / "__init__.py"
ARCHIVE = Path(__file__).parents[1] / "clock_bridge" / "SeveralUDOClockSync.ts4script"
BUILD_SCRIPT = Path(__file__).parents[1] / "clock_bridge" / "build_clock_sync.ps1"


class ClockModSourceTests(unittest.TestCase):
    def load_module(self):
        package_name = "clock_mod_source_test"
        core = types.SimpleNamespace(VERSION="old", _household_snapshot=lambda: ("", []), _send_payload=lambda *args: 200)

        def value_or_call(owner, name, default=None):
            try:
                value = getattr(owner, name, default)
                return value() if callable(value) else value
            except Exception:
                return default

        core._value_or_call = value_or_call
        core._tuning_text = lambda value: str(getattr(value, "__name__", value) or "")
        compat = types.ModuleType(package_name + ".compat_201")
        compat._core = core
        compat.VERSION = "2.0.1"
        compat._extended_snapshot = lambda sim_info, household: {"traits": []}
        sys.modules[compat.__name__] = compat
        spec = importlib.util.spec_from_file_location(
            package_name, SOURCE, submodule_search_locations=[str(SOURCE.parent)]
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = module
        spec.loader.exec_module(module)
        self.addCleanup(sys.modules.pop, package_name, None)
        self.addCleanup(sys.modules.pop, compat.__name__, None)
        return module

    def test_current_skill_and_milestone_apis_are_reported(self):
        module = self.load_module()

        class Skill_Logic:
            is_skill = True
            guid64 = 12345

        class SkillStat:
            stat_type = Skill_Logic

            def get_user_value(self):
                return 7

        class Milestone_First_Steps:
            pass

        class MilestoneTracker:
            def get_all_completed_milestones(self):
                return (Milestone_First_Steps,)

        sim = types.SimpleNamespace(
            all_skills=(Skill_Logic,),
            get_statistic=lambda tuning: SkillStat() if tuning is Skill_Logic else None,
            developmental_milestone_tracker=MilestoneTracker(),
        )
        result = module._extended_snapshot(sim, None)
        self.assertEqual(result["skills"], [{"name": "Logic", "level": 7, "tuning_id": "12345"}])
        self.assertEqual(result["milestones"], ["First Steps"])
        self.assertTrue(result["skills_scan_supported"])
        self.assertTrue(result["milestone_scan_supported"])
        self.assertEqual(result["telemetry_version"], 6)
        self.assertEqual(result["stable_tuning_ids"]["skills"]["Logic"], "12345")

    def test_scandal_evidence_and_inventory_are_guarded_telemetry(self):
        module = self.load_module()

        class RelationshipBit_Caught_Cheating:
            pass

        class Object_Family_Portrait:
            guid64 = 44001

        spouse = types.SimpleNamespace(sim_id=33, first_name="Robin", last_name="Doe", gender="Male", age="Adult")
        relationship_tracker = types.SimpleNamespace(
            get_target_sim_infos=lambda: (spouse,),
            get_all_bits=lambda target: (RelationshipBit_Caught_Cheating,),
            get_friendship_score=lambda target: -20,
            get_romance_score=lambda target: 40,
        )
        portrait = types.SimpleNamespace(definition=Object_Family_Portrait, stack_count=1, current_value=750)
        sim = types.SimpleNamespace(
            relationship_tracker=relationship_tracker,
            inventory_component=types.SimpleNamespace(items=(portrait,)),
        )
        result = module._extended_snapshot(sim, None)
        self.assertEqual(result["relationships"][0]["scandal_signals"][0]["type"], "infidelity")
        self.assertTrue(result["relationships"][0]["scandal_signals"][0]["review_required"])
        self.assertTrue(result["inventory_scan_supported"])
        self.assertEqual(result["inventory_items"][0]["definition_id"], "44001")
        self.assertEqual(result["inventory_items"][0]["name"], "Family Portrait")

    def test_guarded_v4_snapshot_reports_selected_life_history_modules(self):
        module = self.load_module()

        class RelationshipBit_Spouse:
            pass

        class Buff_Malaria:
            pass

        class adeepindigo_HealthcareRedux_Diseases_FluBuff:
            guid64 = 88001

        class adeepindigo_HealthcareRedux_Diseases_buff_RecentFlu:
            pass

        class adeepindigo_HealthcareRedux_Diseases_FluTrait_Diagnosed:
            guid64 = 88002

        class adeepindigo_HealthcareRedux_Diseases_FluImmuneTrait:
            pass

        class Career_Apothecary:
            pass

        class Degree_History:
            pass

        class Aspiration_Successful_Lineage:
            pass

        class Trait_Lifestyle_Close_Knit:
            pass

        child = types.SimpleNamespace(sim_id=22, first_name="Jane", last_name="Doe", gender="Female", age="Child")
        spouse = types.SimpleNamespace(sim_id=33, first_name="Robin", last_name="Doe", gender="Male", age="Adult")
        genealogy = types.SimpleNamespace(get_children=lambda: (child,))
        relationship_tracker = types.SimpleNamespace(
            get_target_sim_infos=lambda: (spouse,),
            get_all_bits=lambda target: (RelationshipBit_Spouse,),
            get_friendship_score=lambda target: 82,
            get_romance_score=lambda target: 91,
        )
        health_buff = types.SimpleNamespace(buff_type=Buff_Malaria, severity="Severe", remaining_minutes=120)
        influenza_buff = types.SimpleNamespace(buff_type=adeepindigo_HealthcareRedux_Diseases_FluBuff, severity="Severe", remaining_minutes=240)
        recent_influenza = types.SimpleNamespace(buff_type=adeepindigo_HealthcareRedux_Diseases_buff_RecentFlu)
        career = types.SimpleNamespace(career_tuning=Career_Apothecary, level=4, performance=77)
        sim = types.SimpleNamespace(
            sim_id=11, first_name="Anne", last_name="Doe", gender="Female", age="Adult",
            pregnancy_tracker=types.SimpleNamespace(
                pregnancy_stage="Third Trimester", pregnancy_progress=.75,
                hours_remaining=18, is_in_labor=True,
                expected_offspring_count=2,
            ),
            genealogy=genealogy,
            relationship_tracker=relationship_tracker,
            buff_component=types.SimpleNamespace(get_all_buffs=lambda: (health_buff, influenza_buff, recent_influenza)),
            age_in_days=103, age_progress_percentage=.75, days_until_age_up=6,
            career_tracker=types.SimpleNamespace(careers={1: career}),
            degree_tracker=types.SimpleNamespace(degrees=(Degree_History,)),
            occult_tracker=types.SimpleNamespace(occult_rank="Master", unlocked_perks=()),
            aspiration_tracker=types.SimpleNamespace(
                active_aspiration=Aspiration_Successful_Lineage,
                completed_aspirations=(),
            ),
            trait_tracker=types.SimpleNamespace(
                equipped_traits=(Trait_Lifestyle_Close_Knit,),
                traits=(
                adeepindigo_HealthcareRedux_Diseases_FluTrait_Diagnosed,
                adeepindigo_HealthcareRedux_Diseases_FluImmuneTrait,
                ),
            ),
            portrait_bytes=b"portrait" * 20,
        )
        result = module._extended_snapshot(sim, None)
        self.assertEqual(result["telemetry_version"], 6)
        self.assertEqual(result["clock_sync_version"], "2.2.8")
        self.assertEqual(result["child_game_sim_ids"], ["22"])
        self.assertEqual(result["relationships"][0]["category"], "Marriage")
        self.assertEqual(result["babies_expected"], 2)
        self.assertEqual(result["pregnancy_progress_percentage"], 75)
        self.assertTrue(result["is_in_labor"])
        self.assertEqual(result["careers"][0]["name"], "Apothecary")
        self.assertIn("History", result["degrees"])
        self.assertEqual(result["occult_progress"]["rank"], "Master")
        self.assertIn("Successful Lineage", result["aspirations"])
        self.assertTrue(result["illness_scan_supported"])
        self.assertEqual({item["name"] for item in result["illnesses"]}, {"Malaria", "Influenza"})
        influenza = next(item for item in result["health_buffs"] if item["name"] == "Influenza")
        self.assertEqual(influenza["provider"], "Healthcare Redux")
        self.assertEqual(influenza["source_kind"], "trait+active_buff")
        self.assertNotIn("Recent Flu", {item["raw_name"] for item in result["health_buffs"]})
        self.assertTrue(result["healthcare_redux_detected"])
        self.assertEqual(result["game_portrait"]["capture_mode"], "embedded")
        self.assertTrue(all(result["telemetry_capabilities"].get(name) for name in (
            "pregnancy", "genealogy", "relationships", "health", "life_stage",
            "career_education", "occult_progress", "personal_development", "portraits",
        )))
        self.assertTrue(result["clock_sync_diagnostics"]["healthy"])

    def test_current_game_buffs_property_reports_hidden_healthcare_redux_disease(self):
        module = self.load_module()

        class adeepindigo_HealthcareRedux_Diseases_MalariaBuff:
            guid64 = 10936279956231109735

        class adeepindigo_HealthcareRedux_Diseases_MalariaImmuneTrait:
            guid64 = 4030309405

        component = types.SimpleNamespace(
            get_active_buff_types=lambda: (adeepindigo_HealthcareRedux_Diseases_MalariaBuff,),
            _active_buffs={},
        )
        sim = types.SimpleNamespace(
            Buffs=component,
            trait_tracker=types.SimpleNamespace(
                traits=(adeepindigo_HealthcareRedux_Diseases_MalariaImmuneTrait,),
            ),
        )
        result = module._extended_snapshot(sim, None)
        self.assertEqual([item["name"] for item in result["illnesses"]], ["Malaria"])
        self.assertEqual(result["health_buffs"][0]["tuning_id"], "10936279956231109735")
        self.assertEqual(result["health_buffs"][0]["source_kind"], "active_buff")
        self.assertNotIn("Malaria Immune Trait", result["symptoms"])

    def test_responsible_pregnancy_states_are_separate_from_illnesses(self):
        module = self.load_module()

        class Kemzima_ResponsiblePregnancy_NewbornComplications_Buff_LowBirthWeight:
            guid64 = 14778631640759100390

        class Kemzima_ResponsiblePregnancy_CatLitter_Buff_ToxoplasmosisInfection_Stage2:
            guid64 = 13283056988740328786

        class Kemzima_ResponsiblePregnancy_Foundation_BirthComplications_Buff_Enabled:
            guid64 = 17836605024682867054

        component = types.SimpleNamespace(get_active_buff_types=lambda: (
            Kemzima_ResponsiblePregnancy_NewbornComplications_Buff_LowBirthWeight,
            Kemzima_ResponsiblePregnancy_CatLitter_Buff_ToxoplasmosisInfection_Stage2,
            Kemzima_ResponsiblePregnancy_Foundation_BirthComplications_Buff_Enabled,
        ))
        sim = types.SimpleNamespace(Buffs=component, trait_tracker=types.SimpleNamespace(traits=()))
        result = module._extended_snapshot(sim, None)
        states = {row["key"]: row for row in result["responsible_pregnancy_states"]}
        self.assertEqual(set(states), {"low-birth-weight", "toxoplasmosis"})
        self.assertEqual(states["toxoplasmosis"]["name"], "Toxoplasmosis infection — stage 2")
        self.assertEqual(states["low-birth-weight"]["category"], "Newborn complication")
        self.assertTrue(result["responsible_pregnancy_detected"])
        self.assertTrue(result["responsible_pregnancy_scan_supported"])
        self.assertNotIn("Low birth weight", {row["name"] for row in result["illnesses"]})

    def test_healthcare_redux_deadly_disease_stage_is_reported_without_guessing(self):
        module = self.load_module()

        class adeepindigo_HealthcareRedux_Diseases_DeadlyDiseaseCommodity_Stage2Buff:
            guid64 = 112233

        class adeepindigo_HealthcareRedux_Core_HasIllness:
            guid64 = 445566

        component = types.SimpleNamespace(
            get_active_buff_types=lambda: (
                adeepindigo_HealthcareRedux_Diseases_DeadlyDiseaseCommodity_Stage2Buff,
                adeepindigo_HealthcareRedux_Core_HasIllness,
            ),
        )
        sim = types.SimpleNamespace(Buffs=component, trait_tracker=types.SimpleNamespace(traits=()))
        result = module._extended_snapshot(sim, None)
        self.assertEqual([item["name"] for item in result["illnesses"]], ["Deadly Disease — diagnosis pending"])
        self.assertEqual(result["symptoms"], ["Deadly Disease — diagnosis pending"])

    def test_exact_healthcare_redux_disease_replaces_pending_stage(self):
        module = self.load_module()

        class adeepindigo_HealthcareRedux_Diseases_DeadlyDiseaseCommodity_Stage1Buff:
            pass

        class adeepindigo_HealthcareRedux_Diseases_TuberculosisBuff:
            guid64 = 17581724841935921134

        component = types.SimpleNamespace(
            get_active_buff_types=lambda: (
                adeepindigo_HealthcareRedux_Diseases_DeadlyDiseaseCommodity_Stage1Buff,
                adeepindigo_HealthcareRedux_Diseases_TuberculosisBuff,
            ),
        )
        sim = types.SimpleNamespace(Buffs=component, trait_tracker=types.SimpleNamespace(traits=()))
        result = module._extended_snapshot(sim, None)
        self.assertEqual([item["name"] for item in result["illnesses"]], ["Tuberculosis"])

    def test_protocol_reports_are_checksummed_delta_encoded_and_queued_in_order(self):
        module = self.load_module()

        class FamilyFunds:
            def __init__(self, money):
                self.money = money

        with tempfile.TemporaryDirectory() as folder:
            module._core._config_path = lambda: str(Path(folder) / "config.json")
            replacements = []
            real_replace = module.os.replace
            module.os.replace = lambda source, destination: (
                replacements.append((str(source), str(destination))),
                real_replace(source, destination),
            )[-1]
            member = {"game_sim_id":"101", "first_name":"Ada", "skills":[], "household_funds":FamilyFunds(12345)}
            config = {"save_identity":"slot-test", "receiver_url":"https://example.invalid/api/clock/report", "sync_token":"secret"}
            households = [{"game_household_id":"h-1", "name":"Test House", "funds":FamilyFunds(12345)}]
            first = module._protocol_report(10, 8, 15, "Test House", [member], True, households, True, config)
            second = module._protocol_report(10, 8, 16, "Test House", [member], False, [], False, config)
            self.assertEqual((first["report_kind"], second["report_kind"]), ("full", "delta"))
            self.assertEqual(first["household_sims"][0]["household_funds"], 12345)
            self.assertEqual(first["population_households"][0]["funds"], 12345)
            self.assertEqual(module._funds_amount(types.SimpleNamespace(funds=FamilyFunds(77))), 77)
            self.assertEqual(second["household_sims"], [])
            self.assertEqual(second["report_sequence"], first["report_sequence"] + 1)
            self.assertEqual(second["previous_report_checksum"], first["report_checksum"])
            self.assertEqual(first["report_checksum"], module._canonical_checksum(first))
            payload = json.dumps(second, separators=(",", ":")).encode("utf-8")
            module._send_payload_v22(config, payload)
            queued = sorted((Path(folder) / "report_queue").glob("report-*.json"))
            self.assertEqual(len(queued), 1)
            envelope = json.loads(queued[0].read_text())
            self.assertEqual(envelope["report_sequence"], second["report_sequence"])
            self.assertEqual(envelope["payload"]["report_checksum"], second["report_checksum"])
            self.assertEqual(json.loads(envelope["payload_json"]), second)
            self.assertTrue(replacements)
            self.assertTrue(all(source.lower().endswith(".json") for source, _ in replacements))
            self.assertFalse(any(source.lower().endswith(".tmp") for source, _ in replacements))

    def test_redirected_documents_config_and_manual_report_are_supported(self):
        module = self.load_module()
        with tempfile.TemporaryDirectory() as folder:
            installed = Path(folder) / "OneDrive" / "Documents" / "Electronic Arts" / "The Sims 4" / "Mods" / "SeveralUDOClockSync"
            installed.mkdir(parents=True)
            config = installed / "config.json"
            config.write_text('{"enabled":true}', encoding="utf-8")
            module.__file__ = str(installed / "SeveralUDOClockSync.ts4script" / "severaludo_clock_sync" / "__init__.pyc")
            self.assertEqual(Path(module._config_path_v224()), config)
        provided = {"save_identity": "slot-manual"}
        captured = {}
        module._core._absolute_game_day = lambda: 17
        module._core._game_clock = lambda: (15, 4)
        module._played_population_snapshot = lambda: ("Hawthorn", [], True, [])
        module._protocol_report = lambda *args: captured.setdefault("report", {
            "game_day": args[0], "config": args[-1],
        })
        result = module._report_payload_v22(provided)
        self.assertEqual(result[:5], (17, 15, 4, "Hawthorn", []))
        self.assertIs(captured["report"]["config"], provided)
        self.assertEqual(json.loads(result[-1]), {"game_day": 17, "config": provided})

    def test_published_archive_keeps_real_compatibility_module(self):
        with ZipFile(ARCHIVE) as archive:
            names = {name.replace("\\", "/") for name in archive.namelist()}
            self.assertIn("severaludo_clock_sync/__init__.pyc", names)
            self.assertIn("severaludo_clock_sync/compat_201.pyc", names)
            self.assertIn("severaludo_clock_sync/core.pyc", names)
            wrapper = archive.read("severaludo_clock_sync/__init__.pyc")
            compatibility = archive.read("severaludo_clock_sync/compat_201.pyc")
        self.assertIn(b"compat_201", wrapper)
        self.assertNotIn(b"compat_201", compatibility)
        self.assertIn(b"core", compatibility)
        self.assertNotEqual(wrapper, compatibility)

    def test_packager_preserves_compatibility_entry_on_rebuild(self):
        script = BUILD_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"severaludo_clock_sync/compat_201.pyc"', script)
        self.assertIn("compatibility module is a recursive wrapper", script)
        self.assertNotIn('EndsWith("/__init__.pyc")', script)


if __name__ == "__main__":
    unittest.main()
