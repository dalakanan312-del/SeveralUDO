import os
import unittest
import uuid

os.environ["DATABASE_URL"] = "sqlite:///./data/automated-tests.db"
os.environ["DECADES_SKIP_STARTUP_MIGRATIONS"] = "1"

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import telemetry, university
from app.db import SessionLocal
from app.main import app
from app.models import ChronicleSave, Record


class UniversityTests(unittest.TestCase):
    def test_dashboard_combines_tracker_and_game_evidence(self):
        save = ChronicleSave(id="save", workspace_id="workspace", name="Test", global_day=20, start_year=1300, days_per_year=4)
        sim = Record(id="sim", save_id="save", kind="sim", label="Ada Scholar", data={
            "game_careers": [{"name": "University Student", "title": "History Student", "performance": 82, "level": 2}],
            "game_degrees": ["History"],
        })
        enrollment = Record(id="enrollment", save_id="save", kind="university_enrollment", label="Ada Scholar — History", global_day=10, data={
            "sim_id": sim.id, "sim_name": sim.label, "degree": "History", "status": "Enrolled",
            "credits_earned": 3, "credits_required": 12,
        })
        term = Record(id="term", save_id="save", kind="university_term", label="Ada Scholar — Term 2", global_day=20, data={
            "enrollment_id": enrollment.id, "sim_id": sim.id, "term_number": 2, "status": "In progress",
            "start_global_day": 14, "end_global_day": 20, "performance": 76,
        })
        result = university.dashboard([sim, enrollment, term], save)
        self.assertEqual(result["stats"]["active"], 1)
        self.assertEqual(result["stats"]["due"], 1)
        self.assertEqual(result["rows"][0]["credit_percent"], 25)
        self.assertEqual(result["rows"][0]["detected_performance"]["performance"], 82)
        self.assertEqual(result["completed_degrees"][0]["degrees"], ["History"])

    def test_term_result_applies_credit_only_once_and_graduates(self):
        enrollment = Record(id="enrollment", save_id="save", kind="university_enrollment", label="Student — History", data={
            "status": "Enrolled", "credits_earned": 9, "credits_required": 12,
        })
        term = Record(id="term", save_id="save", kind="university_term", label="Student — Term 4", data={
            "status": "In progress", "credits_applied": 0,
        })
        first = university.apply_term_result(enrollment, term, status="Completed", performance=91, grade="A", gpa=4,
                                             credits_earned=3, end_global_day=40)
        self.assertEqual(first["enrollment_data"]["credits_earned"], 12)
        self.assertTrue(first["graduated"])
        self.assertTrue(first["graduated_transition"])
        enrollment.data = first["enrollment_data"]
        term.data = first["term_data"]
        second = university.apply_term_result(enrollment, term, status="Completed", performance=91, grade="A", gpa=4,
                                              credits_earned=3, end_global_day=40)
        self.assertEqual(second["enrollment_data"]["credits_earned"], 12)
        self.assertFalse(second["graduated_transition"])

    def test_university_page_enrollment_term_today_and_graduation(self):
        marker = uuid.uuid4().hex[:10]
        save_name = f"University test {marker}"
        with TestClient(app) as client:
            client.post("/saves", data={"name": save_name, "start_year": "1300", "days_per_year": "4", "pregnancy_days": "4"}, follow_redirects=False)
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).where(ChronicleSave.name == save_name))
                sim = Record(save_id=save.id, kind="sim", label="Beatrice Bookish", global_day=1, data={
                    "first_name": "Beatrice", "last_name": "Bookish", "birth_global_day": 1,
                    "game_careers": [{"name": "University Student", "title": "Language Student", "performance": 88}],
                })
                session.add(sim); session.commit(); sim_id = sim.id; save_id = save.id
            created = client.post("/api/university/enrollments", data={
                "sim_id": sim_id, "institution": "Britechester", "degree": "Language and Literature",
                "status": "Enrolled", "start_global_day": "1", "credits_required": "3", "credits_earned": "0",
            }, follow_redirects=False)
            self.assertEqual(created.status_code, 303)
            with SessionLocal() as session:
                enrollment = session.scalar(select(Record).where(Record.save_id == save_id, Record.kind == "university_enrollment"))
                enrollment_id = enrollment.id
                save = session.get(ChronicleSave, save_id); sim = session.get(Record, sim_id)
                snapshot = {"careers": [{"name": "University Student", "title": "Language Student", "performance": 88}]}
                telemetry.capture_sim_changes(session, save, sim, snapshot, dict(sim.data or {}))
                telemetry.capture_sim_changes(session, save, sim, snapshot, dict(sim.data or {}))
                session.commit()
                checkpoints = list(session.scalars(select(Record).where(Record.save_id == save_id, Record.kind == "university_performance")))
                self.assertEqual(len(checkpoints), 1)
                self.assertEqual(checkpoints[0].data["performance"], 88)
            term_created = client.post(f"/api/university/enrollments/{enrollment_id}/terms", data={
                "term_number": "1", "start_global_day": "1", "end_global_day": "1", "status": "In progress",
                "courses": "Language\nHistory", "credits_attempted": "3",
            }, follow_redirects=False)
            self.assertEqual(term_created.status_code, 303)
            page = client.get("/p/university")
            self.assertEqual(page.status_code, 200)
            self.assertIn("University & performance", page.text)
            self.assertIn("Beatrice Bookish", page.text)
            today = client.get("/p/today?task=university&due=today")
            self.assertEqual(today.status_code, 200)
            self.assertIn("TERM REVIEW", today.text)
            with SessionLocal() as session:
                term = session.scalar(select(Record).where(Record.save_id == save_id, Record.kind == "university_term"))
                term_id = term.id
            completed = client.post(f"/api/university/terms/{term_id}", data={
                "status": "Completed", "end_global_day": "1", "courses": "Language\nHistory",
                "credits_attempted": "3", "credits_earned": "3", "performance": "94", "grade": "A", "gpa": "4.0",
                "return_to": "/p/today?task=university",
            }, follow_redirects=False)
            self.assertEqual(completed.status_code, 303)
            with SessionLocal() as session:
                enrollment = session.get(Record, enrollment_id)
                self.assertEqual(enrollment.data["status"], "Graduated")
                self.assertEqual(enrollment.data["credits_earned"], 3)
            client.post(f"/saves/{save_id}/delete", data={"confirm": save_name}, follow_redirects=False)


if __name__ == "__main__":
    unittest.main()
