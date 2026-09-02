from __future__ import annotations

import os
import unittest
import uuid

os.environ["DATABASE_URL"] = "sqlite:///./data/automated-tests.db"
os.environ["DECADES_SKIP_STARTUP_MIGRATIONS"] = "1"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import save_a_sims
from app.db import Base, SessionLocal
from app.main import app
from app.models import ChronicleSave, Record, Workspace


class SaveASimTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)

    def test_death_milestones_and_all_scheduled_award_once(self):
        with Session(self.engine) as session:
            workspace = Workspace(id="save-a-sim-workspace", name="Save-a-Sim test")
            save = ChronicleSave(id="save-a-sim-save", workspace_id=workspace.id, name="Test", global_day=40)
            session.add_all([workspace, save])
            for number in range(10):
                session.add(Record(id=f"dead-{number}", save_id=save.id, kind="sim", label=f"Dead {number}",
                                   data={"death_confirmed": True, "death_global_day": number + 1}))
            pending = Record(id="scheduled", save_id=save.id, kind="sim", label="Scheduled", data={"death_global_day": 50})
            session.add(pending)
            session.flush()

            first = save_a_sims.sync_automatic_awards(session, save)
            self.assertEqual(len(first["created"]), 2)  # ten deaths + all active Sims scheduled
            self.assertEqual(save_a_sims.balance(session, save), 2)
            second = save_a_sims.sync_automatic_awards(session, save)
            self.assertEqual(second["created"], [])
            self.assertEqual(len(save_a_sims.credit_entries(session, save)), 2)

    def test_matching_rule_and_spend_are_auditable_and_restore_retired_rolls(self):
        with Session(self.engine) as session:
            workspace = Workspace(id="save-a-sim-workspace-2", name="Save-a-Sim test")
            save = ChronicleSave(id="save-a-sim-save-2", workspace_id=workspace.id, name="Test", global_day=30)
            sim = Record(id="saved-sim", save_id=save.id, kind="sim", label="Anne", data={
                "death_global_day": 31, "death_source_roll_id": "danger-roll", "cause_of_death": "Fever",
            })
            death = Record(id="saved-death", save_id=save.id, kind="death", label="Death of Anne", global_day=31,
                           data={"sim_id": sim.id, "completed": False, "source_roll_id": "danger-roll"})
            retired = Record(id="retired-roll", save_id=save.id, kind="roll", label="Anne — Adult", global_day=40,
                             deleted=True, data={"sim_id": sim.id, "retired_by_death_roll_id": "danger-roll", "completed": False})
            rule = Record(id="rescue-rule", save_id=save.id, kind=save_a_sims.RULE_KIND, label="Heroic rescue", data={
                "active": True, "trigger_type": "roll_result", "match_roll": "heroic", "match_outcome": "survives",
                "amount": 1, "repeatable": False,
            })
            source_roll = Record(id="source-roll", save_id=save.id, kind="roll", label="Anne — Heroic intervention", global_day=30,
                                 data={"completed": True, "roll_type": "Heroic intervention", "outcome": "Survives the danger"})
            session.add_all([workspace, save, sim, death, retired, rule, source_roll])
            session.flush()

            created = save_a_sims.award_matching_roll_rules(session, save, source_roll)
            self.assertEqual(len(created), 1)
            self.assertEqual(save_a_sims.balance(session, save), 1)
            self.assertEqual(save_a_sims.award_matching_roll_rules(session, save, source_roll), [])
            result = save_a_sims.spend_on_sim(session, save, sim, "Rule intervention")
            self.assertEqual((result["removed_deaths"], result["restored_rolls"]), (1, 1))
            # The first spend also evaluates the all-current-Sims-scheduled
            # condition, which grants its own one-time credit before the
            # scheduled death is withdrawn.
            self.assertEqual(save_a_sims.balance(session, save), 1)
            self.assertIsNone((sim.data or {}).get("death_global_day"))
            self.assertTrue(death.deleted)
            self.assertFalse(retired.deleted)
            self.assertEqual(len(list(session.scalars(select(Record).where(Record.kind == save_a_sims.CREDIT_KIND)))), 3)

    def test_workspace_page_is_available_from_challenge_navigation(self):
        marker = uuid.uuid4().hex[:10]
        name = f"Save-a-Sim page {marker}"
        with TestClient(app) as client:
            created = client.post("/saves", data={
                "name": name, "start_year": "1300", "days_per_year": "4", "pregnancy_days": "4",
            }, follow_redirects=False)
            self.assertEqual(created.status_code, 303)
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).where(ChronicleSave.name == name))
                save_id = save.id
            try:
                page = client.get("/p/save-a-sims")
                self.assertEqual(page.status_code, 200)
                self.assertIn("Save-a-Sims", page.text)
                self.assertIn("RULE-SPECIFIC CONDITIONS", page.text)
            finally:
                client.post(f"/saves/{save_id}/delete", data={"confirm": name}, follow_redirects=False)


if __name__ == "__main__":
    unittest.main()
