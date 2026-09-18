import asyncio
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import automation, labor
from app.db import Base
from app.models import ChronicleSave, Record, Workspace


def snapshot(day=10, hour=22, minute=30, **values):
    return {"detected_game_day":day,"detected_game_hour":hour,"detected_game_minute":minute,
        "is_pregnant":True,"is_in_labor":True,"labor_scan_supported":True,**values}


class LaborTimingTests(unittest.TestCase):
    def test_cross_midnight_uses_game_time_not_year_length(self):
        start=labor.transition({},snapshot(),1,"a")
        end=labor.transition(start,snapshot(day=11,hour=2,minute=5,is_pregnant=False),1,"a")
        self.assertEqual(end["minutes"],215)
        self.assertEqual(labor.summary({"labor_tracking":end}),"3h 35m · Estimated")
        self.assertEqual(labor.clock_label(start["start"]),"Game day 10 · 22:30")

    def test_missing_start_is_not_backfilled_from_pregnancy_days(self):
        self.assertEqual(labor.transition({},snapshot(is_in_labor=False),1,"a"),{})
        self.assertEqual(labor.transition({},snapshot(is_pregnant=False),1,"a"),{})
        self.assertEqual(labor.transition({},snapshot(labor_scan_supported=False),1,"a"),{})

    def test_missing_timestamps_never_assume_midnight(self):
        for key in ('detected_game_day','detected_game_hour','detected_game_minute'):
            value=snapshot();value.pop(key)
            self.assertIsNone(labor.point(value))
            self.assertEqual(labor.transition({},value,1,"a"),{})
        self.assertIsNone(labor.point(snapshot(hour=24)))
        self.assertIsNone(labor.point(snapshot(minute=True)))

    def test_paused_and_repeated_reports_do_not_extend_or_restart(self):
        start=labor.transition({},snapshot(),1,"a")
        self.assertEqual(labor.transition(start,snapshot(),1,"a"),start)
        self.assertEqual(labor.transition(start,snapshot(day=11),1,"a"),start)
        end=labor.transition(start,snapshot(day=11,is_pregnant=False),1,"a")
        self.assertEqual(labor.transition(end,snapshot(day=12,is_pregnant=False),1,"a"),end)

    def test_missing_or_disappearing_labor_buff_is_not_delivery(self):
        start=labor.transition({},snapshot(),1,"a")
        self.assertEqual(labor.transition(start,snapshot(day=11,is_in_labor=False),1,"a"),start)
        value=snapshot(day=11,is_in_labor=False);value.pop('is_pregnant')
        self.assertEqual(labor.transition(start,value,1,"a"),start)

    def test_later_pregnancy_gets_own_interval(self):
        end=labor.transition(labor.transition({},snapshot(),1,"a"),snapshot(day=11,is_pregnant=False),1,"a")
        next_start=labor.transition(end,snapshot(day=20),2,"a")
        self.assertEqual(next_start['cycle'],2)
        self.assertNotIn('end',next_start)
        self.assertEqual(labor.transition(end,snapshot(day=20,is_in_labor=False),2,"a"),{})

    def test_rewind_and_epoch_change_never_give_negative_or_cross_branch_duration(self):
        start=labor.transition({},snapshot(),1,"a")
        self.assertEqual(labor.transition(start,snapshot(day=8,is_pregnant=False),1,"a"),start)
        interrupted=labor.transition(start,snapshot(day=8,is_in_labor=False),1,"b")
        self.assertEqual(interrupted['status'],'interrupted')
        self.assertIsNone(interrupted['minutes'])
        restarted=labor.transition(interrupted,snapshot(day=8),1,"b")
        self.assertTrue(restarted['restarted'])
        end=labor.transition(restarted,snapshot(day=8,hour=23,minute=0,is_pregnant=False),1,"b")
        self.assertEqual(end['minutes'],30)

    def test_manual_validation_and_return_to_automatic(self):
        value=labor.manual({'labor_hours':'12','labor_minutes':'35'})
        self.assertEqual(value['minutes'],755)
        self.assertEqual(labor.summary({'labor_duration_manual':value}),'12h 35m · Player-entered')
        self.assertIsNone(labor.manual({'labor_mode':'automatic'}))
        for data in ({},{'labor_hours':'-1'},{'labor_hours':'nan'}, {'labor_minutes':'60'}, {'labor_hours':'8761'}):
            with self.assertRaises(ValueError):labor.manual(data)


class LaborIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite:///:memory:');Base.metadata.create_all(self.engine)
        self.s=Session(self.engine);ws=Workspace(name='Labor');self.s.add(ws);self.s.flush()
        self.save=ChronicleSave(workspace_id=ws.id,name='Labor',global_day=200,start_year=1300,days_per_year=12,settings={})
        self.s.add(self.save);self.s.flush()
        self.mother=Record(save_id=self.save.id,kind='sim',label='Mother',data={'game_sim_id':'101','game_was_pregnant':True,'game_pregnancy_sequence':1})
        self.s.add(self.mother);self.s.flush()
        self.pregnancy=Record(save_id=self.save.id,kind='pregnancy',label='Twins',global_day=200,
            data={'mother_id':self.mother.id,'status':'Active','babies_expected':2,'game_pregnancy_sequence':1})
        self.s.add(self.pregnancy);self.s.flush()

    def tearDown(self):
        self.s.close();self.engine.dispose()

    def test_auto_intake_stores_one_interval_for_twins_and_preserves_override(self):
        automation.reconcile_sim(self.s,self.save,self.mother,snapshot(game_sim_id='101'))
        self.assertIn('labor_tracking',self.pregnancy.data)
        self.pregnancy.data={**self.pregnancy.data,'labor_duration_manual':{'minutes':600,'source':'Player-entered'}}
        automation.reconcile_sim(self.s,self.save,self.mother,snapshot(day=11,hour=0,minute=30,is_pregnant=False,game_sim_id='101',babies_delivered=2))
        self.assertEqual(self.pregnancy.data['labor_tracking']['minutes'],120)
        self.assertEqual(labor.summary(self.pregnancy.data),'10h 00m · Player-entered')
        self.assertEqual(self.pregnancy.data['babies_expected'],2)

    def test_attachment_delayed_until_pregnancy_exists(self):
        self.pregnancy.deleted=True
        labor.capture(self.s,self.save,self.mother,snapshot())
        labor.capture(self.s,self.save,self.mother,snapshot(day=11,is_pregnant=False))
        self.assertNotIn('labor_tracking',self.pregnancy.data)
        self.pregnancy.deleted=False
        labor.attach(self.s,self.save,self.mother,self.pregnancy)
        self.assertEqual(self.pregnancy.data['labor_tracking']['minutes'],1440)

    def test_real_pending_pregnancy_acceptance_keeps_observed_interval(self):
        from app import main
        from starlette.datastructures import FormData
        self.pregnancy.deleted=True
        self.mother.data={'game_sim_id':'101'}
        automation.reconcile_sim(self.s,self.save,self.mother,snapshot(game_sim_id='101',babies_expected=1))
        candidate=self.s.scalar(select(Record).where(Record.kind=='game_candidate',Record.data['action'].as_string()=='pregnancy_discovered'))
        self.assertEqual(candidate.data['payload']['game_pregnancy_sequence'],1)
        automation.reconcile_sim(self.s,self.save,self.mother,snapshot(day=11,is_pregnant=False,game_sim_id='101',babies_delivered=1))
        @contextmanager
        def db():yield self.s
        class Request:
            headers={}
            session={}
            async def form(self):return FormData()
        with patch.object(main,'db',db),patch.object(main,'owned_save',return_value=self.save):
            response=asyncio.run(main.accept_automation(Request(),candidate.id))
        self.assertEqual(response.status_code,303)
        pregnancy=self.s.scalar(select(Record).where(Record.kind=='pregnancy',Record.deleted.is_(False)))
        self.assertEqual(pregnancy.data['labor_tracking']['minutes'],1440)
        self.assertFalse(pregnancy.data['labor_identity_unverified'])

    def test_legacy_ambiguous_pregnancy_does_not_get_latest_labor(self):
        self.pregnancy.data={**self.pregnancy.data,'labor_identity_unverified':True}
        labor.capture(self.s,self.save,self.mother,snapshot())
        self.assertNotIn('labor_tracking',self.pregnancy.data)

    def test_repeated_capture_is_idempotent(self):
        labor.capture(self.s,self.save,self.mother,snapshot())
        versions=(self.mother.version,self.pregnancy.version)
        labor.capture(self.s,self.save,self.mother,snapshot(day=11))
        self.assertEqual((self.mother.version,self.pregnancy.version),versions)

    def test_no_attachment_to_different_mother_or_pregnancy_cycle(self):
        labor.capture(self.s,self.save,self.mother,snapshot())
        other=Record(save_id=self.save.id,kind='pregnancy',label='Other',data={'mother_id':self.mother.id,'game_pregnancy_sequence':2})
        self.s.add(other);self.s.flush()
        labor.attach(self.s,self.save,self.mother,other)
        self.assertNotIn('labor_tracking',other.data)
        self.pregnancy.data={**self.pregnancy.data,'mother_id':'another-sim'}
        before=self.pregnancy.version
        labor.capture(self.s,self.save,self.mother,snapshot(day=11,is_pregnant=False))
        self.assertEqual(self.pregnancy.version,before)

    def route(self,form):
        from app import main
        @contextmanager
        def db():yield self.s
        class Request:
            async def form(self):return form
        with patch.object(main,'db',db),patch.object(main,'owned_save',return_value=self.save):
            return asyncio.run(main.edit_labor_duration(Request(),self.pregnancy.id))

    def test_manual_route_and_unrelated_clock_changes(self):
        form={'labor_token':labor.fingerprint(self.pregnancy.data),'labor_hours':'8','labor_minutes':'5'}
        self.pregnancy.data={**self.pregnancy.data,'game_pregnancy_progress':95};self.pregnancy.version+=1
        self.assertEqual(self.route(form).status_code,303)
        self.assertEqual(labor.summary(self.pregnancy.data),'8h 05m · Player-entered')
        self.route({'labor_token':labor.fingerprint(self.pregnancy.data),'labor_mode':'automatic'})
        self.assertEqual(labor.summary(self.pregnancy.data),'Not recorded')

    def test_manual_route_blocks_stale_deleted_and_frozen_records(self):
        from fastapi import HTTPException
        form={'labor_token':'old','labor_hours':'5'}
        with self.assertRaises(HTTPException) as err:self.route(form)
        self.assertEqual(err.exception.status_code,409)
        self.pregnancy.data={**self.pregnancy.data,'infinite_frozen':True}
        form['labor_token']=labor.fingerprint(self.pregnancy.data)
        with self.assertRaises(HTTPException) as err:self.route(form)
        self.assertEqual(err.exception.status_code,409)
        self.pregnancy.deleted=True
        with self.assertRaises(HTTPException) as err:self.route(form)
        self.assertEqual(err.exception.status_code,404)

    def test_ui_labels_estimates_and_manual_entries(self):
        from app import main
        labor.capture(self.s,self.save,self.mother,snapshot())
        labor.capture(self.s,self.save,self.mother,snapshot(day=11,is_pregnant=False))
        html=main.templates.env.get_template('_labor_duration.html').render(pregnancy=self.pregnancy)
        self.assertIn('24h 00m · Estimated',html)
        self.assertIn('Game day 10 · 22:30',html)
        self.assertIn('Twins share one labor record',html)
        self.assertIn('Save labor length',html)
