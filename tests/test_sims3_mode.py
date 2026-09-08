"""Sims 3 remains a tracker mode, with saved-file support instead of Clock Sync."""
import io
import json
from pathlib import Path
import types
import unittest
import uuid
from unittest.mock import patch
from zipfile import ZipFile
from app import clock, clock_bundle, game_modes, main
from app.db import SessionLocal
from app.models import ChronicleSave, ClockLink, Record
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

class _EmptySession:
    def scalar(self, _statement): return None

class Sims3ModeTests(unittest.TestCase):
    def test_legacy_saves_default_to_sims4(self):
        self.assertEqual(game_modes.for_save(types.SimpleNamespace(settings={}))['id'],'sims4')
        self.assertEqual(game_modes.normalize('The Sims 3'),'sims3')
        self.assertEqual(game_modes.normalize('unexpected value'),'sims4')

    def test_sims3_bundle_config_and_components_are_withdrawn(self):
        self.assertFalse(clock_bundle.bundle_details('sims3')['available'])
        for action in (lambda:clock_bundle.build_bundle(game_mode='sims3'),
                       lambda:clock_bundle.config_document(game_mode='sims3'),
                       lambda:clock_bundle.bridge_file('script','sims3')):
            with self.assertRaisesRegex(ValueError,'withdrawn'):action()
        self.assertNotIn("('clock_bridge_sims3', 'clock_bridge_sims3')",Path('Decades Tracker.spec').read_text())
        self.assertIn('clock_bridge_sims3',Path('.dockerignore').read_text())

    def test_sims4_bundle_is_still_complete(self):
        with ZipFile(io.BytesIO(clock_bundle.build_bundle())) as archive:
            self.assertIn('SeveralUDOClockSync/SeveralUDOClockSync.ts4script',archive.namelist())
            config=json.loads(archive.read('SeveralUDOClockSync/config-template.json'))
            self.assertEqual(config['game_edition'],'sims4')

    def test_receiver_rejects_the_other_game_edition_before_mutation(self):
        save=types.SimpleNamespace(id='save-1',settings={'game_mode':'sims3'})
        _,_,result=clock._protocol_gate(_EmptySession(),save,{'protocol_version':1,'report_sequence':1,'game_edition':'sims4'})
        self.assertEqual(result['reason'],'wrong_game_edition')
        self.assertTrue(result['permanent_rejection'])

    def test_new_sims3_save_explains_retirement_and_preserves_existing_records(self):
        save_id=''
        with TestClient(main.app) as client:
            try:
                name='Sims 3 retirement '+uuid.uuid4().hex
                response=client.post('/saves',data={'name':name,'start_year':'1300','days_per_year':'4','pregnancy_days':'4','game_mode':'sims3'},follow_redirects=False)
                self.assertEqual(response.status_code,303)
                with SessionLocal() as session:
                    save=session.scalar(select(ChronicleSave).where(ChronicleSave.name==name))
                    save_id=save.id
                    from app.auth import hash_secret
                    token='retired-test-'+uuid.uuid4().hex
                    link=ClockLink(save_id=save_id,token_hash=hash_secret(token),enabled=True,last_game_day=3)
                    session.add(link);session.commit()
                    before=(save.global_day,save.revision,link.token_hash,link.last_game_day)
                page=client.get('/p/clock')
                self.assertEqual(page.status_code,200)
                self.assertIn('Sims 3 Clock Sync — withdrawn',page.text)
                self.assertIn("I couldn't figure out how to get Clock Sync working reliably",page.text)
                self.assertIn('desktop version reads completed Sims 3 save data as a supplement',page.text)
                self.assertIn('Read Sims 3 saves automatically',page.text)
                self.assertNotIn('/downloads/clock-sync',page.text)
                self.assertNotIn('name="capture_portraits"',page.text)
                # Exercise the hosted branch using the same authenticated test
                # context; no real hosted database or filesystem is accessed.
                original=main.context
                def hosted_context(*args,**kwargs):
                    result=original(*args,**kwargs);result['local_mode']=False;return result
                with patch.object(main,'context',side_effect=hosted_context):
                    hosted=client.get('/p/clock')
                self.assertEqual(hosted.status_code,200)
                self.assertIn('Download the desktop tracker',hosted.text)
                self.assertNotIn('name="sync_clock"',hosted.text)
                # Page reads can initialize unrelated save metadata. Measure
                # the retired write endpoints from the post-render state.
                with SessionLocal() as session:
                    save=session.get(ChronicleSave,save_id)
                    link=session.scalar(select(ClockLink).where(ClockLink.save_id==save_id))
                    before=(save.global_day,save.revision,link.token_hash,link.last_game_day)
                for path in ('/downloads/clock-sync/configured','/api/clock/links','/api/clock/reanchor','/api/clock/trust-next-save'):
                    self.assertEqual(client.post(path).status_code,410,path)
                for component in ('','/script','/relay','/starter','/self-test','/updater','/updater-starter','/reporter','/safe-sync','/config-template','/instructions','/troubleshooting'):
                    self.assertEqual(client.get('/downloads/clock-sync'+component+'?game_mode=sims3').status_code,410,component)
                headers={'Authorization':'Bearer '+token}
                self.assertEqual(client.get('/api/clock/ping',headers=headers).status_code,410)
                report=client.post('/api/clock/report',headers=headers,json={'game_edition':'sims3','game_day':999,'household_members':[{'game_sim_id':'should-not-import','name':'No import'}]}).json()
                self.assertEqual(report['reason'],'sims3_clock_retired')
                self.assertTrue(report['permanent_rejection'])
                with SessionLocal() as session:
                    save=session.get(ChronicleSave,save_id)
                    link=session.scalar(select(ClockLink).where(ClockLink.save_id==save_id))
                    self.assertEqual((save.global_day,save.revision,link.token_hash,link.last_game_day),before)
            finally:
                if save_id:
                    with SessionLocal() as session:
                        session.execute(delete(Record).where(Record.save_id==save_id))
                        session.execute(delete(ClockLink).where(ClockLink.save_id==save_id))
                        session.execute(delete(ChronicleSave).where(ChronicleSave.id==save_id))
                        session.commit()

if __name__=='__main__':unittest.main()







