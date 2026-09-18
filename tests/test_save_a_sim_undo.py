"""Undo uses isolated records; no installed save or game integration is accessed."""
import copy
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import domain, main, save_a_sims as mercy
from app.db import Base
from app.models import Change, ChronicleSave, Record, Workspace


class UndoSaveASimTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.workspace = Workspace(name="Undo tests")
        self.session.add(self.workspace)
        self.session.flush()
        self.save = ChronicleSave(workspace_id=self.workspace.id, name="Undo test", global_day=30,
                                  settings={"free_save_a_sims": 2})
        self.session.add(self.save)
        self.session.flush()
        self.sim = self.row("sim", "Anne", {"birth_global_day": 1, "death_global_day": 31,
            "cause_of_death": "Fever", "death_source_roll_id": "danger", "death_place": "Home"})
        self.other = self.row("sim", "Unaffected", {"birth_global_day": 1})
        self.death = self.row("death", "Anne's scheduled death", {"sim_id": self.sim.id,
            "completed": False, "source_roll_id": "danger"}, day=31)
        self.roll = self.row("roll", "Anne's later birthday", {"sim_id": self.sim.id, "completed": False,
            "retired_by_death_roll_id": "danger", "retired_reason": "Original reason",
            "retired_global_day": 29}, day=40, deleted=True)
        self.before = {row.id: mercy._snapshot(row) for row in (self.sim, self.death, self.roll)}
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def row(self, kind, label, data, day=None, deleted=False):
        row = Record(save_id=self.save.id, kind=kind, label=label, data=data, global_day=day, deleted=deleted)
        self.session.add(row)
        self.session.flush()
        domain.journal(self.session, row, "delete" if deleted else "upsert", 0)
        self.session.flush()
        return row

    def spend(self):
        result = mercy.spend_on_sim(self.session, self.save, self.sim, "Mistaken click")
        self.session.commit()
        return result["credit"]

    def test_undo_restores_death_and_rolls_refunds_once_and_keeps_ledger(self):
        entry = self.spend()
        self.assertEqual(mercy.balance(self.session, self.save), 1)
        result = mercy.undo_spend(self.session, self.save, entry)
        self.assertFalse(result["already_undone"])
        self.assertEqual(mercy.balance(self.session, self.save), 2)
        for row in (self.sim, self.death, self.roll):
            self.assertEqual(mercy._snapshot(row), self.before[row.id])
        self.assertFalse(self.death.data["completed"])
        self.assertEqual(entry.data["amount"], -1)
        self.assertTrue(entry.data["undo_credit_id"])
        self.assertTrue(mercy.undo_spend(self.session, self.save, entry)["already_undone"])
        self.assertEqual(mercy.balance(self.session, self.save), 2)
        self.assertEqual(len(mercy.credit_entries(self.session, self.save)), 2)
        board = mercy.dashboard(self.session, self.save)
        self.assertEqual((board["earned"], board["spent"], board["refunded"]), (0, 0, 1))

    def test_can_use_again_after_undo_without_farming_credits(self):
        first = self.spend()
        mercy.undo_spend(self.session, self.save, first)
        second = self.spend()
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(mercy.balance(self.session, self.save), 1)
        mercy.undo_spend(self.session, self.save, second)
        self.assertEqual(mercy.balance(self.session, self.save), 2)

    def test_unrelated_sim_updates_are_preserved(self):
        entry = self.spend()
        self.sim.data = {**self.sim.data, "notes": "Keep these", "game_skills": [{"name": "Painting", "level": 3}]}
        self.sim.label = "Anne Renamed"
        self.sim.version += 1
        self.session.commit()
        mercy.undo_spend(self.session, self.save, entry)
        self.assertEqual(self.sim.label, "Anne Renamed")
        self.assertEqual(self.sim.data["notes"], "Keep these")
        self.assertEqual(self.sim.data["game_skills"][0]["level"], 3)

    def test_repeated_undo_does_not_repeat_all_sims_scheduled_award(self):
        self.other.data = {**self.other.data, "death_global_day": 90}
        for _ in range(3):
            entry = self.spend()
            self.assertEqual(mercy.balance(self.session, self.save), 2)
            mercy.undo_spend(self.session, self.save, entry)
            self.assertEqual(mercy.balance(self.session, self.save), 3)
        awards = [row for row in mercy.credit_entries(self.session, self.save)
                  if str(row.data.get("source_key", "")).startswith("all-sims-scheduled:")]
        self.assertEqual(len(awards), 1)

    def test_later_death_is_not_overwritten(self):
        entry = self.spend()
        self.sim.data = {**self.sim.data, "death_global_day": 50, "cause_of_death": "New danger"}
        with self.assertRaisesRegex(ValueError, "details changed"):
            mercy.undo_spend(self.session, self.save, entry)
        self.assertEqual(self.sim.data["death_global_day"], 50)
        self.assertEqual(mercy.balance(self.session, self.save), 1)

    def test_completed_restored_roll_prevents_undo_without_partial_writes(self):
        entry = self.spend()
        self.roll.data = {**self.roll.data, "completed": True, "actual": 3}
        with self.assertRaisesRegex(ValueError, "future roll changed"):
            mercy.undo_spend(self.session, self.save, entry)
        self.assertTrue(self.death.deleted)
        self.assertNotIn("death_global_day", self.sim.data)
        self.assertEqual(mercy.balance(self.session, self.save), 1)

    def test_confirmed_game_death_is_protected(self):
        entry = self.spend()
        for flag in ("death_confirmed", "game_was_dead"):
            original = copy.deepcopy(self.sim.data)
            self.sim.data = {**original, flag: True}
            with self.assertRaisesRegex(ValueError, "confirmed game death"):
                mercy.undo_spend(self.session, self.save, entry)
            self.sim.data = original
        self.assertEqual(mercy.balance(self.session, self.save), 1)

    def test_new_death_record_is_protected(self):
        entry = self.spend()
        self.row("death", "Later death", {"sim_id": self.sim.id, "completed": False}, day=80)
        with self.assertRaisesRegex(ValueError, "new death record"):
            mercy.undo_spend(self.session, self.save, entry)
        self.assertEqual(mercy.balance(self.session, self.save), 1)

    def test_frozen_branch_and_other_save_are_protected(self):
        entry = self.spend()
        self.sim.data = {**self.sim.data, "infinite_frozen": True}
        with self.assertRaisesRegex(ValueError, "active branch"):
            mercy.undo_spend(self.session, self.save, entry)
        other = ChronicleSave(workspace_id=self.workspace.id, name="Other", settings={})
        self.session.add(other)
        self.session.flush()
        with self.assertRaisesRegex(ValueError, "active save"):
            mercy.undo_spend(self.session, other, entry)
        self.assertEqual(mercy.balance(self.session, self.save), 1)

    def test_foreign_record_in_snapshot_is_rejected(self):
        entry = self.spend()
        state = copy.deepcopy(entry.data["undo_state"])
        state["records"][0]["before"]["id"] = self.other.id
        entry.data = {**entry.data, "undo_state": state}
        with self.assertRaisesRegex(ValueError, "related death"):
            mercy.undo_spend(self.session, self.save, entry)
        self.assertEqual(mercy.balance(self.session, self.save), 1)

    def test_rescue_only_restores_its_own_sims_future_rolls(self):
        other_roll = self.row("roll", "Other Sim birthday", {"sim_id": self.other.id,
            "retired_by_death_roll_id": "danger", "completed": False}, day=40, deleted=True)
        entry = self.spend()
        self.assertTrue(other_roll.deleted)
        mercy.undo_spend(self.session, self.save, entry)
        self.assertTrue(other_roll.deleted)

    def test_manual_schedule_without_death_record_is_reversible(self):
        self.session.delete(self.death)
        self.sim.data = {key: value for key, value in self.sim.data.items() if key != "death_source_roll_id"}
        original = copy.deepcopy(self.sim.data)
        entry = self.spend()
        mercy.undo_spend(self.session, self.save, entry)
        self.assertEqual(self.sim.data, original)

    def test_older_use_recovers_exact_values_from_journal(self):
        entry = self.spend()
        entry.data = {key: value for key, value in entry.data.items() if key != "undo_state"}
        self.session.commit()
        result = mercy.undo_spend(self.session, self.save, entry)
        self.assertFalse(result["already_undone"])
        for row in (self.sim, self.death, self.roll):
            self.assertEqual(mercy._snapshot(row), self.before[row.id])

    def test_missing_legacy_history_refuses_without_refund(self):
        entry = self.spend()
        entry.data = {key: value for key, value in entry.data.items() if key != "undo_state"}
        for change in list(self.session.scalars(select(Change).where(Change.record_id == self.sim.id, Change.base_version == 0))):
            self.session.delete(change)
        with self.assertRaisesRegex(ValueError, "insufficient history"):
            mercy.undo_spend(self.session, self.save, entry)
        self.assertTrue(self.death.deleted)
        self.assertEqual(mercy.balance(self.session, self.save), 1)

    def test_legacy_use_that_also_changed_other_sims_rolls_is_refused(self):
        other_roll = self.row("roll", "Other Sim future roll", {"sim_id": self.other.id,
            "retired_by_death_roll_id": "danger", "completed": False}, day=50, deleted=True)
        entry = self.spend()
        # Reproduce the old spending order (no snapshot update at its end).
        for change in list(self.session.scalars(select(Change).where(
                Change.record_id == entry.id, Change.base_version > 0))):
            self.session.delete(change)
        entry.data = {key: value for key, value in entry.data.items() if key != "undo_state"}
        base = other_roll.version
        other_roll.deleted = False
        other_roll.data = {key: value for key, value in other_roll.data.items() if key not in mercy.RETIRE_FIELDS}
        other_roll.version += 1
        domain.journal(self.session, other_roll, "upsert", base)
        self.session.commit()
        with self.assertRaisesRegex(ValueError, "insufficient history"):
            mercy.undo_spend(self.session, self.save, entry)
        self.assertFalse(other_roll.deleted)
        self.assertEqual(mercy.balance(self.session, self.save), 1)

    def test_no_history_needed_for_new_snapshot_based_undo(self):
        entry = self.spend()
        for change in list(self.session.scalars(select(Change))):
            self.session.delete(change)
        self.session.commit()
        self.assertFalse(mercy.undo_spend(self.session, self.save, entry)["already_undone"])

    def test_endpoint_requires_matching_active_save_and_is_idempotent(self):
        entry = self.spend()
        @contextmanager
        def db():
            yield self.session
            self.session.commit()
        with patch.object(main, "db", db), patch.object(main, "context", return_value={"save": self.save}):
            client = TestClient(main.app)
            url = f"/api/save-a-sims/{entry.id}/undo"
            self.assertEqual(client.post(url, data={"save_id": "wrong"}).status_code, 409)
            response = client.post(url, data={"save_id": self.save.id}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/p/save-a-sims#ledger")
            response = client.post(url, data={"save_id": self.save.id}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
        self.assertEqual(mercy.balance(self.session, self.save), 2)


if __name__ == "__main__":
    unittest.main()
