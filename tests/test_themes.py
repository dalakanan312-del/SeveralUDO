from pathlib import Path
import os
import unittest
import uuid

os.environ["DATABASE_URL"] = "sqlite:///./data/automated-tests.db"
os.environ["DECADES_SKIP_STARTUP_MIGRATIONS"] = "1"

from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.datastructures import FormData

from app import themes
from app.db import SessionLocal
from app.main import app
from app.models import ChronicleSave


ROOT = Path(__file__).resolve().parents[1]


class ThemeTests(unittest.TestCase):
    def test_every_preset_has_readable_text_and_complete_css_variables(self):
        for key in themes.PRESETS:
            resolved = themes.resolve({"preset": key})
            self.assertGreaterEqual(resolved["contrast"], 4.5, key)
            self.assertIn("--panel-raised:", resolved["inline_style"])
            self.assertIn("--theme-heading-font:", resolved["inline_style"])
            self.assertIn(resolved["mode"], {"dark", "light"})

    def test_custom_theme_rejects_css_injection_and_corrects_contrast(self):
        resolved = themes.resolve({
            "preset": "custom", "accent": "#123456;position:fixed", "background": "#ffffff",
            "surface": "#ffffff", "text": "#222222", "muted": "not-a-color",
        })
        self.assertEqual(resolved["accent"], themes.PRESETS["heirloom"]["accent"])
        self.assertTrue(resolved["text_corrected"])
        self.assertTrue(resolved["canvas_corrected"])
        self.assertEqual(resolved["mode"], "dark")
        self.assertGreaterEqual(resolved["contrast"], 4.5)
        self.assertNotIn("position", resolved["inline_style"])

    def test_form_preferences_are_normalized(self):
        result = themes.from_form(FormData([
            ("theme_preset", "custom"), ("theme_accent", "#abcdef"),
            ("theme_background", "#101010"), ("theme_surface", "#202020"),
            ("theme_text", "#ffffff"), ("theme_muted", "#bbbbbb"),
            ("theme_density", "compact"), ("theme_text_scale", "large"),
            ("theme_heading_style", "bookish"), ("theme_corners", "round"),
            ("theme_reduce_motion", "on"),
        ]))
        self.assertEqual((result["density"], result["text_scale"], result["corners"]), ("compact", "large", "round"))
        self.assertTrue(result["reduce_motion"])

    def test_appearance_is_a_first_class_page(self):
        main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        base = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        page = (ROOT / "app" / "templates" / "appearance.html").read_text(encoding="utf-8")
        self.assertIn('"appearance": ("Appearance"', main)
        self.assertIn('"appearance":"appearance.html"', main)
        self.assertIn('/static/theme.css', base)
        self.assertIn('id="appearance-editor"', page)
        self.assertIn('action="/appearance/reset"', page)

    def test_appearance_page_saves_and_applies_custom_theme(self):
        marker = uuid.uuid4().hex[:10]
        save_name = f"Theme test {marker}"
        with TestClient(app) as client:
            client.post("/saves", data={"name": save_name, "start_year": "1300", "days_per_year": "4", "pregnancy_days": "4"}, follow_redirects=False)
            page = client.get("/p/appearance")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Choose a palette", page.text)
            saved = client.post("/appearance", data={
                "theme_preset": "custom", "theme_accent": "#3b82f6",
                "theme_background": "#101827", "theme_surface": "#1f2937",
                "theme_text": "#f9fafb", "theme_muted": "#cbd5e1",
                "theme_density": "compact", "theme_text_scale": "large",
                "theme_heading_style": "modern", "theme_corners": "round",
                "theme_reduce_motion": "on",
            }, follow_redirects=False)
            self.assertEqual(saved.status_code, 303)
            applied = client.get("/p/appearance")
            self.assertIn("--gold:#3b82f6", applied.text)
            self.assertIn('data-theme-density="compact"', applied.text)
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).where(ChronicleSave.name == save_name))
                self.assertEqual(save.settings["visual_theme"]["heading_style"], "modern")
                save_id = save.id
            reset = client.post("/appearance/reset", follow_redirects=False)
            self.assertEqual(reset.status_code, 303)
            client.post(f"/saves/{save_id}/delete", data={"confirm": save_name}, follow_redirects=False)


if __name__ == "__main__":
    unittest.main()
