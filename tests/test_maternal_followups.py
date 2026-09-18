import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import action_previews, core_rulesets, domain, maternal_rules, play_clarity
from app.db import Base
from app.models import ChronicleSave, Record, Workspace


class MaternalFollowupTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.s = Session(self.engine)
        workspace = Workspace(name="Maternal tests")
        self.s.add(workspace); self.s.flush()
        self.save = ChronicleSave(workspace_id=workspace.id, name="Test", global_day=100,
            start_year=1300, days_per_year=4, settings={"core_ruleset_id": core_rulesets.SEVERALUDO})
        self.s.add(self.save); self.s.flush()
        self.mother = self.make("sim", "Mother", {"birth_global_day": 1})
        self.other = self.make("sim", "Other", {"birth_global_day": 1})
        self.pregnancy = self.make("pregnancy", "Twins", {"mother_id": self.mother.id,
            "status": "Delivered", "babies_delivered": 2, "actual_delivery_global_day": 100})
        self.later = self.make("roll", "Later birthday", {"sim_id": self.mother.id, "completed": False}, day=150)

    def tearDown(self):
        self.s.close(); self.engine.dispose()

    def make(self, kind, label, data, day=100):
        row = Record(save_id=self.save.id, kind=kind, label=label, global_day=day, data=data)
        self.s.add(row); self.s.flush(); return row

    def initial(self, stage="Young Adult", baby=1, core=core_rulesets.SEVERALUDO):
        return self.make("roll", "Mother maternal", {"sim_id": self.mother.id,
            "roll_type": "Maternal — " + stage, "source_id": self.pregnancy.id,
            "source": f"maternal:{self.pregnancy.id}:test: baby:{baby}", "maternal_baby_index": baby,
            "delivery_global_day": 100, "die": "d20", "bad_results": "1-4", "core_ruleset_id": core})

    def child(self, origin):
        return self.s.scalar(select(Record).where(Record.kind == "roll", Record.data["origin_roll_id"].as_string() == origin.id))

    def test_all_source_dice_and_first_failure_never_schedules_death(self):
        for stage, sides in maternal_rules.TABLE.items():
            with self.subTest(stage=stage):
                origin = self.initial(stage)
                result = domain.complete_roll(self.s, self.save, origin, 1)
                child = self.child(origin)
                self.assertEqual(child.data["die"], f"d{sides}")
                self.assertEqual(child.data["bad_results"], "1")
                self.assertEqual(result["automatic_followups"], 1)
                self.assertIsNone(result["death"])
                self.assertNotIn("death_global_day", self.mother.data)
                self.assertFalse(self.later.deleted)

    def test_nonfatal_second_roll_records_infertility_without_death(self):
        for stage in maternal_rules.TABLE:
            with self.subTest(stage=stage):
                origin = self.initial(stage)
                domain.complete_roll(self.s, self.save, origin, 1)
                result = domain.complete_roll(self.s, self.save, self.child(origin), 2)
                self.assertTrue(self.mother.data["infertile"])
                self.assertIn("traumatic", self.mother.data["fertility_status"])
                self.assertIsNone(result["death"])
                self.assertFalse(self.later.deleted)
                self.assertNotIn("death_global_day", self.mother.data)

    def test_heads_schedules_death_only_on_second_roll(self):
        origin = self.initial()
        domain.complete_roll(self.s, self.save, origin, 1)
        child = self.child(origin)
        result = domain.complete_roll(self.s, self.save, child, 1)
        self.assertIn("Heads", result["outcome"])
        self.assertEqual(self.mother.data["death_source_roll_id"], child.id)
        self.assertEqual(self.mother.data["death_global_day"], 100)
        self.assertFalse(self.mother.data["death_confirmed"])
        self.assertFalse(self.mother.data.get("infertile", False))
        self.assertTrue(self.later.deleted)

    def test_passing_primary_never_generates_a_followup(self):
        origin = self.initial()
        result = domain.complete_roll(self.s, self.save, origin, 12)
        self.assertIsNone(self.child(origin))
        self.assertEqual(result["automatic_followups"], 0)

    def test_morbid_uses_only_coin_flip_for_every_age(self):
        for stage in [*maternal_rules.TABLE, "All Ages · 1300s"]:
            with self.subTest(stage=stage):
                origin = self.initial(stage, core=core_rulesets.MORBID)
                domain.complete_roll(self.s, self.save, origin, 1)
                self.assertEqual(self.child(origin).data["die"], "d2")
                self.assertIn("Morbid", self.child(origin).data["notes"])

    def test_other_rulesets_are_not_silently_given_this_table(self):
        origin = self.initial(core=core_rulesets.CLASSIC_2023)
        self.assertIsNone(maternal_rules.table_for(self.s, self.save, origin))

    def test_twin_followups_keep_separate_identities_and_delivery_refresh_does_not_rewrite_them(self):
        rules = self.make("roll_rule", "Maternal — Young Adult", {"active": True, "die": "d20", "bad_results": "1-4"})
        first, second = self.initial(baby=1), self.initial(baby=2)
        for origin in (first, second):
            domain.complete_roll(self.s, self.save, origin, 1)
        children = [self.child(first), self.child(second)]
        self.assertNotEqual(children[0].id, children[1].id)
        self.assertEqual([c.data["maternal_baby_index"] for c in children], [1, 2])
        before = [(c.id, c.global_day, dict(c.data)) for c in children]
        domain.preserve_delivery_maternal_rolls(self.s, self.save, self.pregnancy)
        domain.refresh_pending_rolls(self.s, self.save)
        self.assertEqual([(c.id, c.global_day, dict(c.data)) for c in children], before)

    def test_refresh_is_idempotent_and_completed_results_are_not_duplicated(self):
        origin = self.initial()
        domain.complete_roll(self.s, self.save, origin, 1)
        self.assertEqual(maternal_rules.resume_pending(self.s, self.save), 0)
        domain.complete_roll(self.s, self.save, self.child(origin), 2)
        self.assertEqual(maternal_rules.resume_pending(self.s, self.save), 0)

    def test_automation_pause_does_not_schedule_death_and_resume_adds_followup_once(self):
        self.save.settings = {**self.save.settings, "automation_enabled": False}
        origin = self.initial()
        domain.complete_roll(self.s, self.save, origin, 1)
        self.assertIsNone(self.child(origin))
        self.assertNotIn("death_global_day", self.mother.data)
        self.save.settings = {**self.save.settings, "automation_enabled": True}
        domain.schedule_rolls(self.s, self.save)
        self.assertIsNotNone(self.child(origin))
        self.assertEqual(maternal_rules.resume_pending(self.s, self.save), 0)

    def test_unknown_age_uses_scaled_delivery_date_not_current_age(self):
        self.save.days_per_year = 12
        self.save.global_day = 1000
        self.mother.data = {"birth_global_day": 1}
        origin = self.initial("All Ages")
        origin.global_day = 300; origin.data = {**origin.data, "delivery_global_day": 300}
        self.assertEqual(maternal_rules.table_for(self.s, self.save, origin)["stage"], "Young Adult")

    def test_old_completed_failure_is_not_silently_replayed(self):
        origin = self.initial()
        origin.data = {**origin.data, "actual": 1, "completed": True, "outcome": "Failed"}
        self.assertEqual(maternal_rules.resume_pending(self.s, self.save), 0)
        self.assertIsNone(self.child(origin))

    def test_preview_is_rollback_safe_and_shows_fertility_effect(self):
        origin = self.initial()
        plan = action_previews.simulate(self.s, self.save, lambda: domain.complete_roll(self.s, self.save, origin, 1))
        self.assertIsNone(self.child(origin))
        self.assertFalse(origin.data.get("completed"))
        self.assertTrue(any("death or infertility" in e for e in action_previews.describe(plan, "roll")["effects"]))
        domain.complete_roll(self.s, self.save, origin, 1)
        child = self.child(origin)
        plan = action_previews.simulate(self.s, self.save, lambda: domain.complete_roll(self.s, self.save, child, 2))
        self.assertFalse(self.mother.data.get("infertile", False))
        self.assertTrue(any("Fertility status" in e for e in action_previews.describe(plan, "roll")["effects"]))

    def test_presentation_distinguishes_second_roll_and_explains_coin(self):
        origin = self.initial()
        domain.complete_roll(self.s, self.save, origin, 1)
        shown = play_clarity.roll_presentation(self.save, self.child(origin), self.mother)
        self.assertIn("death or infertility", shown["title"])
        self.assertIn("heads", shown["calculation"])

    def test_infertility_stops_new_and_existing_allowance_rolls_but_preserves_pregnancy_history(self):
        origin = self.initial()
        domain.complete_roll(self.s, self.save, origin, 1)
        domain.complete_roll(self.s, self.save, self.child(origin), 2)
        with self.assertRaisesRegex(ValueError, "infertile"):
            domain.create_pregnancy_count_roll(self.s, self.save, self.mother)
        allowance = self.make("roll", "Pregnancy count", {"sim_id": self.mother.id, "pregnancy_count_roll": True})
        with self.assertRaisesRegex(ValueError, "infertile"):
            domain.complete_roll(self.s, self.save, allowance, 3)
        self.assertEqual(self.pregnancy.data["status"], "Delivered")

    def test_reopening_infertility_keeps_other_twin_result_and_original_fertility(self):
        self.mother.data = {**self.mother.data, "fertility_status": "Player entered"}
        children = []
        for baby in (1, 2):
            origin = self.initial(baby=baby)
            domain.complete_roll(self.s, self.save, origin, 1)
            child = self.child(origin); children.append(child)
            domain.complete_roll(self.s, self.save, child, 2)
        maternal_rules.reopen_infertility(self.s, self.mother, children[0])
        self.assertTrue(self.mother.data["infertile"])
        maternal_rules.reopen_infertility(self.s, self.mother, children[1])
        self.assertNotIn("infertile", self.mother.data)
        self.assertEqual(self.mother.data["fertility_status"], "Player entered")

    def test_missing_age_is_blocked_instead_of_silently_scheduling_death(self):
        origin = self.initial("All Ages")
        self.mother.global_day = None
        self.mother.data = {}
        with self.assertRaisesRegex(ValueError, "age at delivery is missing"):
            domain.complete_roll(self.s, self.save, origin, 1)
        self.assertNotIn("death_global_day", self.mother.data)

    def test_confirmed_death_and_wrong_origin_sim_are_protected(self):
        origin = self.initial()
        domain.complete_roll(self.s, self.save, origin, 1)
        child = self.child(origin)
        self.mother.data = {**self.mother.data, "death_confirmed": True}
        with self.assertRaisesRegex(ValueError, "no longer available"):
            domain.complete_roll(self.s, self.save, child, 2)
        self.mother.data = {**self.mother.data, "death_confirmed": False}
        child.data = {**child.data, "sim_id": self.other.id}
        with self.assertRaisesRegex(ValueError, "original failed maternal"):
            domain.complete_roll(self.s, self.save, child, 2)

    def test_reopen_route_blocks_completed_child_then_safely_restores_its_pending_form(self):
        from app import main
        from fastapi import HTTPException, Request
        origin = self.initial()
        domain.complete_roll(self.s, self.save, origin, 1)
        child = self.child(origin)
        domain.complete_roll(self.s, self.save, child, 2)
        self.s.commit()
        @contextmanager
        def db():
            yield self.s
            self.s.commit()
        request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "session": {}})
        with patch.object(main, "db", db), patch.object(main, "owned_save", return_value=self.save):
            with self.assertRaises(HTTPException) as blocked:
                main.reopen_roll(request, origin.id)
            self.assertEqual(blocked.exception.status_code, 409)
            self.assertTrue(self.mother.data["infertile"])
            self.assertEqual(main.reopen_roll(request, child.id).status_code, 303)
            self.assertFalse(self.mother.data.get("infertile"))
            self.assertEqual(main.reopen_roll(request, origin.id).status_code, 303)
            self.assertTrue(child.deleted)
            domain.complete_roll(self.s, self.save, origin, 1)
            self.assertFalse(child.deleted)
            self.assertFalse(child.data["completed"])
            self.assertEqual(self.child(origin).id, child.id)
