"""Whole-dynasty statistics on disposable data only."""
import copy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import infinite_dynasty as dynasty, insights, main, statistics_dashboard as dashboard
from app.models import ChronicleSave, Record
from tests import test_infinite_decades as fixture


class StatisticsDashboardTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.InfiniteDecadesTests()
        self.f.setUp()
        self.s, self.save = self.f.session, self.f.save

    def tearDown(self):
        self.f.tearDown()

    def context(self, branch="all"):
        return dashboard.context(self.s, self.save, {"statistics_branch": branch})

    def add(self, kind, label, data, day=None, deleted=False):
        row = Record(save_id=self.save.id, kind=kind, label=label, data=data, global_day=day, deleted=deleted)
        self.s.add(row)
        self.s.flush()
        return row

    def test_ordinary_save_still_works_and_excludes_deleted(self):
        self.add("sim", "Archived Sim", {"birth_global_day": 1}, deleted=True)
        result = self.context()
        self.assertEqual(result["statistics"]["population"], 5)
        self.assertFalse(result["statistics_dynasty"])
        self.assertEqual(result["statistics_branches"], [])

    def test_all_branches_default_unique_ids_not_checkpoint_copies(self):
        root = self.f.enable()
        child = self.f.capture()
        with patch.object(dynasty, "unpack_snapshot", side_effect=AssertionError("No checkpoint scans")):
            result = dashboard.context(self.s, self.save, {})
        self.assertEqual(result["statistics_scope"], "all")
        self.assertEqual(result["statistics"]["population"], 5)
        self.assertEqual(result["statistics"]["households"], 1)
        self.assertEqual(sum(r["population"] for r in result["statistics_branches"]), 5)
        by_id = {r["id"]: r for r in result["statistics_branches"]}
        self.assertEqual(by_id[root.id]["population"], 3)
        self.assertEqual(by_id[child.id]["population"], 1)
        self.assertEqual(by_id[child.id]["status"], "Waiting")

    def test_branch_filters_are_read_only_and_shared_households_available(self):
        root = self.f.enable()
        child = self.f.capture()
        before = (self.save.global_day, self.save.revision, copy.deepcopy(self.save.settings), copy.deepcopy(child.data))
        result = self.context(child.id)
        self.assertEqual(result["statistics"]["population"], 1)
        self.assertEqual(result["statistics"]["households"], 1)
        self.assertEqual(result["statistics_scope_label"], "Cara line")
        self.assertIn("#dynasty-person", result["statistics_profiles"][self.f.people[2].id])
        self.assertEqual(self.context("active")["statistics_scope"], root.id)
        self.assertEqual(self.context("foreign-branch")["statistics_scope"], "all")
        self.assertEqual(before, (self.save.global_day, self.save.revision, self.save.settings, child.data))
        self.assertFalse(self.s.dirty)

    def test_paused_age_future_death_and_roll_due_use_preserved_date(self):
        self.f.people[2].data = {**self.f.people[2].data, "death_global_day": 150}
        self.s.commit()
        self.f.enable()
        child = self.f.capture(day=104)
        self.save.global_day = 200
        self.s.commit()
        result = self.context(child.id)["statistics"]
        self.assertEqual(result["living"], 1)
        self.assertEqual(result["deceased"], 0)
        self.assertEqual(result["average_living_age"], 103)
        self.assertEqual(result["rolls"]["pending_due"], 0)
        self.assertEqual(result["rolls"]["pending_future"], 1)

    def test_switch_keeps_finished_and_waiting_people_in_totals(self):
        root = self.f.enable()
        child = self.f.capture()
        self.f.finish_modern()
        dynasty.activate_branch(self.s, self.save, child.id)
        self.s.commit()
        result = self.context()
        self.assertEqual(result["statistics"]["population"], 5)
        self.assertEqual(result["statistics"]["living"], 5)
        by_id = {r["id"]: r for r in result["statistics_branches"]}
        self.assertEqual(by_id[root.id]["status"], "Modern")
        self.assertEqual(by_id[root.id]["day"], 401)
        self.assertEqual(by_id[child.id]["day"], 104)

    def test_paused_dynasty_still_includes_every_branch(self):
        self.f.enable()
        self.f.capture()
        dynasty.set_enabled(self.s, self.save, False)
        self.s.commit()
        result = self.context()
        self.assertEqual(result["statistics"]["population"], 5)
        self.assertTrue(any(r["status"] == "Paused" for r in result["statistics_branches"]))

    def test_no_foreign_save_data_is_used(self):
        other = ChronicleSave(workspace_id=self.save.workspace_id, name="Not this dynasty")
        self.s.add(other)
        self.s.flush()
        self.s.add(Record(save_id=other.id, kind="sim", label="Foreign Sim", data={"birth_global_day": 1}))
        self.s.flush()
        self.f.enable()
        self.assertEqual(self.context()["statistics"]["population"], 5)

    def test_hidden_events_and_retired_frozen_rolls_are_excluded(self):
        event = self.add("event", "Hidden event", {"hidden": True})
        # Use the actual ignore contract independently of its UI field names.
        self.add("roll", "Hidden event roll", {"event_id": event.id})
        self.add("roll", "Retired paused roll", {"infinite_frozen": True, "retired_reason": "Duplicate obligation"}, deleted=True)
        self.f.enable()
        with patch.object(dashboard.domain, "event_is_ignored", lambda row: row.id == event.id):
            result = self.context()["statistics"]
        self.assertEqual(result["events"], 0)
        self.assertEqual(result["rolls"]["total"], 1)

    def test_death_flags_without_dates_are_not_living_or_fabricated_ages(self):
        self.f.people[0].data = {"birth_global_day": 1, "death_confirmed": True}
        self.f.people[1].data = {"birth_global_day": 1, "game_was_dead": True}
        self.s.flush()
        result = self.context()["statistics"]
        self.assertEqual((result["living"], result["deceased"]), (3, 2))
        self.assertIsNone(result["average_death_age"])
        self.assertEqual(result["children"]["unknown"], 2)
        self.assertEqual(result["challenge_deaths"], 0)

    def test_adulthood_scales_and_future_death_does_not_resolve_child(self):
        self.save.days_per_year = 12
        self.save.global_day = 150
        self.f.people[0].data = {"birth_global_day": 1, "death_global_day": 180}
        result = insights.statistics([self.f.people[0]], self.save)
        self.assertEqual(result["children"]["adulthood_days"], 216)
        self.assertEqual(result["children"]["pending"], 1)
        self.assertEqual(result["children"]["died_young"], 0)
        self.assertIsNone(result["children"]["survival_rate"])

    def test_birth_sizes_normalize_units_and_ignore_missing_invalid_future(self):
        for sim, weight, length in zip(self.f.people, ["3", "6.6138678655", None, "nan", "5"], ["50", "20", None, None, "55"]):
            unit = "lb" if sim == self.f.people[1] else "kg"
            sim.data = {"birth_global_day": 200 if sim == self.f.people[4] else 1,
                "birth_measurements": {"source": "Player-entered", "weight": {"value": weight, "unit": unit},
                "length": {"value": length, "unit": "in" if sim == self.f.people[1] else "cm"}}}
        self.s.flush()
        values = self.context()["statistics"]["birth_details"]
        self.assertEqual(values["weights"], 2)
        self.assertEqual(values["weight_kg"], 3.0)
        self.assertEqual(values["length_cm"], 50.4)
        self.assertEqual(values["sources"], [("Player-entered", 2)])

    def test_labor_counts_pregnancies_not_twins_and_respects_manual_override(self):
        self.add("pregnancy", "Twins", {"babies_delivered": 2, "status": "Delivered", "labor_tracking": {"status": "ended", "minutes": 600}, "labor_duration_manual": {"minutes": 120}})
        self.add("pregnancy", "Observed", {"status": "Delivered", "labor_tracking": {"status": "ended", "minutes": 240}})
        self.add("pregnancy", "Ongoing", {"labor_tracking": {"status": "in_progress", "minutes": 999}})
        self.add("pregnancy", "Rewound", {"labor_tracking": {"status": "interrupted", "minutes": 999}})
        self.add("pregnancy", "Missing", {})
        result = self.context()["statistics"]["birth_details"]
        self.assertEqual(result["labor_count"], 2)
        self.assertEqual(result["labor_average"], "3h 00m")
        self.assertEqual((result["manual"], result["estimated"], result["ongoing"], result["interrupted"]), (1, 1, 1, 1))

    def test_completed_pregnancy_multiple_births_use_actual_not_forecast(self):
        self.add("pregnancy", "Only one delivered", {"status": "Delivered", "babies_expected": 2, "babies_delivered": 1})
        self.add("pregnancy", "Expecting twins", {"status": "Active", "babies_expected": 2})
        p = self.add("pregnancy", "Actual twins", {"status": "Completed", "babies_delivered": 1})
        for sim in self.f.people[:2]:
            sim.data = {**sim.data, "pregnancy_id": p.id}
        self.s.flush()
        result = self.context()["statistics"]["pregnancy"]
        self.assertEqual(result["multiple_births"], 1)
        self.assertEqual(result["delivered_babies"], 3)
        self.assertEqual(result["active"], 1)

    def test_spoiler_free_masks_future_births_and_deaths(self):
        self.f.people[2].data = {"birth_global_day": 1, "death_global_day": 150, "cause_of_death": "Future cause"}
        self.s.commit()
        self.f.enable()
        self.f.capture(day=104)
        with dynasty.branch_operation(self.s, self.save):
            self.save.settings = {**self.save.settings, "infinite_decades": {**dynasty.state(self.save), "spoiler_free": True}}
        self.s.commit()
        result = self.context()["statistics"]
        self.assertEqual(result["living"], 5)
        self.assertEqual(result["causes"], [])

    def test_page_renders_all_scopes_without_switching_branch(self):
        root = self.f.enable()
        child = self.f.capture()
        with patch.object(main, "SessionLocal", self.f.sessions):
            client = TestClient(main.app)
            client.post("/saves/select", data={"save_id": self.save.id})
            for query in ("", "?statistics_branch=" + child.id, "?statistics_branch=active"):
                response = client.get("/p/statistics" + query)
                self.assertEqual(response.status_code, 200, response.text[:400])
                self.assertIn('id="birth-details"', response.text)
                self.assertIn("Branch comparison", response.text)
                self.assertIn("statistics.css", response.text)
            client.close()
        self.s.refresh(self.save)
        self.assertEqual(dynasty.state(self.save)["active_branch_id"], root.id)

    def test_empty_save_has_clear_missing_values(self):
        for row in self.s.scalars(select(Record).where(Record.save_id == self.save.id)):
            row.deleted = True
        self.s.flush()
        result = self.context()["statistics"]
        self.assertEqual(result["population"], 0)
        self.assertIsNone(result["average_living_years"])
        self.assertIsNone(result["birth_details"]["labor_average"])


if __name__ == "__main__":
    unittest.main()
