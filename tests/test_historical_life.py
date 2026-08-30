import unittest

from app import historical_life
from app import sync
from app.models import ChronicleSave, Record


class HistoricalLifeTests(unittest.TestCase):
    def test_dashboard_connects_requested_decades_features(self):
        save = ChronicleSave(id="save", workspace_id="workspace", name="Test", global_day=85, start_year=1300, days_per_year=4,
                             settings={"historical_accuracy_profile": "strict", "marriage_min_age_days": 20})
        first = Record(id="first", save_id="save", kind="sim", label="Anne", global_day=1,
                       data={"birth_global_day": 1, "sex": "Female", "game_age": "Young Adult", "current_household_id": "home",
                             "game_inventory_scan_supported": True,
                             "game_inventory_items": [{"name": "Family Portrait", "definition_id": "44001", "scope": "personal"}]})
        second = Record(id="second", save_id="save", kind="sim", label="Robin", global_day=4,
                        data={"birth_global_day": 4, "sex": "Male", "game_age": "Young Adult", "current_household_id": "other"})
        home = Record(id="home", save_id="save", kind="household", label="Anne House", data={"head_sim_id": "first", "social_class": "Gentry"})
        result = historical_life.build([first, second, home], save)
        self.assertEqual(result["profile_key"], "strict")
        self.assertEqual(result["era"]["decade"], 1320)
        self.assertEqual(result["demographics"]["living"], 2)
        self.assertEqual(result["heirlooms"]["detected"][0]["item"]["name"], "Family Portrait")
        self.assertEqual(len(result["marriage"]["pairs"]), 1)

    def test_correspondence_uses_recorded_facts(self):
        save = ChronicleSave(id="save", workspace_id="workspace", name="Test", global_day=9, start_year=1300, days_per_year=4)
        author = Record(id="author", save_id="save", kind="sim", label="Anne", data={})
        event = Record(id="event", save_id="save", kind="event", label="A poor harvest", global_day=9, data={})
        label, body = historical_life.compose_correspondence("diary", author, None, "the winter", "Food is scarce.", save, [event])
        self.assertIn("Diary of Anne", label)
        self.assertIn("A poor harvest", body)
        self.assertIn("Food is scarce", body)

    def test_historical_life_records_sync_between_desktop_and_online(self):
        expected = {"era_check", "estate_plan", "economy_entry", "education_plan", "reputation_event",
                    "migration_plan", "memorial", "heirloom", "correspondence"}
        self.assertTrue(expected.issubset(sync.SYNC_KINDS))


if __name__ == "__main__":
    unittest.main()
