import unittest
from datetime import datetime,timezone
from unittest.mock import patch
from sqlalchemy import select,func
from fastapi.testclient import TestClient
from app import main,domain,play_clarity as clarity,action_previews as previews,usability,usability_ui
from app.models import Record,ActionPreview,DiceAudit,ClockLink,Change
from tests import test_infinite_decades as fixtures

class PlayClarityTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.InfiniteDecadesTests();self.f.setUp()
        self.f.save.settings={**self.f.save.settings,'automation_enabled':False};self.f.session.commit()
        self.binding=patch.object(main,'SessionLocal',self.f.sessions);self.binding.start();self.client=TestClient(main.app)
        self.client.post('/saves/select',data={'save_id':self.f.save.id})
    def tearDown(self):self.client.close();self.binding.stop();self.f.tearDown()
    def add(self,kind,label,day=100,**data):
        row=Record(save_id=self.f.save.id,kind=kind,label=label,global_day=day,data=data);self.f.session.add(row);self.f.session.commit();return row
    def headers(self,row=None):
        return {'X-Decades-Fragment':'1' if row else 'preview','X-UI-Save':self.f.save.id,**({'X-Record-Version':str(row.version)} if row else {})}
    def test_twin_duplicate_identity_is_per_delivery_and_baby(self):
        a=self.add('roll','Mother check 1',sim_id='mother',roll_type='Maternal',source_id='preg1',maternal_baby_index=1)
        b=self.add('roll','Mother check 2',sim_id='mother',roll_type='Maternal',source_id='preg1',maternal_baby_index=2)
        c=self.add('roll','Other pregnancy',sim_id='mother',roll_type='Maternal',source_id='preg2',maternal_baby_index=1)
        self.assertEqual(domain.duplicate_obligation_summary([a,b,c])['groups'],0)
        duplicate=self.add('roll','Extra check 1',sim_id='mother',roll_type='Maternal',source_id='preg1',maternal_baby_index=1)
        summary=domain.duplicate_obligation_summary([a,b,c,duplicate]);self.assertEqual(summary['repairable'],1)
        self.assertIn('Same mother, pregnancy, baby',summary['preview'][0]['reason'])
    def test_delivery_panel_contains_named_babies_and_each_check(self):
        mom=self.f.people[0]
        pregnancy=self.add('pregnancy','Twins',mother_id=mom.id,status='Delivered',babies_delivered=2,actual_delivery_global_day=100)
        kids=[self.add('sim',name,birth_global_day=100,pregnancy_id=pregnancy.id,mother_id=mom.id,birthplace='London',legitimacy='Legitimate') for name in ('Galia','Aria')]
        checks=[]
        for i,child in enumerate(kids,1):
            checks.append(self.add('roll','Maternal — Young Adult',sim_id=mom.id,roll_type='Maternal — Young Adult',source_id=pregnancy.id,maternal_baby_index=i,die='d20',bad_results='1'))
            self.add('roll','Birth check',sim_id=child.id,roll_type='Being Born',die='d20',lifecycle_age_days=0,source='aging:'+child.id+':test')
        html=self.client.get('/p/today').text
        for label in ('Galia','Aria','London','Legitimate','Baby 1','Baby 2','childbirth survival','delivering Galia','delivering Aria'):self.assertIn(label,html)
        self.assertEqual(html.count('ONE DELIVERY'),1)
        self.assertEqual(html.count('id="roll-'+checks[0].id+'"'),1)
    def test_scaled_aging_calculation_and_profile_schedule(self):
        sim=self.f.people[0];sim.data={**sim.data,'birth_global_day':98};self.f.save.days_per_year=12;self.f.session.commit()
        rule=self.add('roll_rule','Infant',age_days=3,standard_age_days=1,active=True,die='d20')
        row=self.add('roll','Infant check',101,sim_id=sim.id,roll_type='Infant',lifecycle_age_days=3,age_calendar_days_per_year=12,source=f'aging:{sim.id}:{rule.id}',die='d20')
        view=clarity.roll_presentation(self.f.save,row,sim)
        self.assertIn('Birth GD 98 + 3 days',view['calculation']);self.assertIn('1 base days × 12/4 = 3',view['calculation'])
        html=self.client.get('/sims/'+sim.id).text;self.assertIn('Life-stage schedule',html);self.assertIn('Next birthday',html);self.assertIn('GD 101',html)
    def test_unrelated_context_removed(self):
        row=self.add('roll','Birth',sim_id=self.f.people[0].id,roll_type='Being Born',historical_context='Mayan Collapse',notes='Mayan Collapse',die='d20')
        html=self.client.get('/api/ui/rolls/'+row.id).text;self.assertNotIn('Mayan Collapse',html)
        row.data={**row.data,'event_id':'source-event'};self.f.session.commit()
        self.assertIn('Mayan Collapse',self.client.get('/api/ui/rolls/'+row.id).text)
    def test_legacy_aging_roll_exposes_wrong_date(self):
        sim=self.f.people[0];sim.data={**sim.data,'birth_global_day':100};self.f.save.days_per_year=12;self.f.session.commit()
        row=self.add('roll','Imported infant check',101,sim_id=sim.id,roll_type='Infant',die='d20')
        result=clarity.roll_presentation(self.f.save,row,sim)
        self.assertIn('Birth GD 100 + 3 days',result['calculation']);self.assertIn('GD 103',result['warning'])
    def test_household_batch_filters_all_actor_fields(self):
        house=self.add('household','Black home');other=self.add('household','Other home')
        sim=self.f.people[0];sim.data={**sim.data,'current_household_id':house.id}
        outsider=self.f.people[1];outsider.data={**outsider.data,'current_household_id':other.id};self.f.session.commit()
        self.add('roll','Black roll',sim_id=sim.id,die='d6');self.add('roll','Other roll',sim_id=outsider.id,die='d6')
        self.add('pregnancy','Black pregnancy',mother_id=sim.id,due_global_day=100,status='Active')
        self.add('illness','Black fever',sim_id=sim.id,status='Active')
        self.add('relationship','Black marriage',partner1_id=sim.id,partner2_id=outsider.id,marriage_global_day=100)
        html=self.client.get('/p/today?household='+house.id).text
        for text in ('Black roll','Black pregnancy','Black fever','Black marriage'):self.assertIn(text,html)
        self.assertNotIn('Other roll',html)
    def test_manual_result_previews_without_mutations_then_exact_commit(self):
        row=self.add('roll','Survival',sim_id=self.f.people[0].id,die='d6',bad_results='1')
        before=self.f.session.scalar(select(func.count()).select_from(Change))
        response=self.client.post('/api/rolls/'+row.id+'/complete',data={'actual':'1'},headers=self.headers(row))
        self.assertEqual(response.status_code,200,response.text);p=response.json()['preview']
        self.f.session.refresh(row);self.assertFalse(row.data.get('completed'))
        self.assertEqual(self.f.session.scalar(select(func.count()).select_from(Change)),before)
        result=self.client.post('/api/previews/'+p['token']+'/confirm',headers=self.headers())
        self.assertEqual(result.status_code,200,result.text);self.f.session.refresh(row);self.assertEqual(row.data['actual'],1)
        self.assertEqual(self.client.post('/api/previews/'+p['token']+'/confirm',headers=self.headers()).status_code,409)
    def test_failed_roll_preview_keeps_exact_death_and_followups(self):
        sim=self.f.people[0];sim.data={**sim.data,'death_global_day':125};self.f.save.settings={**self.f.save.settings,'automation_enabled':True};self.f.session.commit()
        self.add('death','Scheduled death',125,sim_id=sim.id,completed=False)
        self.add('illness','Active fever',sim_id=sim.id,status='Active')
        famine=self.add('event','Famine',103,start_global_day=103,end_global_day=103)
        row=self.add('roll','Famine',sim_id=sim.id,die='d6',bad_results='1',event_id=famine.id,failure_is_lethal=True,death_window_start=103,death_window_end=103,roll_type='Event')
        r=self.client.post('/api/rolls/'+row.id+'/complete',data={'actual':1},headers=self.headers(row));self.assertEqual(r.status_code,200,r.text)
        p=r.json()['preview'];self.assertTrue(any('replaces existing GD 125' in s for s in p['effects']))
        self.f.session.refresh(sim);self.assertEqual(sim.data['death_global_day'],125)
        ticket=self.f.session.get(ActionPreview,p['token']);expected=next(c['after']['data']['death_global_day'] for c in ticket.payload['plan']['records'] if c['after']['id']==sim.id)
        r=self.client.post('/api/previews/'+p['token']+'/confirm',headers=self.headers());self.assertEqual(r.status_code,200,r.text)
        self.f.session.refresh(sim);self.assertEqual(sim.data['death_global_day'],expected)
    def test_native_throw_is_reserved_not_rerolled(self):
        row=self.add('roll','One throw',sim_id=self.f.people[0].id,die='d6',bad_results='1')
        a=self.client.post('/api/rolls/'+row.id+'/roll',headers=self.headers(row)).json()['preview']
        b=self.client.post('/api/rolls/'+row.id+'/roll',headers=self.headers(row)).json()['preview']
        self.assertEqual(a['actual'],b['actual']);self.f.session.refresh(row);self.assertFalse(row.data.get('completed'))
        self.assertEqual(self.f.session.scalar(select(func.count()).select_from(DiceAudit).where(DiceAudit.context_id==row.id)),1)
    def test_conditional_followup_in_preview_and_exactly_once_after_confirm(self):
        self.f.save.settings={**self.f.save.settings,'automation_enabled':True};self.f.session.commit()
        self.add('future_rule','Declared parent',rule_key='parent',die='d6',trigger_results='1',active=True)
        self.add('future_rule','Declared child',rule_key='child',triggered_by='parent',die='d4',trigger_results='2',active=True)
        row=self.add('roll','Origin',sim_id=self.f.people[0].id,rule_generated=True,source_rule_key='parent',die='d6',trigger_results='1',completed=False)
        r=self.client.post('/api/rolls/'+row.id+'/complete',data={'actual':1},headers=self.headers(row))
        self.assertEqual(r.status_code,200,r.text);p=r.json()['preview'];self.assertTrue(any('Declared child' in x for x in p['effects']))
        query=select(func.count()).select_from(Record).where(Record.data['origin_roll_id'].as_string()==row.id)
        self.assertEqual(self.f.session.scalar(query),0)
        r=self.client.post('/api/previews/'+p['token']+'/confirm',headers=self.headers());self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.f.session.scalar(query),1)
    def test_stale_related_record_rejects_atomic_confirmation(self):
        row=self.add('roll','Pending',sim_id=self.f.people[0].id,die='d6',nonlethal=True)
        p=self.client.post('/api/rolls/'+row.id+'/complete',data={'actual':2},headers=self.headers(row)).json()['preview']
        row.data={**row.data,'notes':'New edit'};row.version+=1;self.f.session.commit()
        self.assertEqual(self.client.post('/api/previews/'+p['token']+'/confirm',headers=self.headers()).status_code,409)
        self.f.session.refresh(row);self.assertFalse(row.data.get('completed'))
    def test_calendar_preview_counts_and_keeps_completed_history(self):
        sim=self.f.people[0];sim.data={**sim.data,'birth_global_day':100};self.f.session.commit()
        rule=self.add('roll_rule','Infant',age_days=1,standard_age_days=1,age_days_calendar_days_per_year=4,die='d20',active=True)
        pending=self.add('roll','Infant pending',101,sim_id=sim.id,roll_type='Infant',source=f'aging:{sim.id}:{rule.id}',lifecycle_age_days=1,die='d20')
        completed=self.add('roll','History',90,sim_id=sim.id,roll_type='Child',completed=True,actual=8,outcome='Passed')
        r=self.client.post('/settings',data={'days_per_year':'12','settings_scope':'calendar'},headers=self.headers());self.assertEqual(r.status_code,200,r.text)
        p=r.json()['preview'];self.assertEqual(p['counts']['moved'],1);self.f.session.refresh(pending);self.assertEqual(pending.global_day,101)
        r=self.client.post('/api/previews/'+p['token']+'/confirm',headers=self.headers());self.assertEqual(r.status_code,200,r.text)
        self.f.session.refresh(pending);self.f.session.refresh(completed);self.assertEqual(pending.global_day,103);self.assertEqual(completed.global_day,90);self.assertEqual(completed.data['actual'],8)
    def test_receipt_vs_changes_and_source_evidence(self):
        save=self.f.save;save.settings={**save.settings,'clock_receipt_summary':{'game_time_changed':False,'candidate_types':{'new_baby':1,'relationship':2}}}
        link=ClockLink(save_id=save.id,enabled=True,last_seen_at=datetime.now(timezone.utc),last_game_day=29,last_game_hour=7,last_game_minute=50)
        text=usability.clock_status(save,link)['receipt_summary'];self.assertIn('game time unchanged',text);self.assertIn('1 birth detection',text);self.assertIn('2 relationship updates',text)
        sim=self.f.people[0];sim.data={**sim.data,'birth_time_source':'Clock Sync exact game observation','birth_time':'08:12'};self.f.session.commit()
        html=self.client.get('/sims/'+sim.id).text;self.assertIn('Game-confirmed',html);self.assertIn('data-date-evidence=',html)
    def test_branch_banner_uses_checkpoint_metadata(self):
        branch=self.add('dynasty_branch','Main family line',meta={'game_save_name':'Black Decades 1'})
        self.f.save.settings={**self.f.save.settings,'infinite_decades':{'schema_version':2,'active_branch_id':branch.id,'branch_name':'Main family line','game_ready':True,'status':'active'}}
        result=clarity.branch_banner(self.f.session,self.f.save);self.assertEqual(result['checkpoint'],'Black Decades 1')

if __name__=='__main__':unittest.main()
