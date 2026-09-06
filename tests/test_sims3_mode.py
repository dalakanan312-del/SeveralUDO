import io
import json
import types
import unittest
from zipfile import ZipFile

from app import clock, clock_bundle, game_modes
from app.db import SessionLocal
from app.main import app
from app.models import ChronicleSave, ClockLink, Record
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
import uuid


class _EmptySession:
    def scalar(self, _statement):
        return None


class Sims3ModeTests(unittest.TestCase):
    def test_legacy_saves_default_to_sims4(self):
        self.assertEqual(game_modes.for_save(types.SimpleNamespace(settings={}))['id'], 'sims4')
        self.assertEqual(game_modes.normalize('The Sims 3'), 'sims3')
        self.assertEqual(game_modes.normalize('unexpected value'), 'sims4')

    def test_sims3_bundle_contains_its_automatic_package_separate_from_ts4_mod(self):
        package = clock_bundle.build_bundle(game_mode='sims3')
        with ZipFile(io.BytesIO(package)) as archive:
            names = set(archive.namelist())
            config = json.loads(archive.read('SeveralUDOSims3ClockSync/config-template.json'))
        self.assertIn('SeveralUDOSims3ClockSync/SeveralUDOSims3ClockSync.package', names)
        self.assertIn('SeveralUDOSims3ClockSync/Report Sims 3 Clock Now.ps1', names)
        self.assertIn('SeveralUDOSims3ClockSync/README - Install Sims 3 Clock Sync.txt', names)
        self.assertNotIn('SeveralUDOSims3ClockSync/SeveralUDOClockSync.ts4script', names)
        self.assertEqual(config['game_edition'], 'sims3')
        self.assertIn('sims3_save_identity', config)

    def test_receiver_rejects_the_other_game_edition_before_mutation(self):
        save = types.SimpleNamespace(id='save-1', settings={'game_mode': 'sims3'})
        _, _, result = clock._protocol_gate(_EmptySession(), save, {
            'protocol_version': 1,
            'report_sequence': 1,
            'game_edition': 'sims4',
        })
        self.assertEqual(result['reason'], 'wrong_game_edition')
        self.assertTrue(result['permanent_rejection'])

    def test_new_sims3_save_gets_sims3_screen_and_private_clock_config(self):
        marker = uuid.uuid4().hex[:10]
        name = f'Sims 3 mode {marker}'
        save_id = ''
        with TestClient(app) as client:
            try:
                created = client.post('/saves', data={
                    'name': name, 'start_year': '1300', 'days_per_year': '4',
                    'pregnancy_days': '4', 'game_mode': 'sims3',
                }, follow_redirects=False)
                self.assertEqual(created.status_code, 303)
                with SessionLocal() as session:
                    save = session.scalar(select(ChronicleSave).where(ChronicleSave.name == name))
                    self.assertIsNotNone(save)
                    save_id = save.id
                    self.assertEqual(save.settings['game_mode'], 'sims3')
                page = client.get('/p/clock')
                self.assertEqual(page.status_code, 200)
                self.assertIn('Sims 3 Clock Sync', page.text)
                self.assertIn('automatic Sims 3 clock package', page.text)
                configured = client.post('/downloads/clock-sync/configured')
                self.assertEqual(configured.status_code, 200)
                with ZipFile(io.BytesIO(configured.content)) as package:
                    config = json.loads(package.read('SeveralUDOSims3ClockSync/config.json'))
                self.assertEqual(config['game_edition'], 'sims3')
                accepted = client.post('/api/clock/report', headers={'Authorization': f"Bearer {config['sync_token']}"}, json={
                    'protocol_version': 1, 'report_sequence': 1, 'game_edition': 'sims3',
                    'save_identity': 'Sims3-slot', 'game_day': 4, 'hour': 12, 'minute': 0,
                    'household_members': [],
                })
                self.assertEqual(accepted.status_code, 200)
                rejected = client.post('/api/clock/report', headers={'Authorization': f"Bearer {config['sync_token']}"}, json={
                    'protocol_version': 1, 'report_sequence': 2, 'game_edition': 'sims4',
                    'save_identity': 'Sims3-slot', 'game_day': 5, 'hour': 12, 'minute': 0,
                    'household_members': [],
                })
                self.assertEqual(rejected.json()['reason'], 'wrong_game_edition')
            finally:
                if save_id:
                    with SessionLocal() as session:
                        session.execute(delete(Record).where(Record.save_id == save_id))
                        session.execute(delete(ClockLink).where(ClockLink.save_id == save_id))
                        session.execute(delete(ChronicleSave).where(ChronicleSave.id == save_id))
                        session.commit()


    def test_sims3_clock_sync_sweeps_the_loaded_town_and_marks_a_complete_population(self):
        source = (clock_bundle.SIMS3_BRIDGE_ROOT / 'SeveralUDOClockSync-Sims3-Source.cs').read_text(encoding='utf-8')
        relay = (clock_bundle.SIMS3_BRIDGE_ROOT / 'SeveralUDOClockRelay.ps1').read_text(encoding='utf-8')
        self.assertEqual(clock_bundle.SIMS3_CLOCK_SYNC_VERSION, '1.1.0')
        self.assertIn('Household.EverySimDescription()', source)
        self.assertIn('population_complete', source)
        self.assertIn('population_scope', source)
        self.assertIn('new AlarmTimerCallback(WriteGameSnapshot), 30f, TimeUnit.Minutes', source)
        self.assertIn('report_kind = if ($hasRichSnapshot) { "full" } else { "clock" }', relay)
        self.assertIn('population_scope = "town"', relay)
        self.assertIn('population_complete = $hasRichSnapshot', relay)


if __name__ == '__main__':
    unittest.main()

