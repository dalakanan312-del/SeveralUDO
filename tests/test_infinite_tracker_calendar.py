"""Branch-relative tracker dates with a continuously running Sims clock."""
import copy
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import infinite_decades as dynasty, clock, main, crash_recovery
from app.models import ClockLink, Record, BackupSnapshot
from tests import test_infinite_decades as fixtures


class TrackerCalendarTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.InfiniteDecadesTests()
        self.f.setUp()
        self.f.save.settings={**self.f.save.settings,'automation_enabled':True}
        self.f.session.commit()
        self.link=ClockLink(save_id=self.f.save.id,token_hash='test-continuous-link',enabled=True)
        self.f.session.add(self.link)
        self.f.session.commit()
        self.sequence=0

    def tearDown(self): self.f.tearDown()

    def enable(self):
        result=dynasty.enable(self.f.session,self.f.save,[r.id for r in self.f.people[:4]],'Main line',1400,clock_mode='tracker')
        self.f.session.commit()
        return result

    def report(self, day, hour=12, members=None):
        self.sequence+=1
        report={'protocol_version':2,'report_sequence':self.sequence,'report_kind':'full',
                'game_day':day,'hour':hour,'minute':0,'household_sims':members or [],
                'game_edition':'sims4','save_identity':'continuous-game'}
        report['report_checksum']=clock.report_checksum(report)
        result=clock.receive(self.f.session,self.link,report)
        self.f.session.commit()
        return result,report

    def test_new_dynasty_needs_no_game_checkpoint_and_first_report_anchors(self):
        self.enable()
        self.assertTrue(dynasty.import_allowed(self.f.save))
        self.assertTrue(self.link.enabled)
        self.assertTrue(dynasty.state(self.f.save)['clock_anchor_pending'])
        result,_=self.report(500)
        self.assertTrue(result['ok'])
        self.assertEqual(self.f.save.global_day,100)
        self.assertEqual((self.link.game_anchor_day,self.link.tracker_anchor_day),(500,100))
        self.assertFalse(crash_recovery.held(self.f.save))
        self.report(501)
        self.assertEqual(self.f.save.global_day,101)

    def test_switch_backward_in_tracker_and_return_without_game_rewind(self):
        first=self.enable()
        self.report(500)
        child=self.f.capture()
        self.f.save.global_day=180
        self.f.session.commit()
        dynasty.activate_branch(self.f.session,self.f.save,child.id)
        self.f.session.commit()
        self.assertEqual(self.f.save.global_day,104)
        self.assertEqual(dynasty.metadata(first)['status'],'paused')
        self.assertEqual(dynasty.metadata(first)['current_global_day'],180)
        self.assertTrue(self.link.enabled)
        self.assertEqual(self.link.token_hash,'test-continuous-link')
        self.report(700)
        self.assertEqual(self.f.save.global_day,104)
        self.report(701)
        self.assertEqual(self.f.save.global_day,105)
        dynasty.activate_branch(self.f.session,self.f.save,first.id)
        self.f.session.commit()
        self.report(702)
        self.assertEqual(self.f.save.global_day,180)
        self.report(703)
        self.assertEqual(self.f.save.global_day,181)
        self.assertEqual(dynasty.metadata(child)['current_global_day'],105)
        self.assertFalse(crash_recovery.held(self.f.save))

    def test_sequence_and_identity_protection_survive_branch_switch(self):
        self.enable()
        _,old=self.report(500)
        child=self.f.capture()
        dynasty.activate_branch(self.f.session,self.f.save,child.id)
        self.f.session.commit()
        duplicate=clock.receive(self.f.session,self.link,old)
        self.assertTrue(duplicate['duplicate'])
        self.assertTrue(dynasty.state(self.f.save)['clock_anchor_pending'])
        wrong={**old,'report_sequence':2,'save_identity':'another-game'}
        wrong['report_checksum']=clock.report_checksum(wrong)
        self.assertEqual(clock.receive(self.f.session,self.link,wrong)['reason'],'wrong_game_save')
        self.assertEqual(self.f.save.global_day,104)
        self.report(501)
        self.assertEqual(self.f.save.global_day,104)

    def test_actual_game_rewind_still_requires_recovery(self):
        self.enable()
        self.report(500)
        self.report(501,13)
        result,_=self.report(501,12)
        self.assertEqual(result['status'],'recovery_hold')
        self.assertTrue(crash_recovery.held(self.f.save))
        self.assertEqual(self.f.save.global_day,101)

    def test_rewind_on_first_report_after_switch_is_not_hidden(self):
        self.enable()
        self.report(500,13)
        child=self.f.capture()
        dynasty.activate_branch(self.f.session,self.f.save,child.id)
        self.f.session.commit()
        result,_=self.report(500,12)
        self.assertEqual(result['status'],'recovery_hold')
        self.assertTrue(crash_recovery.held(self.f.save))
        self.assertTrue(dynasty.state(self.f.save)['clock_anchor_pending'])
        self.assertEqual(self.f.save.global_day,104)

    def test_converting_unconfirmed_classic_branch_reuses_existing_link(self):
        self.f.enable()
        self.assertFalse(self.link.enabled)
        dynasty.set_clock_mode(self.f.session,self.f.save,'tracker')
        self.f.session.commit()
        self.assertTrue(self.link.enabled)
        self.assertTrue(dynasty.import_allowed(self.f.save))
        self.report(700)
        self.assertEqual(self.f.save.global_day,100)
        dynasty.set_clock_mode(self.f.session,self.f.save,'checkpoints')
        self.f.session.commit()
        self.assertFalse(self.link.enabled)
        self.assertFalse(dynasty.import_allowed(self.f.save))
        self.assertEqual(self.f.save.global_day,100)

    def test_mode_change_preserves_day_records_and_pending_recovery(self):
        self.f.enable()
        dynasty.confirm_game(self.f.session,self.f.save)
        self.f.session.commit()
        self.link.enabled=True
        self.f.session.commit()
        self.report(500)
        self.report(500,11)
        self.assertTrue(crash_recovery.held(self.f.save))
        before=copy.deepcopy(crash_recovery.state(self.f.save))
        rows={r.id:(r.data,r.deleted,r.version) for r in self.f.session.scalars(select(Record).where(Record.kind.in_(['sim','dynasty_branch'])))}
        day=self.f.save.global_day
        dynasty.set_clock_mode(self.f.session,self.f.save,'tracker')
        self.f.session.commit()
        self.assertEqual(self.f.save.global_day,day)
        self.assertEqual(crash_recovery.state(self.f.save),before)
        self.assertTrue(crash_recovery.held(self.f.save))
        self.assertEqual(rows,{r.id:(r.data,r.deleted,r.version) for r in self.f.session.scalars(select(Record).where(Record.kind.in_(['sim','dynasty_branch'])))})
        self.assertIsNotNone(self.f.session.scalar(select(BackupSnapshot).where(BackupSnapshot.reason=='before-dynasty-calendar-mode')))

    def test_old_checkpoint_mode_keeps_its_existing_requirements(self):
        self.f.enable()
        child=self.f.capture()
        self.assertFalse(dynasty.tracker_calendar(self.f.save))
        self.assertFalse(self.link.enabled)
        with self.assertRaises(ValueError):
            dynasty.activate_branch(self.f.session,self.f.save,child.id)
        with self.assertRaises(ValueError):
            dynasty.capture(self.f.session,self.f.save,[self.f.people[1].id],'No checkpoint')

    def test_frozen_people_and_their_new_babies_do_not_cross_branches(self):
        self.enable()
        child=self.f.capture()
        dynasty.activate_branch(self.f.session,self.f.save,child.id)
        self.f.session.commit()
        incoming=[{'game_sim_id':'10','household_id':'77'},
                  {'game_sim_id':'12','household_id':'77'},
                  {'game_sim_id':'98','is_baby':True,'household_id':'77','parent_game_sim_ids':['10','11']},
                  {'game_sim_id':'99','is_baby':True,'household_id':'77','parent_game_sim_ids':['12']}]
        filtered=dynasty.filter_members(self.f.session,self.f.save,incoming)
        self.assertEqual([r['game_sim_id'] for r in filtered],['12','99'])
        snapshot=copy.deepcopy(self.f.people[0].data)
        self.report(500,members=incoming)
        self.assertEqual(self.f.people[0].data,snapshot)
        candidates=list(self.f.session.scalars(select(Record).where(Record.kind=='game_candidate')))
        self.assertTrue(any(r.data.get('source_key')=='new_sim:99' for r in candidates))
        self.assertFalse(any(r.data.get('source_key')=='new_sim:98' for r in candidates))

    def test_pause_and_finish_keep_connection_for_next_branch(self):
        self.enable()
        child=self.f.capture()
        self.report(500)
        dynasty.set_enabled(self.f.session,self.f.save,False)
        self.f.session.commit()
        self.assertTrue(self.link.enabled)
        self.assertEqual(self.report(550)[0]['status'],'paused')
        dynasty.set_enabled(self.f.session,self.f.save,True)
        self.f.session.commit()
        self.report(551)
        self.assertEqual(self.f.save.global_day,104)
        self.f.finish_modern()
        self.assertTrue(self.link.enabled)
        dynasty.activate_next(self.f.session,self.f.save)
        self.f.session.commit()
        self.report(600)
        self.assertEqual(self.f.save.global_day,104)
        self.assertEqual(dynasty.active_branch(self.f.session,self.f.save).id,child.id)

    def test_routes_and_forms_switch_without_checkpoint_confirmations(self):
        self.enable()
        child=self.f.capture()
        with patch.object(main,'SessionLocal',self.f.sessions),TestClient(main.app) as client:
            client.post('/saves/select',data={'save_id':self.f.save.id},follow_redirects=False)
            html=client.get('/p/infinite-decades').text
            self.assertIn('Tracker calendar',html)
            self.assertNotIn('name="current_game_save_name"',html)
            self.assertNotIn('name="checkpoint_confirmed"',html)
            self.assertNotIn('name="load_confirmed"',html)
            epoch=dynasty.state(self.f.save)['epoch']
            response=client.post('/infinite/'+self.f.save.id+'/play?_dynasty_epoch='+epoch,data={'branch_id':child.id},follow_redirects=False)
            self.assertEqual(response.status_code,200,response.text[:300])
            import re
            confirm_path=re.search(r'action="([^"]+/tools/confirm/[^"]+)"',response.text).group(1)
            response=client.post(confirm_path,headers={'X-Dynasty-Epoch':epoch},follow_redirects=False)
            self.assertEqual(response.status_code,303,response.text[:300])
            stale=client.post('/infinite/'+self.f.save.id+'/play?_dynasty_epoch='+epoch,data={'branch_id':child.id},follow_redirects=False)
            self.assertEqual(stale.status_code,409)
            self.assertIn('Tracker calendar',client.get('/p/today').text)

    def test_new_save_route_accepts_tracker_mode_without_save_as_fields(self):
        with patch.object(main,'SessionLocal',self.f.sessions),TestClient(main.app) as client:
            client.post('/saves/select',data={'save_id':self.f.save.id},follow_redirects=False)
            response=client.post('/infinite/'+self.f.save.id+'/enable',data={'clock_mode':'tracker',
                'sim_ids':[p.id for p in self.f.people[:4]],'label':'One game','modern_year':1400},follow_redirects=False)
            self.assertEqual(response.status_code,303,response.text[:200])
            self.f.session.expire_all()
            self.assertTrue(dynasty.tracker_calendar(self.f.save))
            self.assertTrue(self.link.enabled)
            self.assertEqual(self.f.save.global_day,100)


if __name__=='__main__':unittest.main()
