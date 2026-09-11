"""Private settings, date calculations and paid-request safeguards on disposable data."""
import base64
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
from cryptography.fernet import Fernet
from sqlalchemy import select

from app import domain, main, portrait_studio as studio, portraits, sync
from app.models import Portrait, PortraitProviderSetting, Record, User, UiPreference, Workspace, ChronicleSave
from tests import test_infinite_decades as fixtures


def photo_bytes():
    output = io.BytesIO()
    Image.new("RGBA", (64, 64), (70, 90, 110, 255)).save(output, format="PNG")
    return output.getvalue()


BASE_CONFIG = {"provider": "manual", "comfyui_url": "http://127.0.0.1:8188", "openai_api_key": "", "openai_image_model": "gpt-image-1"}


class PortraitStudioTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.InfiniteDecadesTests(); self.f.setUp()
        self.f.save.start_year = 1400; self.f.save.days_per_year = 12; self.f.save.global_day = 400
        self.f.save.settings = {**self.f.save.settings, "automation_enabled": False}
        self.sim = self.f.people[0]
        self.sim.data = {**self.sim.data, "birth_global_day": 1, "game_age_stage": "youngadult", "birth_country": "England"}
        self.original = Portrait(save_id=self.f.save.id, record_id=self.sim.id, stage="youngadult", source="tray-library-game", image=photo_bytes(), mime_type="image/png")
        self.f.session.add(self.original); self.f.session.commit()
        self.config_patch = patch.object(portraits, "effective_config", return_value=dict(BASE_CONFIG)); self.config_patch.start()
        self.cipher_patch = patch.object(studio, "_cipher", return_value=Fernet(Fernet.generate_key())); self.cipher_patch.start()
        self.sessions_patch = patch.object(main, "SessionLocal", self.f.sessions); self.sessions_patch.start()
        self.client = TestClient(main.app)
        self.client.post('/saves/select', data={'save_id': self.f.save.id}, follow_redirects=False)

    def tearDown(self):
        self.client.close(); self.sessions_patch.stop(); self.config_patch.stop(); self.cipher_patch.stop(); self.f.tearDown()

    def enable(self):
        studio.save_configuration(self.f.session, self.f.user.id, {"provider": "openai", "enabled": "on", "openai_api_key": "sk-test-private-not-real", "openai_image_model": "gpt-image-1"})
        self.f.session.commit()

    def form(self):
        return {"source_id": self.original.id, "stage": "Young Adult", "year": "1418", "style": "painted", "place": "England", "notes": "A merchant family", "request_id": uuid4().hex}

    def csrf(self, page="/p/ai-settings"):
        response = self.client.get(page)
        self.assertEqual(response.status_code, 200, response.text[:400])
        return re.search(r'name="csrf" value="([^"]+)"', response.text)[1]

    def queue(self, form=None):
        row, created = studio.queue_job(self.f.session, self.f.save, self.f.user.id, self.sim.id, form or self.form())
        self.f.session.commit()
        return row, created

    def test_year_matches_four_and_twelve_day_calendars(self):
        for days in (4, 12):
            self.f.save.days_per_year = days
            result = studio.stage_date(self.f.session, self.f.save, self.sim, "Young Adult")
            self.assertEqual(result["year"], 1418)
            self.assertEqual(result["day"], 1 + 18 * days)
        self.assertEqual(studio.stage_date(self.f.session, self.f.save, self.sim, "Infant")["day"], 4)

    def test_custom_aging_rule_is_used_instead_of_standard_offset(self):
        self.f.session.add(Record(save_id=self.f.save.id, kind="roll_rule", label="Young Adult", data={"age_days": 300, "active": True}))
        self.f.session.flush()
        result = studio.stage_date(self.f.session, self.f.save, self.sim, "youngadult")
        self.assertEqual((result["year"], result["day"], result["basis"]), (1425, 301, "Calculated from aging rules"))

    def test_completed_aging_date_is_not_replaced_by_current_schedule(self):
        self.f.session.add(Record(save_id=self.f.save.id, kind="roll", label="Old stage date", global_day=121,
            data={"sim_id": self.sim.id, "completed": True, "roll_type": "Young Adult", "source": "aging:old", "lifecycle_age_days": 120}))
        self.f.session.flush()
        result = studio.stage_date(self.f.session, self.f.save, self.sim, "Young Adult")
        self.assertEqual((result["year"], result["basis"]), (1410, "Recorded aging check"))

    def test_birth_year_only_and_missing_dates_are_explicit(self):
        self.sim.data = {"birth_global_day": None, "birth_year": 1390, "birth_year_only": True}
        self.assertEqual(studio.stage_date(self.f.session, self.f.save, self.sim, "Young Adult")["year"], 1408)
        self.sim.data = {"birth_global_day": None}
        result = studio.stage_date(self.f.session, self.f.save, self.sim, "Young Adult")
        self.assertIsNone(result["year"]); self.assertEqual(result["basis"], "Year needed")

    def test_keys_are_private_encrypted_and_do_not_appear_in_html(self):
        self.enable()
        row = self.f.session.get(PortraitProviderSetting, self.f.user.id)
        self.assertNotIn("sk-test", row.encrypted_key)
        self.assertNotIn("sk-test", json.dumps(row.config))
        self.assertIsNone(self.f.session.get(UiPreference, self.f.user.id))
        self.assertEqual(studio.configuration(self.f.session, self.f.user.id)["openai_api_key"], "sk-test-private-not-real")
        page = self.client.get('/p/ai-settings')
        self.assertNotIn("sk-test", page.text)
        self.assertIn("Image generation is on", page.text)
        self.assertIn("Key saved", page.text)

    def test_switch_can_disable_without_forgetting_key_and_clear_key(self):
        self.enable()
        studio.save_configuration(self.f.session, self.f.user.id, {"provider": "openai"})
        config = studio.configuration(self.f.session, self.f.user.id)
        self.assertFalse(config['enabled']); self.assertTrue(config['openai_api_key'])
        studio.save_configuration(self.f.session, self.f.user.id, {"provider": "openai", "enabled": "on", "clear_key": "on"})
        self.assertFalse(studio.public_configuration(studio.configuration(self.f.session, self.f.user.id))["ready"])

    def test_another_account_does_not_inherit_my_personal_key(self):
        self.enable()
        other = User(email="other@test.invalid"); self.f.session.add(other); self.f.session.flush()
        self.assertFalse(studio.configuration(self.f.session, other.id)["openai_api_key"])

    def test_online_visitors_cannot_spend_the_deployment_owners_api_credits(self):
        with patch.object(studio, 'settings', SimpleNamespace(local_mode=False)), patch.object(portraits, 'effective_config', return_value={**BASE_CONFIG, 'provider':'openai', 'openai_api_key':'deployment-private'}):
            config = studio.configuration(self.f.session, self.f.user.id)
            self.assertFalse(config['enabled']); self.assertEqual(config['openai_api_key'], '')
            self.enable()
            config = studio.configuration(self.f.session, self.f.user.id)
            self.assertTrue(config['enabled']); self.assertEqual(config['openai_api_key'], 'sk-test-private-not-real')

    def test_invalid_or_hosted_local_provider_is_rejected(self):
        for url in ("https://example.com", "http://169.254.169.254", "http://user:password@localhost:8188"):
            with self.assertRaises(ValueError):
                studio.save_configuration(self.f.session, self.f.user.id, {"provider": "comfyui", "comfyui_url": url})
        with patch.object(studio, "settings", SimpleNamespace(local_mode=False)):
            with self.assertRaisesRegex(ValueError, "desktop"):
                studio.save_configuration(self.f.session, self.f.user.id, {"provider": "comfyui"})

    def test_disabled_generation_never_reaches_provider(self):
        with patch.object(portraits, "generate_references") as provider:
            with self.assertRaisesRegex(ValueError, "Enable AI"):
                self.queue()
            provider.assert_not_called()

    def test_missing_reference_and_foreign_reference_are_rejected(self):
        self.enable()
        for source in ("missing", self.f.session.scalar(select(Portrait.id).where(Portrait.record_id != self.sim.id))):
            with self.assertRaisesRegex(ValueError, "belonging"):
                self.queue({**self.form(), "source_id": source})

    def test_changed_year_is_marked_as_player_entered_and_does_not_edit_sim(self):
        self.enable(); original = dict(self.sim.data)
        row, _ = self.queue({**self.form(), "year": "1430"})
        self.assertEqual(row.data['date']['year'], 1430)
        self.assertEqual(row.data['date']['basis'], 'Player-entered year')
        self.assertEqual(self.sim.data, original)

    def test_success_creates_separate_gallery_image_and_sync_metadata(self):
        self.enable(); row, _ = self.queue()
        original = self.original.image
        with patch.object(portraits, "generate_references", return_value=photo_bytes()) as provider:
            studio.run_job(row.id, self.f.sessions)
        self.f.session.expire_all(); row = self.f.session.get(Record, row.id)
        self.assertEqual(row.data['status'], 'complete')
        self.assertEqual(self.f.session.get(Portrait, self.original.id).image, original)
        generated = self.f.session.scalar(select(Portrait).where(Portrait.record_id == row.id))
        self.assertEqual((generated.stage, generated.source), ('generated', 'ai-historical'))
        shadow = self.f.session.scalar(select(Record).where(Record.kind == 'portrait_blob', Record.data['record_id'].as_string() == row.id))
        self.assertIsNotNone(shadow)
        self.assertEqual(studio.gallery(self.f.session, self.f.save.id, self.sim.id)[0].id, row.id)
        self.assertIn('1418', provider.call_args.args[1]); self.assertNotIn('sk-test', row.data['prompt'])

    def test_same_request_and_repeated_worker_do_not_charge_twice(self):
        self.enable(); form = self.form()
        first, created = self.queue(form); second, again = self.queue(form)
        self.assertTrue(created); self.assertFalse(again); self.assertEqual(first.id, second.id)
        with patch.object(portraits, 'generate_references', return_value=photo_bytes()) as provider:
            studio.run_job(first.id, self.f.sessions); studio.run_job(first.id, self.f.sessions)
            provider.assert_called_once()

    def test_failed_provider_preserves_original_and_redacts_error(self):
        self.enable(); row, _ = self.queue()
        with patch.object(portraits, 'generate_references', side_effect=RuntimeError('Secret sk-test-private-not-real')):
            studio.run_job(row.id, self.f.sessions)
        self.f.session.expire_all(); row = self.f.session.get(Record, row.id)
        self.assertEqual(row.data['status'], 'failed'); self.assertNotIn('sk-test', json.dumps(row.data))
        self.assertIsNone(self.f.session.scalar(select(Portrait).where(Portrait.record_id == row.id)))
        self.assertEqual(self.f.session.get(Portrait, self.original.id).image, photo_bytes())

    def test_changed_source_or_disabled_provider_stops_queued_job(self):
        self.enable(); row, _ = self.queue()
        self.original.image = b'changed'; self.f.session.commit()
        with patch.object(portraits, 'generate_references') as provider:
            studio.run_job(row.id, self.f.sessions); provider.assert_not_called()
        self.f.session.expire_all()
        self.assertIn('source photo changed', self.f.session.get(Record, row.id).data['error'])

    def test_settings_form_persists_on_off_and_needs_csrf(self):
        token = self.csrf()
        form = {"csrf": token, "provider": "openai", "enabled": "on", "openai_api_key": "sk-test-private-not-real"}
        response = self.client.post('/portrait-studio/settings', data=form)
        self.assertIn('Image generation is on', response.text)
        response = self.client.post('/portrait-studio/settings', data={"csrf": token, "provider": "openai"})
        self.assertIn('Image generation is off', response.text)
        self.assertEqual(self.client.post('/portrait-studio/settings', data={"provider": "openai"}).status_code, 403)

    def test_generation_route_and_source_are_authenticated_and_work(self):
        self.enable()
        token = self.csrf('/p/portrait-studio?sim_id=' + self.sim.id)
        with patch.object(portraits, 'generate_references', return_value=photo_bytes()) as provider:
            response = self.client.post(f'/portrait-studio/sims/{self.sim.id}/generate', data={**self.form(), "csrf": token})
        self.assertEqual(response.status_code, 200, response.text[:300])
        self.assertIn('AI INTERPRETATION · 1418', response.text); provider.assert_called_once()
        self.assertEqual(self.client.get('/portrait-studio/sources/' + self.original.id).content, photo_bytes())
        self.f.session.expire_all(); row = studio.gallery(self.f.session, self.f.save.id, self.sim.id)[0]
        self.assertEqual(self.client.get('/portrait-studio/jobs/' + row.id).json()['status'], 'complete')
        response = self.client.post('/portrait-studio/jobs/' + row.id + '/remove', data={"csrf": token})
        self.assertIn('archived', response.text)
        self.f.session.expire_all(); self.assertTrue(self.f.session.get(Record, row.id).deleted)
        self.assertIsNotNone(self.f.session.get(Portrait, self.original.id))

    def test_gallery_and_settings_navigation_and_profile_link(self):
        response = self.client.get('/p/portrait-studio?sim_id=' + self.sim.id)
        self.assertEqual(response.status_code, 200)
        self.assertIn('1418', response.text); self.assertIn('Open AI settings', response.text)
        self.assertIn('Original kept safe', response.text)
        profile = self.client.get('/sims/' + self.sim.id)
        self.assertEqual(profile.status_code, 200)
        self.assertIn('Generate historical portrait', profile.text)
        self.assertIn('AI settings', profile.text)
        self.assertIn('portrait-studio', {p for group in main.NAVIGATION_GROUPS for p in group['pages']})
        self.assertIn('ai-settings', {p for group in main.NAVIGATION_GROUPS for p in group['pages']})

    def test_connection_check_does_not_generate(self):
        self.enable(); token = self.csrf()
        with patch('openai.OpenAI') as api:
            response = self.client.post('/portrait-studio/test', data={'csrf': token})
            api.return_value.__enter__.return_value.models.retrieve.assert_called_once_with('gpt-image-1')
            api.return_value.__enter__.return_value.images.edit.assert_not_called()
        self.assertIn('No image was generated', response.text)

    def test_reference_api_upload_is_named_png_and_retries_are_disabled(self):
        with patch('openai.OpenAI') as api:
            api.return_value.__enter__.return_value.images.edit.return_value = SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(photo_bytes()).decode())])
            config = {**BASE_CONFIG, 'provider': 'openai', 'enabled': True, 'openai_api_key': 'test-key'}
            result = portraits.generate_references([photo_bytes()], 'Historical portrait', config)
            self.assertEqual(result, photo_bytes())
            self.assertEqual(api.call_args.kwargs['max_retries'], 0)
            args = api.return_value.__enter__.return_value.images.edit.call_args.kwargs
            self.assertEqual(args['n'], 1); self.assertEqual(args['input_fidelity'], 'high')
            self.assertEqual((args['image'][0][0], args['image'][0][2]), ('reference-0.png', 'image/png'))
            self.assertTrue(args['image'][0][1].startswith(b'\x89PNG'))

    def test_four_pending_jobs_limit_and_existing_request_still_idempotent(self):
        self.enable(); first_form = self.form()
        self.queue(first_form)
        for _ in range(3): self.queue()
        self.assertFalse(self.queue(first_form)[1])
        with self.assertRaisesRegex(ValueError, 'Four portraits'):
            self.queue()

    def test_source_and_gallery_from_another_workspace_are_not_visible(self):
        workspace = Workspace(name='Unrelated workspace'); self.f.session.add(workspace); self.f.session.flush()
        save = ChronicleSave(workspace_id=workspace.id, name='Private'); self.f.session.add(save); self.f.session.flush()
        person = Record(save_id=save.id, kind='sim', label='Private person', data={}); self.f.session.add(person); self.f.session.flush()
        photo = Portrait(save_id=save.id, record_id=person.id, stage='default', source='upload', image=photo_bytes(), mime_type='image/png')
        self.f.session.add(photo); self.f.session.commit()
        self.assertEqual(self.client.get('/portrait-studio/sources/'+photo.id).status_code, 404)
        self.assertEqual(self.client.get('/p/portrait-studio?sim_id='+person.id).status_code, 404)
        self.enable()
        with self.assertRaisesRegex(ValueError, 'belonging'):
            self.queue({**self.form(), 'source_id': photo.id})

    def test_switching_ai_off_before_worker_starts_stops_the_job(self):
        self.enable(); row, _ = self.queue()
        studio.save_configuration(self.f.session, self.f.user.id, {'provider':'openai'})
        self.f.session.commit()
        with patch.object(portraits, 'generate_references') as provider:
            studio.run_job(row.id, self.f.sessions); provider.assert_not_called()
        self.f.session.expire_all()
        self.assertEqual(self.f.session.get(Record, row.id).data['status'], 'failed')

    def test_desktop_encryption_does_not_use_the_development_cookie_secret(self):
        self.cipher_patch.stop()
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(portraits, '_config_path', return_value=Path(folder)/'portrait-provider.json'), patch.object(studio, 'settings', SimpleNamespace(local_mode=True, session_secret='development-only-change-me')):
                encrypted = studio._cipher().encrypt(b'private')
                self.assertEqual(studio._cipher().decrypt(encrypted), b'private')
                self.assertTrue((Path(folder)/'portrait-key.secret').is_file())
            with patch.object(studio, 'settings', SimpleNamespace(local_mode=False, session_secret='development-only-change-me')):
                with self.assertRaisesRegex(ValueError, 'SESSION_SECRET'): studio._cipher()


if __name__ == '__main__':
    unittest.main()
