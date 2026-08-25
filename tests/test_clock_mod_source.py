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
        self.assertEqual(result["telemetry_version"], 3)

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
