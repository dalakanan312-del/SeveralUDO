from pathlib import Path
import os
import unittest
import uuid
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "sqlite:///./data/automated-tests.db"
os.environ["DECADES_SKIP_STARTUP_MIGRATIONS"] = "1"

from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.datastructures import FormData

from app import themes
from app.db import SessionLocal
from app.main import app, hogwarts_profile_theme
from app.models import ChronicleSave, Record


ROOT = Path(__file__).resolve().parents[1]


class ThemeTests(unittest.TestCase):
    def test_every_preset_has_readable_text_and_complete_css_variables(self):
        for key in themes.PRESETS:
            resolved = themes.resolve({"preset": key})
            self.assertGreaterEqual(resolved["contrast"], 4.5, key)
            self.assertIn("--panel-raised:", resolved["inline_style"])
            self.assertIn("--theme-heading-font:", resolved["inline_style"])
            self.assertIn(resolved["mode"], {"dark", "light"})

    def test_palette_library_includes_both_light_and_dark_choices(self):
        self.assertGreaterEqual(len(themes.PRESETS), 14)
        self.assertEqual(themes.resolve({"preset": "harbor"})["mode"], "dark")
        self.assertEqual(themes.resolve({"preset": "mist"})["mode"], "light")
        self.assertEqual(themes.resolve({"preset": "lavender"})["accent"], "#705a9b")

    def test_theme_css_tokenizes_legacy_named_surfaces_for_every_palette(self):
        css = (ROOT / "app" / "static" / "theme.css").read_text(encoding="utf-8")
        self.assertIn("Theme coverage bridge", css)
        self.assertIn('body[data-theme-mode] :is(.today-hero-date', css)
        self.assertIn('.storyline-current-year', css)
        self.assertNotIn('body[data-theme-mode="light"] :is(.today-hero-date', css)

    def test_sorted_hogwarts_sims_get_a_local_profile_house_theme(self):
        save = SimpleNamespace(settings={"selected_rule_packs": ["severaludo", "harry_potter_decades"]})
        sim = SimpleNamespace(data={"hp_hogwarts_house": " Ravenclaw "})
        theme = hogwarts_profile_theme(save, sim)
        self.assertEqual((theme["label"], theme["css_class"]), ("Ravenclaw", "hogwarts-house-ravenclaw"))
        self.assertIsNone(hogwarts_profile_theme(SimpleNamespace(settings={}), sim))
        self.assertIsNone(hogwarts_profile_theme(save, SimpleNamespace(data={"hp_hogwarts_house": ""})))
        css = (ROOT / "app" / "static" / "theme.css").read_text(encoding="utf-8")
        self.assertIn("hogwarts-house-gryffindor", css)
        self.assertIn("hogwarts-house-slytherin", css)

    def test_sorted_house_is_rendered_on_the_sim_profile(self):
        marker = uuid.uuid4().hex[:10]
        save_name = f"House theme {marker}"
        with TestClient(app) as client:
            client.post("/saves", data={"name": save_name, "start_year": "1991", "days_per_year": "4", "pregnancy_days": "4"}, follow_redirects=False)
            with SessionLocal() as session:
                save = session.scalar(select(ChronicleSave).where(ChronicleSave.name == save_name))
                save.settings = {**(save.settings or {}), "selected_rule_packs": ["harry_potter_decades"]}
                sim = Record(save_id=save.id, kind="sim", label="Raven Theme", global_day=1,
                             data={"birth_global_day": 1, "hp_hogwarts_house": "Ravenclaw"})
                session.add(sim)
                session.commit()
                sim_id, save_id = sim.id, save.id
            profile = client.get(f"/sims/{sim_id}")
            self.assertEqual(profile.status_code, 200)
            self.assertIn("hogwarts-profile hogwarts-house-ravenclaw", profile.text)
            self.assertIn("Ravenclaw · Hogwarts", profile.text)
            client.post(f"/saves/{save_id}/delete", data={"confirm": save_name}, follow_redirects=False)

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

    def test_daylight_preset_and_custom_light_mode_preserve_light_canvas(self):
        daylight = themes.resolve({"preset": "daylight"})
        self.assertEqual(daylight["mode"], "light")
        self.assertEqual(daylight["background"], themes.PRESETS["daylight"]["background"])
        custom = themes.resolve({
            "preset": "custom", "mode": "light", "accent": "#89601d",
            "background": "#f4f0e7", "surface": "#fffdf8", "text": "#292721", "muted": "#625e55",
        })
        self.assertEqual((custom["mode"], custom["background"]), ("light", "#f4f0e7"))
        self.assertFalse(custom["canvas_corrected"])
        submitted = themes.from_form(FormData([("theme_preset", "daylight"), ("theme_mode", "dark")]))
        self.assertEqual(submitted["mode"], "light")

    def test_form_preferences_are_normalized(self):
        result = themes.from_form(FormData([
            ("theme_preset", "custom"), ("theme_accent", "#abcdef"),
            ("theme_background", "#101010"), ("theme_surface", "#202020"),
            ("theme_text", "#ffffff"), ("theme_muted", "#bbbbbb"),
            ("theme_density", "compact"), ("theme_text_scale", "large"),
            ("theme_heading_style", "bookish"), ("theme_corners", "round"),
            ("theme_mode", "light"),
            ("theme_reduce_motion", "on"),
        ]))
        self.assertEqual((result["density"], result["text_scale"], result["corners"]), ("compact", "large", "round"))
        self.assertEqual(result["mode"], "light")
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
            saved_daylight = client.post("/appearance", data={"theme_preset": "daylight", "theme_mode": "dark"}, follow_redirects=False)
            self.assertEqual(saved_daylight.status_code, 303)
            daylight = client.get("/p/appearance")
            self.assertIn('data-theme-mode="light"', daylight.text)
            self.assertIn("#f4f0e7", daylight.text)
            client.post(f"/saves/{save_id}/delete", data={"confirm": save_name}, follow_redirects=False)


if __name__ == "__main__":
    unittest.main()
