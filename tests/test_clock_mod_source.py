import importlib.util
import sys
import types
import unittest
from zipfile import ZipFile
from pathlib import Path


SOURCE = Path(__file__).parents[1] / "clock_bridge" / "mod_source" / "severaludo_clock_sync" / "__init__.py"
ARCHIVE = Path(__file__).parents[1] / "clock_bridge" / "SeveralUDOClockSync.ts4script"
BUILD_SCRIPT = Path(__file__).parents[1] / "clock_bridge" / "build_clock_sync.ps1"


class ClockModSourceTests(unittest.TestCase):
    def load_module(self):
        package_name = "clock_mod_source_test"
        core = types.SimpleNamespace(VERSION="old")

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
        self.assertEqual(result["skills"], [{"name": "Logic", "level": 7}])
        self.assertEqual(result["milestones"], ["First Steps"])
        self.assertTrue(result["skills_scan_supported"])
        self.assertTrue(result["milestone_scan_supported"])
        self.assertEqual(result["telemetry_version"], 4)

    def test_guarded_v4_snapshot_reports_selected_life_history_modules(self):
        module = self.load_module()

        class RelationshipBit_Spouse:
            pass

        class Buff_Malaria:
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
            buff_component=types.SimpleNamespace(get_all_buffs=lambda: (health_buff,)),
            age_in_days=103, age_progress_percentage=.75, days_until_age_up=6,
            career_tracker=types.SimpleNamespace(careers={1: career}),
            degree_tracker=types.SimpleNamespace(degrees=(Degree_History,)),
            occult_tracker=types.SimpleNamespace(occult_rank="Master", unlocked_perks=()),
            aspiration_tracker=types.SimpleNamespace(
                active_aspiration=Aspiration_Successful_Lineage,
                completed_aspirations=(),
            ),
            trait_tracker=types.SimpleNamespace(traits=(Trait_Lifestyle_Close_Knit,)),
            portrait_bytes=b"portrait" * 20,
        )
        result = module._extended_snapshot(sim, None)
        self.assertEqual(result["telemetry_version"], 4)
        self.assertEqual(result["clock_sync_version"], "2.1.0")
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
        self.assertEqual(result["illnesses"][0]["name"], "Malaria")
        self.assertEqual(result["game_portrait"]["capture_mode"], "embedded")
        self.assertTrue(all(result["telemetry_capabilities"].get(name) for name in (
            "pregnancy", "genealogy", "relationships", "health", "life_stage",
            "career_education", "occult_progress", "personal_development", "portraits",
        )))
        self.assertTrue(result["clock_sync_diagnostics"]["healthy"])

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
