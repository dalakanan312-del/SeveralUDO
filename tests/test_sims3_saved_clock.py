"""Saved-clock decoding and monotonic challenge-day alignment regressions."""
import copy
import struct
import unittest
from unittest.mock import patch
from app import sims3_save as parser
from app.sims3_save_clock import apply_clock
from tests import test_sims3_save_reader as fixtures

text = fixtures.text


def clock_graph(ticks, copies=1, extra=b''):
    value=b'\x10'+struct.pack('<I',0)+struct.pack('<q',ticks)+extra
    index_at=28+len(value)*copies
    type_at=index_at+4*copies
    types=b'\0'+text('Sims3.Gameplay.Utilities.SimClockUtils')+b'\0'
    header=struct.pack('<7I',0x500,int.from_bytes(b'OBJS','little'),1,copies,type_at,index_at,type_at+len(types))
    offsets=b''.join(struct.pack('<I',28+i*len(value)) for i in range(copies))
    return parser.ObjectGraph(header+value*copies+offsets+types)


class ClockDecoderTests(unittest.TestCase):
    def test_user_verified_monday_0553(self):
        clock=parser.extract_clock(clock_graph(67273))
        self.assertEqual((clock['weekday'],clock['game_day'],clock['game_hour'],clock['game_minute']),('Monday',1,5,53))
        self.assertEqual(clock['game_second'],56)
        self.assertEqual(clock['week'],1)

    def test_midnight_sunday_and_week_boundary(self):
        for ticks, expected in [(0,('Sunday',0,0,0,1)),(53999,('Sunday',0,23,59,1)),
                                (54000,('Monday',1,0,0,1)),(378000,('Sunday',7,0,0,2))]:
            clock=parser.extract_clock(clock_graph(ticks))
            self.assertEqual(tuple(clock[k] for k in ('weekday','game_day','game_hour','game_minute','week')),expected)

    def test_missing_duplicate_negative_and_changed_serializer_rejected(self):
        for graph in [parser.ObjectGraph(fixtures.objs()),clock_graph(1,copies=2),clock_graph(-1),
                      clock_graph(1,extra=b'\0'),clock_graph(2**63-1)]:
            with self.assertRaises(parser.SaveReadError):parser.extract_clock(graph)


class ClockAlignmentTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.ImportTests.setUpClass.__func__)
    setUp=fixtures.ImportTests.setUp
    tearDown=fixtures.ImportTests.tearDown

    def scan(self,ticks=67273,world='test_0x00000000',stamp='2026-09-08T10:00:00+00:00'):
        scan=parser.inspect_save(self.folder)
        clock={**parser.extract_clock(clock_graph(ticks)),'world_key':world,'world_name':world,'saved_at':stamp}
        scan['saved_clock']=clock
        scan['slot'].update(clock)
        return scan

    def test_baseline_midnight_skipped_days_and_repeated_save(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave,ClockLink
        from sqlalchemy import select
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            self.assertEqual(apply_clock(session,save,self.scan())['advanced'],0)
            self.assertEqual(save.global_day,42)
            self.assertEqual(apply_clock(session,save,self.scan(108000))['advanced'],1)
            self.assertEqual(save.global_day,43)
            self.assertEqual(apply_clock(session,save,self.scan(9*54000))['advanced'],7)
            self.assertEqual(save.global_day,50)
            self.assertEqual(apply_clock(session,save,self.scan(9*54000))['advanced'],0)
            link=session.scalar(select(ClockLink).where(ClockLink.save_id==save.id))
            self.assertFalse(link.enabled) # no usable receiver was silently enabled
            self.assertEqual(link.last_game_day,9)
            self.assertEqual(link.tracker_anchor_day,50)

    def test_intraday_rollback_and_older_mtime_rejected(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            apply_clock(session,save,self.scan())
            for scan in [self.scan(67272),self.scan(70000,stamp='2026-09-07T10:00:00+00:00')]:
                before=copy.deepcopy(save.settings)
                with self.assertRaises(parser.SaveReadError):apply_clock(session,save,scan)
                self.assertEqual(save.settings,before)
                self.assertEqual(save.global_day,42)

    def test_new_town_reanchors_and_old_town_watermark_is_retained(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            apply_clock(session,save,self.scan(10*54000))
            apply_clock(session,save,self.scan(54000,world='newtown'))
            self.assertEqual(save.global_day,42)
            self.assertIn('Town/save change',save.settings['sims3_save_clock']['status'])
            apply_clock(session,save,self.scan(108000,world='newtown'))
            self.assertEqual(save.global_day,43)
            with self.assertRaises(parser.SaveReadError):apply_clock(session,save,self.scan(9*54000))
            apply_clock(session,save,self.scan(11*54000))
            self.assertEqual(save.global_day,43) # returning is a new alignment, not catch-up

    def test_manual_day_change_and_pause_epoch_reanchor(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            apply_clock(session,save,self.scan(),reset_id='a')
            save.global_day=80
            apply_clock(session,save,self.scan(3*54000),reset_id='a')
            self.assertEqual(save.global_day,80)
            apply_clock(session,save,self.scan(4*54000),reset_id='a')
            self.assertEqual(save.global_day,81)
            apply_clock(session,save,self.scan(8*54000),reset_id='b')
            self.assertEqual(save.global_day,81)

    def test_clock_off_and_missing_clock_cannot_advance(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            self.reader.apply_scan(session,save,self.scan(),{'123'})
            self.assertEqual(save.global_day,42)
            self.assertNotIn('sims3_save_clock',save.settings)
            result=self.reader.apply_scan(session,save,parser.inspect_save(self.folder),{'123'},sync_clock=True)
            self.assertEqual(result['advanced'],0)
            self.assertNotIn('sims3_save_clock',save.settings)

    def test_toggle_rescans_same_file_and_manual_relay_cannot_double_advance(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave,ClockLink
        from app import clock
        from sqlalchemy import select
        scan=self.scan()
        self.reader.configure(self.save_id,'Test.sims3',True,sync_clock=False)
        with patch.object(parser,'inspect_save',return_value=scan):self.reader.tick()
        self.reader.configure(self.save_id,'Test.sims3',True,sync_clock=True)
        with patch.object(parser,'inspect_save',return_value=scan):self.reader.tick()
        state=self.reader.status(self.save_id)
        self.assertTrue(state.get('detected_clock'))
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            self.assertEqual(save.settings['sims3_save_clock']['ticks'],67273)
            self.assertEqual(save.global_day,42)
            link=session.scalar(select(ClockLink).where(ClockLink.save_id==save.id))
            response=clock.receive(session,link,{'game_day':999,'game_edition':'sims3'})
            self.assertEqual(response['reason'],'sims3_clock_retired')
            self.assertEqual(save.global_day,42)

    def test_first_clock_update_preserves_existing_link_credentials(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave,ClockLink
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            link=ClockLink(save_id=save.id,token_hash='test-'+self.save_id,enabled=True)
            session.add(link);session.flush()
            token=link.token_hash
            apply_clock(session,save,self.scan())
            self.assertEqual(link.token_hash,token)
            self.assertTrue(link.enabled)

    def test_master_pause_resets_clock_epoch(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave
        self.reader.configure(self.save_id,'Test.sims3',True,sync_clock=True)
        with patch.object(parser,'inspect_save',return_value=self.scan()):self.reader.tick()
        old=self.reader.status(self.save_id)['clock_reset_id']
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            save.settings={**save.settings,'automation_enabled':False};session.commit()
        self.reader.tick()
        self.assertNotEqual(old,self.reader.status(self.save_id)['clock_reset_id'])
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            save.settings={**save.settings,'automation_enabled':True};session.commit()
        with patch.object(parser,'inspect_save',return_value=self.scan(5*54000)):self.reader.tick()
        with SessionLocal() as session:
            self.assertEqual(session.get(ChronicleSave,self.save_id).global_day,42)

    def test_failed_reconciliation_rolls_back_clock_and_marker(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave
        from app import save_scanner
        self.reader.configure(self.save_id,'Test.sims3',True,sync_clock=True)
        with patch.object(parser,'inspect_save',return_value=self.scan()), patch.object(save_scanner,'reconcile_scan',side_effect=ValueError('test failure')):
            self.reader.tick()
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            self.assertEqual(save.global_day,42)
            self.assertNotIn('sims3_save_clock',save.settings)
        self.assertNotIn('last_fingerprint',self.reader.status(self.save_id))


if __name__=='__main__':unittest.main()
