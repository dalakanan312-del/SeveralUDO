"""Background updates must not prevent confirming an unchanged reviewed result."""
import copy
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from sqlalchemy import select,func
from app import action_previews as previews,domain
from app.models import Record,ActionPreview,DiceAudit,Change
from tests import test_play_clarity as fixtures


class ConfirmationTests(unittest.TestCase):
    setUp=fixtures.PlayClarityTests.setUp
    tearDown=fixtures.PlayClarityTests.tearDown
    add=fixtures.PlayClarityTests.add
    headers=fixtures.PlayClarityTests.headers

    def preview(self,row,actual=2,native=False):
        response=self.client.post('/api/rolls/'+row.id+('/roll' if native else '/complete'),
                                  data={} if native else {'actual':actual},headers=self.headers(row))
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['preview']

    def confirm(self,p,status=200):
        response=self.client.post('/api/previews/'+p['token']+'/confirm',headers=self.headers())
        self.assertEqual(response.status_code,status,response.text)
        return response

    def pending(self,**data):
        return self.add('roll','Survival',sim_id=self.f.people[0].id,die='d6',**data)

    def enable(self):
        self.f.save.settings={**self.f.save.settings,'automation_enabled':True};self.f.session.commit()

    def test_clock_revision_settings_and_unrelated_changes_are_preserved(self):
        row=self.pending(nonlethal=True);p=self.preview(row)
        self.f.session.refresh(self.f.save)
        self.f.save.revision+=40;revision=self.f.save.revision
        self.f.save.name='Renamed save'
        self.f.save.settings={**self.f.save.settings,'clock_ui_observation':{'hour':21},
            'clock_game_day_high_watermark':1000,'theme_preset':'light','clock_receipt_summary':{'births':1}}
        outsider=self.f.people[-1];outsider.data={**outsider.data,'notes':'Unrelated edit'};outsider.version+=1
        self.f.session.commit()
        self.add('game_candidate','New game pregnancy',status='pending')
        self.confirm(p)
        self.f.session.refresh(self.f.save);self.f.session.refresh(outsider);self.f.session.refresh(row)
        self.assertTrue(row.data['completed']);self.assertEqual(row.data['actual'],2)
        self.assertEqual(self.f.save.name,'Renamed save');self.assertEqual(self.f.save.revision,revision+1)
        self.assertEqual(self.f.save.settings['clock_game_day_high_watermark'],1000)
        self.assertEqual(self.f.save.settings['theme_preset'],'light');self.assertEqual(outsider.data['notes'],'Unrelated edit')

    def test_previewing_two_rolls_does_not_invalidate_first(self):
        a=self.pending(nonlethal=True);b=self.pending(nonlethal=True)
        first=self.preview(a,native=True);second=self.preview(b,native=True)
        self.confirm(first);self.confirm(second)
        self.f.session.refresh(a);self.f.session.refresh(b)
        self.assertTrue(a.data['completed'] and b.data['completed'])

    def test_witch_trial_no_trial_confirms_after_report(self):
        self.enable()
        row=self.add('roll','Witch trial occurrence — Black',sim_id=self.f.people[0].id,
            die='d4',occult_roll=True,occult_rule_key='spellcaster_witch_trial',trigger_results='3',
            result_rules='3: Witch trial occurs; all others: No trial',nonlethal=True)
        p=self.preview(row,1);self.assertEqual(p['outcome'],'No trial')
        self.f.session.refresh(self.f.save);self.f.save.revision+=5;self.f.session.commit()
        self.confirm(p);self.f.session.refresh(row)
        self.assertEqual(row.data['actual'],1);self.assertEqual(row.data['outcome'],'No trial')
        self.assertFalse(row.data['triggered'])
        self.assertEqual(self.f.session.scalar(select(func.count()).select_from(Record).where(Record.data['origin_roll_id'].as_string()==row.id)),0)

    def test_native_result_is_not_drawn_again_on_confirm(self):
        row=self.pending(nonlethal=True);p=self.preview(row,native=True)
        self.f.session.refresh(self.f.save);self.f.save.revision+=9;self.f.session.commit()
        with patch('app.dice.audited_roll',side_effect=AssertionError('Must not throw again')):self.confirm(p)
        self.f.session.refresh(row);self.assertEqual(row.data['actual'],p['actual'])
        self.assertEqual(self.f.session.scalar(select(func.count()).select_from(DiceAudit).where(DiceAudit.context_id==row.id)),1)

    def test_same_sim_metadata_survives_failed_roll_and_random_consequences_are_replayed(self):
        self.enable();row=self.pending(bad_results='1');p=self.preview(row,1)
        ticket=self.f.session.get(ActionPreview,p['token'])
        expected=next(c['after']['data'] for c in ticket.payload['plan']['records'] if c['after']['id']==self.f.people[0].id)
        sim=self.f.people[0];self.f.session.refresh(sim)
        sim.data={**sim.data,'game_skills':[{'name':'Cooking','level':8}],'notes':'Keep this new note'};sim.version+=1
        self.f.session.commit()
        with patch('app.preview_random.random.SystemRandom',side_effect=AssertionError('No new random consequences')):self.confirm(p)
        self.f.session.refresh(sim)
        self.assertEqual(sim.data['death_global_day'],expected['death_global_day'])
        self.assertEqual(sim.data['cause_of_death'],expected['cause_of_death'])
        self.assertEqual(sim.data['notes'],'Keep this new note');self.assertEqual(sim.data['game_skills'][0]['level'],8)

    def test_changed_actor_death_requires_review_even_for_passing_result(self):
        row=self.pending(nonlethal=True);p=self.preview(row)
        sim=self.f.people[0];sim.data={**sim.data,'death_confirmed':True,'death_global_day':100};sim.version+=1;self.f.session.commit()
        self.confirm(p,409);self.f.session.refresh(row);self.assertFalse(row.data.get('completed'))

    def test_calendar_or_master_toggle_change_requires_review(self):
        for field,value in [('days_per_year',12),('global_day',101),('settings',{'automation_enabled':True})]:
            with self.subTest(field=field):
                row=self.pending(nonlethal=True);p=self.preview(row)
                setattr(self.f.save,field,value);self.f.session.commit();self.confirm(p,409)

    def test_read_only_source_rule_change_is_detected(self):
        rule=self.add('roll_rule','Source',die='d6',bad_results='1')
        row=self.pending(source_rule_id=rule.id,nonlethal=True);p=self.preview(row)
        rule.data={**rule.data,'die':'d12'};rule.version+=1;self.f.session.commit();self.confirm(p,409)

    def test_new_conditional_followup_requires_review_without_partial_writes(self):
        self.enable();row=self.pending(rule_generated=True,source_rule_key='parent',trigger_results='2')
        p=self.preview(row)
        self.add('future_rule','New followup',rule_key='child',triggered_by='parent',die='d4',active=True)
        before=self.f.session.scalar(select(func.count()).select_from(Change))
        self.confirm(p,409)
        self.f.session.refresh(row);self.assertFalse(row.data.get('completed'))
        self.assertEqual(self.f.session.scalar(select(func.count()).select_from(Change)),before)
        self.assertEqual(self.f.session.scalar(select(func.count()).select_from(Record).where(Record.data['origin_roll_id'].as_string()==row.id)),0)

    def test_new_active_illness_changes_death_consequences_and_requires_review(self):
        self.enable();row=self.pending(bad_results='1');p=self.preview(row,1)
        self.add('illness','New fever',sim_id=self.f.people[0].id,status='Active')
        self.confirm(p,409);self.f.session.refresh(row);self.assertFalse(row.data.get('completed'))

    def test_new_followup_survives_background_report_without_duplication(self):
        self.enable();self.add('future_rule','Child',rule_key='child',triggered_by='parent',die='d4',active=True)
        row=self.pending(rule_generated=True,source_rule_key='parent',trigger_results='2');p=self.preview(row)
        self.f.session.refresh(self.f.save);self.f.save.revision+=4;self.f.session.commit()
        self.confirm(p);self.confirm(p,409)
        self.assertEqual(self.f.session.scalar(select(func.count()).select_from(Record).where(Record.data['origin_roll_id'].as_string()==row.id)),1)

    def test_refresh_preserves_actual_result_and_still_requires_confirmation(self):
        row=self.pending(nonlethal=True);p=self.preview(row,native=True)
        self.f.session.refresh(row);row.data={**row.data,'success_outcome':'A revised outcome'};row.version+=1;self.f.session.commit()
        self.confirm(p,409)
        response=self.client.post('/api/previews/'+p['token']+'/refresh',headers=self.headers())
        self.assertEqual(response.status_code,200,response.text);fresh=response.json()['preview']
        self.assertNotEqual(fresh['token'],p['token']);self.assertEqual(fresh['actual'],p['actual'])
        self.f.session.refresh(row);self.assertFalse(row.data.get('completed'))
        self.confirm(fresh);self.f.session.refresh(row);self.assertEqual(row.data['actual'],p['actual'])

    def test_expired_preview_refresh_and_legacy_ticket_fail_closed(self):
        row=self.pending(nonlethal=True);p=self.preview(row)
        ticket=self.f.session.get(ActionPreview,p['token']);ticket.created_at=datetime.now(timezone.utc)-timedelta(minutes=20)
        payload=copy.deepcopy(ticket.payload);payload.pop('dependencies');payload['plan'].pop('replay');ticket.payload=payload
        self.f.session.commit();self.confirm(p,409)
        response=self.client.post('/api/previews/'+p['token']+'/refresh',headers=self.headers())
        self.assertEqual(response.status_code,200,response.text);self.confirm(response.json()['preview'])

    def test_wrong_owner_and_active_save_are_rejected(self):
        row=self.pending(nonlethal=True);p=self.preview(row)
        ticket=self.f.session.get(ActionPreview,p['token']);ticket.user_id='another-user';self.f.session.commit()
        self.assertEqual(self.client.post('/api/previews/'+p['token']+'/confirm',headers=self.headers()).status_code,404)
        self.assertEqual(self.client.post('/api/previews/'+p['token']+'/refresh',headers=self.headers()).status_code,404)


if __name__=='__main__':unittest.main()
