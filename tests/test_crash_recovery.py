"""Crash/reload tests use isolated SQLite, never the installed game or save."""
import copy
import re
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select,func
from app import main,clock,domain,crash_recovery as recovery,usability,sync,backup_service
from app.models import ClockLink,ClockCheckpoint,Record,Change,ActionPreview,BackupSnapshot,ChronicleSave
from tests import test_infinite_decades as fixtures


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.InfiniteDecadesTests();self.f.setUp();self.s=self.f.session;self.save=self.f.save
        self.save.settings={**self.save.settings,'automation_enabled':False,'clock_game_day_high_watermark':20}
        for row in self.s.scalars(select(Record).where(Record.save_id==self.save.id)):
            domain.journal(self.s,row,'upsert',0)
        self.link=ClockLink(save_id=self.save.id,token_hash='test-recovery',enabled=True)
        self.s.add(self.link);self.s.commit();self.sequence=0
        self.binding=patch.object(main,'SessionLocal',self.f.sessions);self.binding.start();self.client=TestClient(main.app)
        self.client.post('/saves/select',data={'save_id':self.save.id},follow_redirects=False)
    def tearDown(self):self.client.close();self.binding.stop();self.f.tearDown()
    def report(self,day=20,hour=8,minute=0,**extra):
        self.sequence+=1
        report={'protocol_version':2,'report_sequence':self.sequence,'save_identity':'same-game',
                'game_day':day,'hour':hour,'minute':minute,'report_kind':'full','household_members':[],**extra}
        report['report_checksum']=clock.report_checksum(report)
        result=clock.receive(self.s,self.link,report);self.s.commit();return result,report
    def add(self,kind,label,**data):
        row=Record(save_id=self.save.id,kind=kind,label=label,global_day=self.save.global_day,data=data)
        self.s.add(row);self.s.flush();domain.journal(self.s,row,'upsert',0);self.s.commit();return row
    def change(self,row,**data):
        base=row.version;row.data={**row.data,**data};row.version+=1;domain.journal(self.s,row,'upsert',base);self.s.commit()
    def crash(self,same_day=True):
        self.report();self.report(day=20 if same_day else 21,hour=10)
        return self.report(hour=8,minute=30)[0]
    def form(self,**extra):
        return {'save_id':self.save.id,'recovery_id':recovery.state(self.save)['id'],**extra}
    def resolve(self,choice):
        response=self.client.post('/recovery/resolve',data=self.form(choice=choice),follow_redirects=False)
        self.s.expire_all();self.assertEqual(response.status_code,303,response.text[:500]);return response
    def preview(self):
        response=self.client.post('/recovery/preview',data=self.form())
        self.assertEqual(response.status_code,200,response.text[:700])
        token=re.search(r'/recovery/([a-f0-9]+)/confirm',response.text).group(1)
        return token,response
    def confirm(self,token):
        result=self.client.post('/recovery/'+token+'/confirm',data=self.form(),follow_redirects=False)
        self.s.expire_all();return result
    def test_same_day_rewind_pauses_before_importing_members(self):
        self.report(hour=10)
        self.save.settings={**self.save.settings,'automation_enabled':True};self.s.commit()
        with patch.object(clock,'sync_game_households',side_effect=AssertionError('must not import')):
            result,_=self.report(hour=8,household_members=[{'game_sim_id':'new-baby','is_baby':True}])
        self.assertEqual(result['status'],'recovery_hold');self.assertTrue(recovery.held(self.save))
        self.assertFalse(domain.automation_enabled(self.save));self.assertEqual(self.save.global_day,100)
    def test_previous_day_rewind_keeps_tracker_day_until_choice(self):
        self.crash(False);self.assertEqual(self.save.global_day,100) # master switch was off
        self.assertEqual(recovery.state(self.save)['target_day'],99)
    def test_game_day_zero_is_valid(self):
        self.report(day=0,hour=1);self.report(day=0,hour=0,minute=30)
        self.assertTrue(recovery.held(self.save));self.assertEqual(recovery.state(self.save)['restored_minute'],30)
    def test_duplicate_and_out_of_order_reports_do_not_trigger_recovery(self):
        _,old=self.report(hour=8);self.report(hour=10)
        result=clock.receive(self.s,self.link,old);self.s.commit()
        self.assertTrue(result['duplicate']);self.assertFalse(recovery.held(self.save));self.assertEqual(self.link.last_game_hour,10)
    def test_wrong_save_and_invalid_checksum_do_not_trigger_recovery(self):
        self.report(hour=10)
        result,_=self.report(hour=1,save_identity='other-save')
        self.assertEqual(result['reason'],'wrong_game_save');self.assertFalse(recovery.held(self.save))
        bad={'protocol_version':2,'report_sequence':20,'report_checksum':'wrong','game_day':1}
        self.assertEqual(clock.receive(self.s,self.link,bad)['reason'],'checksum_mismatch')
        self.assertFalse(recovery.held(self.save))
    def test_held_reports_advance_protocol_but_not_records_or_calendar(self):
        self.crash();identifier=recovery.state(self.save)['id']
        count=self.s.scalar(select(func.count()).select_from(Record))
        for hour in [9,10,11]:
            result,_=self.report(hour=hour,household_members=[{'game_sim_id':'new','is_pregnant':True}])
            self.assertEqual(result['status'],'recovery_hold')
        self.assertEqual(identifier,recovery.state(self.save)['id'])
        self.assertEqual(count,self.s.scalar(select(func.count()).select_from(Record)))
        self.assertEqual(self.save.global_day,100)
        protocol=clock._protocol_record(self.s,self.save);self.assertEqual(protocol.data['last_report_sequence'],self.sequence)
    def test_legacy_report_rewind_is_held_but_marked_uncertain(self):
        self.report(hour=10)
        result=clock.receive(self.s,self.link,{'game_day':20,'hour':8,'minute':1})
        self.s.commit();self.assertEqual(result['status'],'recovery_hold')
        self.assertFalse(recovery.state(self.save)['ordered'])
    def test_wait_keeps_rolls_until_original_time_and_full_report(self):
        self.report();self.change(self.f.roll,completed=True,actual=5,outcome='Passed');self.report(hour=10);self.report(hour=8,minute=30)
        self.resolve('wait');self.report(hour=9)
        self.assertTrue(recovery.held(self.save));self.assertTrue(self.f.roll.data['completed'])
        self.report(hour=10,report_kind='delta');self.assertTrue(recovery.held(self.save))
        self.report(hour=10,minute=10);self.assertFalse(recovery.held(self.save))
        self.assertTrue(self.f.roll.data['completed']);self.assertEqual(recovery.state(self.save)['resolution'],'kept_history')
        self.report(hour=11);self.assertEqual(recovery.state(self.save)['status'],'resolved')
    def test_realign_preserves_history_and_uses_fresh_anchor(self):
        self.crash(False);before=self.save.global_day
        self.resolve('keep');self.assertEqual(self.link.game_anchor_day,20);self.assertEqual(self.link.tracker_anchor_day,before)
        self.report(hour=9,report_kind='delta');self.assertTrue(recovery.held(self.save))
        self.report(hour=9,minute=10);self.assertFalse(recovery.held(self.save))
        self.assertEqual(self.save.global_day,before)
    def test_preview_is_read_only_and_rollback_reopens_roll_restores_pregnancy(self):
        pregnancy=self.add('pregnancy','Etheria pregnancy',status='Expecting',mother_id=self.f.people[0].id)
        self.report();self.change(pregnancy,status='Delivered',babies_delivered=2)
        self.change(self.f.roll,completed=True,actual=2,outcome='Failed')
        baby=self.add('sim','Unsaved baby',birth_global_day=100,pregnancy_id=pregnancy.id)
        self.change(self.f.people[0],death_confirmed=True,death_global_day=100)
        self.report(hour=10);self.report(hour=8,minute=30)
        token,response=self.preview();self.assertIn('Unsaved baby',response.text)
        self.s.refresh(pregnancy);self.assertEqual(pregnancy.data['status'],'Delivered');self.assertFalse(baby.deleted)
        self.assertEqual(self.s.scalar(select(func.count()).select_from(BackupSnapshot)),0)
        result=self.confirm(token);self.assertEqual(result.status_code,303,result.text[:1000])
        self.assertEqual(pregnancy.data['status'],'Expecting');self.assertFalse(self.f.roll.data['completed'])
        self.assertNotIn('actual',self.f.roll.data);self.assertTrue(baby.deleted)
        self.assertFalse(self.f.people[0].data.get('death_confirmed',False))
        self.assertTrue(recovery.held(self.save));self.assertEqual(self.f.roll.version,3)
        backup=self.s.get(BackupSnapshot,recovery.state(self.save)['backup_id']);self.assertTrue(backup.package)
        self.assertEqual(self.confirm(token).status_code,409)
    def test_new_unjournaled_candidate_is_archived(self):
        self.report();candidate=Record(save_id=self.save.id,kind='game_candidate',label='Unsaved birth',data={})
        self.s.add(candidate);self.s.commit();self.report(hour=10);self.report(hour=8,minute=30)
        token,_=self.preview();self.assertEqual(self.confirm(token).status_code,303);self.assertTrue(candidate.deleted)
    def test_missing_prior_history_disables_rollback(self):
        unknown=Record(save_id=self.save.id,kind='sim',label='Imported without history',data={})
        self.s.add(unknown);self.s.commit();self.report();self.change(unknown,death_confirmed=True)
        self.report(hour=10);self.report(hour=8,minute=30)
        page=self.client.get('/p/crash-recovery');self.assertIn('Earlier record state was not retained',page.text)
        self.assertEqual(self.client.post('/recovery/preview',data=self.form()).status_code,409)
    def test_missing_checkpoint_disables_rollback_but_allows_keep(self):
        self.link.last_game_day=20;self.link.last_game_hour=10;self.link.last_game_minute=0;self.s.commit()
        self.report(hour=8);page=self.client.get('/p/crash-recovery');self.assertIn('No earlier recovery checkpoint',page.text)
        self.resolve('wait')
    def test_calendar_change_blocks_rollback(self):
        self.crash();self.save.days_per_year=12;self.s.commit()
        self.assertEqual(self.client.post('/recovery/preview',data=self.form()).status_code,409)
    def test_relevant_record_edit_invalidates_preview(self):
        self.report();self.change(self.f.roll,completed=True);self.report(hour=10);self.report(hour=8,minute=30)
        token,_=self.preview();self.change(self.f.roll,outcome='Edited after preview')
        self.assertEqual(self.confirm(token).status_code,409);self.assertTrue(self.f.roll.data['completed'])
        self.assertEqual(self.s.scalar(select(func.count()).select_from(BackupSnapshot)),0)
    def test_more_held_reports_do_not_invalidate_preview(self):
        self.report();self.change(self.f.roll,completed=True);self.report(hour=10);self.report(hour=8,minute=30)
        token,_=self.preview();self.report(hour=9,report_kind='delta')
        self.assertEqual(self.confirm(token).status_code,303)
    def test_backup_failure_does_not_apply_rollback(self):
        self.report();self.change(self.f.roll,completed=True);self.report(hour=10);self.report(hour=8,minute=30)
        token,_=self.preview()
        with patch('app.backup_service.create_snapshot',side_effect=RuntimeError('backup unavailable')):
            with self.assertRaises(RuntimeError):self.confirm(token)
        self.s.expire_all();self.assertTrue(self.f.roll.data['completed']);self.assertFalse(self.s.get(ActionPreview,token).consumed)
    def test_expired_preview_cannot_apply(self):
        self.crash();token,_=self.preview();ticket=self.s.get(ActionPreview,token)
        ticket.created_at=datetime.now(timezone.utc)-timedelta(minutes=21);self.s.commit()
        self.assertEqual(self.confirm(token).status_code,409)
    def test_foreign_save_and_recovery_id_are_rejected(self):
        self.crash();data=self.form(choice='wait',save_id='not-owned')
        self.assertEqual(self.client.post('/recovery/resolve',data=data).status_code,404)
        self.assertEqual(self.client.post('/recovery/resolve',data=self.form(choice='wait',recovery_id='old')).status_code,409)
    def test_banner_and_clock_status_explain_hold(self):
        self.crash();status=usability.clock_status(self.save,self.link)
        self.assertEqual(status['state'],'recovery');self.assertTrue(status['recovery_required']);self.assertFalse(status['can_mark_paused'])
        page=self.client.get('/p/crash-recovery');self.assertEqual(page.status_code,200,page.text[:500])
        self.assertIn('Game time moved backward',page.text);self.assertIn('Review crash recovery',page.text)
    def test_checkpoints_are_bounded_in_frequency_and_do_not_copy_payloads(self):
        self.report(minute=0);self.report(minute=1);self.report(minute=9);self.report(minute=10)
        points=list(self.s.scalars(select(ClockCheckpoint).where(ClockCheckpoint.save_id==self.save.id)))
        self.assertEqual(len(points),2);self.assertEqual(len(points[0].configuration),64)
        self.assertNotIn('private',points[0].configuration)
    def test_second_crash_has_new_identity_and_new_checkpoint_epoch(self):
        self.crash();old=recovery.state(self.save)['id'];self.resolve('keep');self.report(hour=9)
        self.report(hour=11);self.report(hour=10)
        self.assertNotEqual(old,recovery.state(self.save)['id']);self.assertTrue(recovery.held(self.save))

    def test_another_earlier_reload_invalidates_the_old_preview(self):
        self.report(hour=7);self.report(hour=10);self.report(hour=8)
        token,_=self.preview();identifier=recovery.state(self.save)['id']
        self.report(hour=7,minute=30)
        self.assertNotEqual(identifier,recovery.state(self.save)['id'])
        self.assertEqual(self.confirm(token).status_code,409)

    def test_recovery_settings_are_local_and_backup_imports_wont_get_stuck(self):
        self.crash();local=copy.deepcopy(recovery.state(self.save))
        self.assertNotIn('clock_recovery',sync.ensure_save_metadata(self.s,self.save).data['settings'])
        self.assertNotIn('clock_recovery',backup_service.public_settings(self.save.settings))
        shadow=Record(save_id=self.save.id,kind='save_metadata',label='Remote',data={
            'global_day':100,'settings':{'clock_recovery':{'status':'review','id':'foreign'},'automation_enabled':False}})
        sync.materialize_special(self.s,self.save,shadow)
        self.assertEqual(recovery.state(self.save),local)

    def test_frozen_branch_rejects_resolution(self):
        self.crash()
        with patch('app.infinite_decades.import_allowed',return_value=False):
            self.assertEqual(self.client.post('/recovery/resolve',data=self.form(choice='keep')).status_code,409)

    def test_foreign_user_cannot_confirm_a_preview(self):
        self.crash();token,_=self.preview();ticket=self.s.get(ActionPreview,token)
        ticket.user_id='other-account';self.s.commit()
        self.assertEqual(self.confirm(token).status_code,404)

    def test_rollback_replayed_birth_is_detected_again_and_days_advance(self):
        self.save.settings={**self.save.settings,'automation_enabled':True};self.s.commit()
        self.report()
        baby={'game_sim_id':'new-baby-77','first_name':'Galia','last_name':'Black','is_baby':True}
        self.report(hour=10,household_members=[baby])
        self.report(hour=8,minute=30)
        token,_=self.preview();result=self.confirm(token)
        self.assertEqual(result.status_code,303,result.text[:600])
        result,_=self.report(hour=8,minute=40,household_members=[baby])
        self.assertFalse(recovery.held(self.save));self.assertEqual(result['new_candidates'],1)
        count=self.s.scalar(select(func.count()).select_from(Record).where(Record.save_id==self.save.id,
            Record.kind=='game_candidate',Record.deleted.is_(False),Record.data['source_key'].as_string()=='new_sim:new-baby-77'))
        self.assertEqual(count,1)
        self.report(day=21,hour=1);self.assertEqual(self.save.global_day,101)

    def test_record_deleted_after_checkpoint_is_restored(self):
        self.report();base=self.f.roll.version;self.f.roll.deleted=True;self.f.roll.version+=1
        domain.journal(self.s,self.f.roll,'delete',base);self.s.commit()
        self.report(hour=10);self.report(hour=8,minute=30)
        token,_=self.preview();self.assertEqual(self.confirm(token).status_code,303)
        self.assertFalse(self.f.roll.deleted)

    def test_unsupported_old_journal_shape_is_not_used_for_rollback(self):
        self.report()
        old=self.s.scalar(select(Change).where(Change.record_id==self.f.roll.id).order_by(Change.sequence.desc()).limit(1))
        old.payload={'legacy':'unrecognized'};self.s.commit()
        self.change(self.f.roll,completed=True);self.report(hour=10);self.report(hour=8,minute=30)
        response=self.client.post('/recovery/preview',data=self.form())
        self.assertEqual(response.status_code,409);self.assertIn('unsupported format',response.text)

    def test_another_branch_epoch_does_not_inherit_the_hold(self):
        self.crash();prior=recovery.epoch(self.save)
        with patch('app.crash_recovery.epoch',return_value=prior+'different-branch'):
            self.assertFalse(recovery.held(self.save))
            self.assertFalse(usability.clock_status(self.save,self.link)['recovery_required'])
            page=self.client.get('/p/crash-recovery');self.assertIn('No recovery is pending',page.text)


if __name__=='__main__':unittest.main()
