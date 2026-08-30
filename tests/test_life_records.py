from __future__ import annotations

import os
import unittest
import uuid

os.environ["DATABASE_URL"] = "sqlite:///./data/automated-tests.db"
os.environ["DECADES_SKIP_STARTUP_MIGRATIONS"] = "1"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from app import automation, life_records
from app.db import Base, SessionLocal
from app.main import app
from app.models import Change, ChronicleSave, Record, Workspace


class LifeRecordsTests(unittest.TestCase):
    def save(self, **values):
        defaults = dict(id="save-life", workspace_id="workspace-life", name="Cooley Decades",
                        global_day=100, start_year=1300, days_per_year=4, settings={})
        defaults.update(values)
        return ChronicleSave(**defaults)

    def record(self, kind, label, *, record_id, day=None, data=None):
        return Record(id=record_id, save_id="save-life", kind=kind, label=label,
                      global_day=day, data=data or {})

    def test_planned_marriage_receives_deterministic_dowry_estimate(self):
        save = self.save()
        home = self.record("household", "Cooley House", record_id="home", data={"social_class":"Gentry", "funds":20000})
        first = self.record("sim", "Anne Cooley", record_id="anne", day=1,
                            data={"birth_global_day":1, "parent_ids":["p1","p2"], "current_household_id":"home"})
        sibling = self.record("sim", "John Cooley", record_id="john", day=20,
                              data={"birth_global_day":20, "parent_ids":["p1","p2"], "current_household_id":"home"})
        second = self.record("sim", "Robert Hale", record_id="robert", day=1, data={"birth_global_day":1})
        courtship = self.record("relationship", "Anne & Robert", record_id="court", day=90,
                                data={"partner1_id":"anne", "partner2_id":"robert", "type":"Courtship",
                                      "status":"Active", "suggested_marriage_global_day":104})
        result = life_records.build([home, first, sibling, second, courtship], save)
        row = result["dowries"]["rows"][0]
        self.assertEqual(row["first"].id, "anne")
        self.assertGreater(row["suggested"], 0)
        self.assertIn("birth order 1", row["method"])

    def test_contradictions_explain_impossible_dates(self):
        save = self.save()
        parent = self.record("sim", "Young Parent", record_id="parent", day=20, data={"birth_global_day":20})
        child = self.record("sim", "Older Child", record_id="child", day=10,
                            data={"birth_global_day":10, "death_global_day":5, "mother_id":"parent"})
        issues = life_records.build([parent, child], save)["contradictions"]
        titles = {issue["title"] for issue in issues}
        self.assertIn("Death before birth", titles)
        self.assertIn("Parent date conflict", titles)

    def test_newspaper_stops_at_selected_year_and_uses_recorded_facts(self):
        save = self.save(global_day=9, start_year=1600, days_per_year=4)
        birth = self.record("sim", "Anne Cooley", record_id="anne", day=5, data={"birth_global_day":5})
        future = self.record("event", "Future war", record_id="future", day=9, data={})
        issue = life_records.annual_newspaper(save, [birth, future], 1601)
        self.assertEqual(issue["year"], 1601)
        self.assertIn("Anne Cooley", issue["body"])
        self.assertNotIn("Future war", issue["body"])
        self.assertEqual(issue["source_record_ids"], ["anne"])

    def test_law_and_disorder_and_grief_signals_wait_for_inbox_review_once(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Workspace(id="workspace-life", name="Life test"))
            save = self.save(global_day=40)
            sim = self.record("sim", "Marcus Hale", record_id="marcus", day=1,
                              data={"birth_global_day":1, "game_sim_id":"game-marcus"})
            session.add_all([save, sim]);session.flush()
            snapshot = {
                "health_buffs":[{"name":"Arrested by police"}, {"name":"Grieving the death of a loved one"}],
                "detected_optional_mods":["Law and Disorder"], "detected_tracker_global_day":40,
            }
            automation.reconcile_sim(session, save, sim, snapshot)
            automation.reconcile_sim(session, save, sim, snapshot)
            candidates = list(session.scalars(select(Record).where(Record.kind=="game_candidate")))
            actions = [(item.data or {}).get("action") for item in candidates]
            self.assertEqual(actions.count("legal_signal"), 1)
            self.assertEqual(actions.count("grief_detected"), 1)
            legal = next(item for item in candidates if item.data["action"]=="legal_signal")
            self.assertEqual(legal.data["payload"]["source_mod"], "Law and Disorder")

    def test_accepted_automation_can_be_safely_undone(self):
        marker = uuid.uuid4().hex[:10]
        with TestClient(app) as client:
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).order_by(ChronicleSave.updated_at.desc()))
                sim = Record(save_id=save.id, kind="sim", label=f"Undo Sim {marker}", data={"birth_global_day":1})
                session.add(sim);session.flush()
                candidate = Record(save_id=save.id, kind="game_candidate", label=f"Legal review {marker}", global_day=save.global_day,
                                   data={"action":"legal_signal", "sim_id":sim.id, "status":"pending",
                                         "source_key":f"test-legal:{marker}", "payload":{"signal_label":"Arrested", "source_mod":"Law and Disorder"}})
                session.add(candidate);session.commit();candidate_id,sim_id=candidate.id,sim.id
            accepted = client.post(f"/automation/{candidate_id}/accept", data={"offense":"Poaching", "case_status":"Charged"}, follow_redirects=False)
            self.assertEqual(accepted.status_code, 303)
            with SessionLocal() as session:
                candidate=session.get(Record,candidate_id);legal_id=candidate.data["resolved_record_id"]
                self.assertEqual(candidate.data["status"],"accepted")
                self.assertFalse(session.get(Record,legal_id).deleted)
            undone = client.post(f"/api/automation/{candidate_id}/undo", follow_redirects=False)
            self.assertEqual(undone.status_code,303)
            with SessionLocal() as session:
                self.assertEqual(session.get(Record,candidate_id).data["status"],"undone")
                self.assertTrue(session.get(Record,legal_id).deleted)
                session.execute(delete(Change).where(Change.record_id.in_([candidate_id,sim_id,legal_id])))
                session.execute(delete(Record).where(Record.id.in_([candidate_id,sim_id,legal_id])))
                session.commit()


if __name__ == "__main__":
    unittest.main()
