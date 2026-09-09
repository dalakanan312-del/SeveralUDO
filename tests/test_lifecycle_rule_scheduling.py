"""Regression coverage for maternal tables accidentally scheduled at birth."""
import copy
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app import domain
from app.db import Base
from app.models import Change, ChronicleSave, Record, Workspace


class LifecycleRuleSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        workspace = Workspace(name="Isolated roll tests")
        self.session.add(workspace)
        self.session.flush()
        self.save = ChronicleSave(workspace_id=workspace.id, name="Black test", global_day=14,
                                  start_year=979, days_per_year=4, settings={})
        self.session.add(self.save)
        self.session.flush()
        self.baby = Record(save_id=self.save.id, kind="sim", label="Galia test", global_day=14,
                           data={"birth_global_day": 14, "game_age_stage": "Age.BABY"})
        self.session.add(self.baby)
        self.session.flush()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def rule(self, label, age):
        record = Record(save_id=self.save.id, kind="roll_rule", label=label,
                        data={"age_days": age, "die": "d20", "bad_results": "1",
                              "active": True, "core_ruleset_id": "severaludo"})
        self.session.add(record)
        self.session.flush()
        return record

    def roll(self, rule, *, completed=False, source=None, **extra):
        record = Record(save_id=self.save.id, kind="roll", label=rule.label, global_day=14,
                        data={"sim_id": self.baby.id, "completed": completed,
                              "source": source or f"aging:{self.baby.id}:{rule.id}",
                              "due_global_day": 14, **extra})
        self.session.add(record)
        self.session.flush()
        return record

    def active_rolls(self):
        self.session.flush()
        return list(self.session.scalars(select(Record).where(
            Record.save_id == self.save.id, Record.kind == "roll", Record.deleted.is_(False))))

    def test_default_ages_are_separate_and_maternal_tables_never_age_milestones(self):
        rules = [self.rule(label, age) for label, age, _, _ in domain.DEFAULT_STAGES]
        rules += [self.rule(label, None) for label, _, _ in domain.DEFAULT_MATERNAL_RULES]
        self.assertEqual(domain._schedule_sim_lifecycle_rolls(self.session, self.save, self.baby, rules), 10)
        days = {r.data["roll_type"]: r.global_day for r in self.active_rolls()}
        self.assertEqual(days, {label: 14 + age for label, age, _, _ in domain.DEFAULT_STAGES})
        self.assertEqual(days["Infant"], 15)
        self.assertEqual(days["Young Adult"], 86)
        self.assertEqual(domain._schedule_sim_lifecycle_rolls(self.session, self.save, self.baby, rules), 0)

    def test_missing_known_offsets_scale_for_twelve_day_years_and_unknowns_are_skipped(self):
        self.save.days_per_year = 12
        rules = [self.rule(label, None) for label, _, _, _ in domain.DEFAULT_STAGES]
        rules += [self.rule("Manual table", None), self.rule("Unknown table", ""),
                  self.rule("Maternal — Adult", None), self.rule("Maternal — Teen", 0)]
        domain._schedule_sim_lifecycle_rolls(self.session, self.save, self.baby, rules)
        rolls = {r.data["roll_type"]: r for r in self.active_rolls()}
        self.assertEqual(len(rolls), 10)
        self.assertEqual(rolls["Infant"].global_day, 17)
        self.assertEqual(rolls["Toddler"].global_day, 26)
        self.assertEqual(rolls["Young Adult"].global_day, 230)
        self.assertEqual(rolls["Newborn"].data["death_window_end"], 16)

    def test_explicit_custom_ages_are_authoritative(self):
        self.save.days_per_year = 12
        rules = [self.rule("Custom birth", 0), self.rule("Custom milestone", "27"),
                  self.rule("Malformed milestone", "unknown"), self.rule("Negative milestone", -2)]
        domain._schedule_sim_lifecycle_rolls(self.session, self.save, self.baby, rules)
        self.assertEqual({r.data["roll_type"]: r.global_day for r in self.active_rolls()},
                         {"Custom birth": 14, "Custom milestone": 41})

    def test_repair_preserves_history_deliveries_frozen_records_and_valid_aging(self):
        maternal = self.rule("Maternal — Adult", None)
        infant = self.rule("Infant", 1)
        bad = self.roll(maternal)
        completed = self.roll(maternal, completed=True, actual=12)
        completed_before = copy.deepcopy(completed.data)
        legitimate = self.roll(maternal, source=f"maternal:pregnancy:{maternal.id}:baby:1")
        frozen = self.roll(maternal, infinite_frozen=True)
        valid = self.roll(infant)
        self.assertEqual(domain.retire_invalid_lifecycle_rolls(self.session, self.save), 1)
        self.assertTrue(bad.deleted)
        self.assertIn("Non-aging", bad.data["retired_reason"])
        self.assertEqual(completed.data, completed_before)
        self.assertFalse(any(r.deleted for r in [completed, legitimate, frozen, valid]))
        self.assertEqual(domain.retire_invalid_lifecycle_rolls(self.session, self.save), 0)
        self.assertEqual(self.session.scalar(select(Change.operation).where(Change.record_id == bad.id)), "delete")

    def test_normal_scheduler_retires_bad_roll_without_recreating_it(self):
        maternal = self.rule("Maternal — Young Adult", None)
        self.rule("Newborn", 0)
        self.rule("Infant", 1)
        bad = self.roll(maternal)
        domain.schedule_rolls(self.session, self.save)
        self.assertTrue(bad.deleted)
        domain.schedule_rolls(self.session, self.save)
        self.assertFalse(any("Maternal" in r.label for r in self.active_rolls()))
        self.assertEqual({r.data["roll_type"]: r.global_day for r in self.active_rolls()
                          if str(r.data.get("source", "")).startswith("aging:")},
                         {"Newborn": 14, "Infant": 15})

    def test_delivery_still_creates_one_maternal_roll_per_baby_for_mother(self):
        for label, _, _ in domain.DEFAULT_MATERNAL_RULES:
            self.rule(label, None)
        mother = Record(save_id=self.save.id, kind="sim", label="Mother", global_day=-70,
                        data={"birth_global_day": -70})
        self.session.add(mother)
        self.session.flush()
        pregnancy = Record(save_id=self.save.id, kind="pregnancy", label="Twins", global_day=14,
                           data={"mother_id": mother.id, "status": "delivered",
                                 "babies_delivered": 2, "actual_delivery_global_day": 14})
        self.session.add(pregnancy)
        self.session.flush()
        self.assertEqual(domain.preserve_delivery_maternal_rolls(self.session, self.save, pregnancy), 2)
        self.assertEqual(domain.retire_invalid_lifecycle_rolls(self.session, self.save), 0)
        rolls = self.active_rolls()
        self.assertEqual(len(rolls), 2)
        self.assertTrue(all(r.data["sim_id"] == mother.id and r.data["roll_type"] == "Maternal — Young Adult"
                            and r.global_day == 14 for r in rolls))
        self.assertEqual({r.data["maternal_baby_index"] for r in rolls}, {1, 2})
        self.assertEqual(domain.preserve_delivery_maternal_rolls(self.session, self.save, pregnancy), 0)


if __name__ == "__main__":
    unittest.main()
