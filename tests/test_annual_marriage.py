"""Player-supplied annual marriage tables on disposable saves only."""
import copy
import unittest
from sqlalchemy import select
from app import domain, marriage_rules as rules, action_previews
from app.models import Record
from tests import test_event_targets as fixtures
from tests import test_play_clarity as api


class AnnualMarriageTests(unittest.TestCase):
    tearDown=fixtures.EventTargetTests.tearDown
    record=fixtures.EventTargetTests.record
    home=fixtures.EventTargetTests.home
    person=fixtures.EventTargetTests.person
    def setUp(self):
        fixtures.EventTargetTests.setUp(self)
        self.save.global_day=101
        self.save.settings={'marriage_roll_mode':'era_annual','marriage_min_age_days':52}
    def age(self,age,name='Sim',**data):
        return self.person(name,birth_global_day=self.save.global_day-age*self.save.days_per_year,**data)
    def annuals(self):
        return list(self.s.scalars(select(Record).where(Record.kind=='roll',Record.deleted.is_(False),Record.data['annual_marriage'].as_boolean().is_(True))))
    def schedule(self):
        domain.schedule_marriage_rolls(self.s,self.save);self.s.flush();return self.annuals()
    def end_marriage(self,sim,years,status='Widowed',**extra):
        day=rules.year_bounds(self.save,rules.year_at(self.save)-years)[0]
        return self.record('relationship','Prior marriage',type='Marriage',partner1_id=sim.id,partner2_id='former',
                           status=status,end_global_day=day,**extra)
    def result(self,sim,value=1):
        roll=next(r for r in self.schedule() if r.data['sim_id']==sim.id)
        domain.complete_roll(self.s,self.save,roll,value);self.s.flush();return roll

    def test_exact_table_all_eras_and_columns(self):
        expected=[[(8,1),(6,2),(8,1),(6,2),(8,1)],[(10,1),(8,2),(10,1),(8,2),(10,1)],
                  [(10,1),(8,2),(10,1),(8,2),(10,1)],[(12,1),(8,2),(10,1),(8,1),(10,1)],
                  [(20,1),(8,1),(10,1),(10,1),(12,1)],[(20,1),(10,2),(12,1),(10,1),(12,1)],
                  [(20,1),(8,2),(12,1),(12,1),(20,1)],[(20,1),(6,2),(10,1),(12,1),(20,1)],
                  [(20,1),(8,1),(10,1),(12,1),(20,1)]]
        for era,odds in zip(rules.ERAS,expected):self.assertEqual(list(era[3]),odds)
        for boundary in [-3000,500,1000,1450,1750,1900,1946,1970]:self.assertEqual(rules.era_for(boundary)[0],boundary)
        self.assertIsNone(rules.era_for(-9001))
    def test_age_bands_not_game_stage_or_occult_lifespan(self):
        for age,column,stage in [(12,None,None),(13,0,'Teen'),(17,0,'Teen'),(18,1,'Young Adult'),(39,1,'Young Adult'),(40,2,'Adult'),(59,2,'Adult'),(60,2,'Elder')]:
            sim=self.age(age,str(age),game_age='YOUNGADULT',species_occult='Vampire')
            spec,reason=rules.specification(sim,self.save,rules.rows(self.s,self.save))
            if column is None:self.assertIsNone(spec)
            else:
                self.assertEqual(spec['marriage_stage'],stage)
                self.assertEqual(spec['die'],'d'+str(rules.era_for(rules.year_at(self.save))[3][column][0]))
    def test_one_each_year_including_heirs_no_backlog(self):
        a=self.age(25,'Heir');self.age(40,'Adult');self.save.settings={**self.save.settings,'current_heir_id':a.id}
        rolls=self.schedule();self.assertEqual(len(rolls),2);self.assertEqual(len(self.schedule()),2)
        domain.complete_roll(self.s,self.save,rolls[0],8);before=copy.deepcopy(rolls[0].data)
        self.save.global_day+=4;current=self.schedule()
        self.assertEqual(len([r for r in current if not r.data.get('completed')]),2)
        self.assertEqual(rolls[0].data,before);self.assertTrue(rolls[1].deleted)
    def test_twelve_day_year_age_and_annual_boundary(self):
        self.save.days_per_year=12;self.save.global_day=301;sim=self.age(18)
        spec,_=rules.specification(sim,self.save,rules.rows(self.s,self.save));self.assertEqual(spec['marriage_stage'],'Young Adult')
        first=self.schedule()[0];domain.complete_roll(self.s,self.save,first,8)
        self.save.global_day=312;self.assertEqual(len(self.schedule()),1)
        self.save.global_day=313;self.assertEqual(len(self.schedule()),2)
    def test_no_remarriage_in_year_marriage_ends(self):
        for status in ['Widowed','Divorced','Abandoned']:
            sim=self.age(30,status);self.end_marriage(sim,0,status)
        self.assertEqual(self.schedule(),[])
        self.save.global_day+=4;self.assertEqual(len(self.schedule()),3)
    def test_latest_marriage_not_each_former_spouse(self):
        sim=self.age(45);self.end_marriage(sim,8);self.end_marriage(sim,2,'Divorced')
        roll=self.schedule()[0];self.assertEqual(roll.data['marriage_years_unmarried'],2);self.assertEqual(roll.data['die'],'d8')
    def test_five_ten_year_and_elder_boundaries(self):
        for years,extended,age,die in [(5,False,45,'d8'),(6,False,45,'d10'),(10,True,45,'d8'),(11,True,45,'d10'),(1,True,60,'d10')]:
            sim=self.age(age,str((years,extended,age)),marriage_practical_extension=extended,marriage_practical_reason='Farm')
            self.end_marriage(sim,years)
            spec,_=rules.specification(sim,self.save,rules.rows(self.s,self.save));self.assertEqual(spec['die'],die)
    def test_extension_needs_recorded_reason(self):
        sim=self.age(40,marriage_practical_extension=True);self.end_marriage(sim,6)
        spec,_=rules.specification(sim,self.save,rules.rows(self.s,self.save));self.assertEqual(spec['die'],'d10')
    def test_unknown_end_date_and_separation_do_not_grant_remarriage(self):
        a=self.age(30,'A');b=self.age(30,'B')
        self.record('relationship','Unknown end',type='Marriage',partner1_id=a.id,status='Widowed')
        self.record('relationship','Separated',type='Marriage',partner1_id=b.id,status='Separated',legally_married=True)
        self.assertEqual(self.schedule(),[])
    def test_dead_spouse_date_is_used_without_end_status(self):
        a=self.age(30,'A');b=self.age(30,'Dead',death_global_day=101)
        self.record('relationship','Marriage',type='Marriage',partner1_id=a.id,partner2_id=b.id,legally_married=True)
        self.assertEqual(self.schedule(),[])
        self.save.global_day+=4;rolls=self.schedule();self.assertEqual(len(rolls),1);self.assertTrue(rolls[0].data['remarriage_roll'])
    def test_deceased_frozen_animals_unborn_and_married_excluded(self):
        self.age(30,'Dead',death_global_day=100);self.age(30,'Frozen',infinite_frozen=True)
        self.age(30,'Cat',game_species='Cat');self.age(-1,'Unborn');married=self.age(30,'Married')
        self.record('relationship','Active marriage',type='Marriage',partner1_id=married.id,legally_married=True)
        self.assertEqual(self.schedule(),[])
    def test_permissions_region_class_faith_custom_and_event_override(self):
        self.save.settings={**self.save.settings,'annual_marriage_policies':[
            {'label':'Custom','region':'England','class':'Noble','faith':'Catholic','custom':'court',
             'permission':'blocked','from_year':1000,'until_year':1100}]}
        a=self.age(25,'Blocked',country='England',social_class='Noble',religion='Catholic',marriage_custom='court')
        b=self.age(25,'Different region',country='France',social_class='Noble',religion='Catholic',marriage_custom='court')
        self.age(25,'Required',marriage_eligibility='event');self.age(25,'Too early',marriage_available_from_year=1100)
        self.age(25,'Unpermitted',marriage_eligibility='blocked')
        self.assertEqual([r.data['sim_id'] for r in self.schedule()],[b.id])
        self.assertIn('Custom',rules.eligibility(a,self.save,rules.rows(self.s,self.save)))
    def test_master_automation_off_creates_nothing(self):
        self.age(25);self.save.settings={**self.save.settings,'automation_enabled':False}
        self.assertEqual(self.schedule(),[])
    def test_success_without_spouse_records_arranged_match_not_fake_wedding(self):
        sim=self.age(25);roll=self.result(sim)
        self.assertEqual(roll.data['marriage_decision'],'awaiting_match');self.assertNotIn('suggested_marriage_global_day',roll.data)
        self.assertEqual(list(self.s.scalars(select(Record).where(Record.kind=='relationship'))),[])
        self.save.global_day+=4;self.assertEqual(len(self.schedule()),2)
    def test_success_date_stays_in_current_year_even_on_last_day(self):
        self.save.global_day=104;a=self.age(25,'A');self.age(25,'B')
        roll=self.result(a);self.assertEqual(roll.data['suggested_marriage_global_day'],104)
        domain.backfill_generated_marriage_dates(self.s,self.save);self.assertEqual(roll.data['suggested_marriage_global_day'],104)
        self.assertIsNone(a.data.get('death_global_day'))
    def test_refusal_one_succeeds_and_other_results_do_not(self):
        for actual in range(1,7):
            sim=self.age(25,str(actual));origin=self.result(sim);child=rules.request_refusal(self.s,self.save,origin)
            self.assertEqual(rules.request_refusal(self.s,self.save,origin).id,child.id)
            domain.complete_roll(self.s,self.save,child,actual)
            self.assertEqual(origin.data['marriage_decision'],'refused' if actual==1 else 'must_marry')
            self.assertIsNone(sim.data.get('death_global_day'))
    def test_preview_rolls_back_and_confirms_exact_current_year_date(self):
        a=self.age(25,'A');self.age(25,'B');origin=next(r for r in self.schedule() if r.data['sim_id']==a.id);self.s.commit()
        plan=action_previews.simulate(self.s,self.save,lambda:domain.complete_roll(self.s,self.save,origin,1))
        self.assertFalse(origin.data['completed']);description=action_previews.describe(plan,'roll')
        self.assertTrue(any('eligible spouse' in effect for effect in description['effects']))
    def test_spouse_plan_reuses_courtship_and_suppresses_duplicate_roll(self):
        a=self.age(25,'A');b=self.age(25,'B')
        courtship=self.record('relationship','Courtship',type='Courtship',status='Active',partner1_id=a.id,partner2_id=b.id)
        origin=self.result(a);rel=rules.plan_match(self.s,self.save,origin,b.id)
        self.assertEqual(rel.id,courtship.id);self.assertEqual(rel.data['type'],'Betrothal');self.assertFalse(rel.data['legally_married'])
        self.assertEqual(len(self.schedule()),1)
        with self.assertRaises(ValueError):rules.plan_match(self.s,self.save,origin,b.id)
    def test_close_kin_and_negative_results_are_not_available_spouses(self):
        parent=self.age(45,'Parent');a=self.age(25,'A',mother_id=parent.id);b=self.age(25,'B');self.result(b,8)
        self.assertEqual(rules.candidates(a,self.save,rules.rows(self.s,self.save)),[])
    def test_completed_and_frozen_legacy_rolls_preserved_on_switch(self):
        a=self.age(25);old=self.record('roll','Legacy completed',source='planner:marriage:'+a.id,sim_id=a.id,completed=True,actual=7)
        old.global_day=97;frozen=self.record('roll','Parked',source='planner:marriage:frozen',infinite_frozen=True,completed=False)
        before=copy.deepcopy(old.data);self.schedule();self.assertEqual(old.data,before);self.assertFalse(frozen.deleted)
    def test_stale_year_or_custom_permissions_rejected(self):
        a=self.age(25);origin=self.schedule()[0];self.save.global_day+=4
        with self.assertRaises(ValueError):domain.complete_roll(self.s,self.save,origin,1)
        self.save.global_day-=4;a.data={**a.data,'marriage_eligibility':'blocked'}
        with self.assertRaises(ValueError):domain.complete_roll(self.s,self.save,origin,1)

    def test_calendar_scaling_does_not_turn_refusal_into_age_milestone(self):
        a=self.age(25);self.age(25,'Other');origin=self.result(a)
        child=rules.request_refusal(self.s,self.save,origin);due=child.global_day
        self.save.days_per_year=12
        domain.rescale_age_timing(self.s,self.save,4,12)
        self.assertEqual(child.global_day,due)

    def test_completed_no_marriage_and_refusal_never_become_death(self):
        sim=self.age(25);origin=self.result(sim,8)
        self.assertTrue(origin.data['nonlethal']);self.assertFalse(origin.data.get('marriage_success'))
        self.assertIsNone(sim.data.get('death_global_day'))


class AnnualMarriageHttpTests(unittest.TestCase):
    tearDown=api.PlayClarityTests.tearDown
    add=api.PlayClarityTests.add
    headers=api.PlayClarityTests.headers
    def setUp(self):
        api.PlayClarityTests.setUp(self)
        self.f.save.settings={**self.f.save.settings,'marriage_roll_mode':'era_annual','automation_enabled':True};self.f.session.commit()
    def test_controls_and_table_render_without_javascript(self):
        response=self.client.get('/p/relationships');self.assertEqual(response.status_code,200,response.text[:200])
        for label in ['Yearly marriage &amp; remarriage','Regional, class, faith','9000','Try to refuse'][:3]:self.assertIn(label,response.text)
        self.assertEqual(self.client.get('/p/roll-tables').status_code,200)
    def test_actual_preview_confirm_refusal_route(self):
        domain.schedule_marriage_rolls(self.f.session,self.f.save);self.f.session.commit()
        origin=self.f.session.scalar(select(Record).where(Record.data['annual_marriage'].as_boolean().is_(True)))
        response=self.client.post('/api/rolls/'+origin.id+'/complete',data={'actual':1},headers=self.headers(origin))
        self.assertEqual(response.status_code,200,response.text[:200]);preview=response.json()['preview']
        response=self.client.post('/api/previews/'+preview['token']+'/confirm',headers=self.headers())
        self.assertEqual(response.status_code,200,response.text[:200]);self.f.session.refresh(origin)
        response=self.client.post('/api/marriage-rules/rolls/'+origin.id+'/refusal',data={'marriage_save_id':self.f.save.id,'record_version':origin.version},follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text[:200])
        child=self.f.session.scalar(select(Record).where(Record.data['marriage_refusal'].as_boolean().is_(True)))
        self.assertIsNotNone(child);self.assertEqual(child.data['die'],'d6')
    def test_wrong_save_profile_and_stale_version_blocked(self):
        sim=self.f.people[0];url='/api/marriage-rules/sims/'+sim.id
        data={'marriage_save_id':'other-save','record_version':sim.version,'marriage_eligibility':'blocked'}
        self.assertEqual(self.client.post(url,data=data).status_code,409)
        data.update(marriage_save_id=self.f.save.id,record_version=-1)
        self.assertEqual(self.client.post(url,data=data).status_code,409)
    def test_profile_requires_extension_reason(self):
        sim=self.f.people[0]
        response=self.client.post('/api/marriage-rules/sims/'+sim.id,data={'marriage_save_id':self.f.save.id,
            'record_version':sim.version,'marriage_eligibility':'auto','marriage_practical_extension':'yes'},follow_redirects=False)
        self.assertEqual(response.status_code,400)

    def test_mode_change_uses_preview_and_preserves_completed_results(self):
        domain.schedule_marriage_rolls(self.f.session,self.f.save);self.f.session.commit()
        origin=self.f.session.scalar(select(Record).where(Record.data['annual_marriage'].as_boolean().is_(True)))
        domain.complete_roll(self.f.session,self.f.save,origin,8);self.f.session.commit();before=copy.deepcopy(origin.data)
        response=self.client.post('/settings',data={'settings_scope':'annual-marriage','marriage_roll_mode':'legacy'},headers=self.headers())
        self.assertEqual(response.status_code,200,response.text[:200]);preview=response.json()['preview']
        self.assertGreater(preview['counts']['retired'],0)
        self.f.session.refresh(self.f.save);self.assertTrue(rules.enabled(self.f.save))
        response=self.client.post('/api/previews/'+preview['token']+'/confirm',headers=self.headers())
        self.assertEqual(response.status_code,200,response.text[:200]);self.f.session.refresh(self.f.save)
        self.assertFalse(rules.enabled(self.f.save));self.f.session.refresh(origin);self.assertEqual(origin.data,before)

    def test_refusal_can_be_corrected_without_losing_parent_state(self):
        domain.schedule_marriage_rolls(self.f.session,self.f.save)
        origin=self.f.session.scalar(select(Record).where(Record.data['annual_marriage'].as_boolean().is_(True)))
        domain.complete_roll(self.f.session,self.f.save,origin,1);child=rules.request_refusal(self.f.session,self.f.save,origin)
        domain.complete_roll(self.f.session,self.f.save,child,1);self.f.session.commit()
        blocked=self.client.post('/api/rolls/'+origin.id+'/reopen',follow_redirects=False)
        self.assertEqual(blocked.status_code,409)
        response=self.client.post('/api/rolls/'+child.id+'/reopen',follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text[:200]);self.f.session.refresh(origin);self.f.session.refresh(child)
        self.assertEqual(origin.data['marriage_decision'],'refusal_pending')
        domain.complete_roll(self.f.session,self.f.save,child,6);self.assertEqual(origin.data['marriage_decision'],'must_marry')

    def test_successful_match_route_cannot_be_submitted_twice(self):
        domain.schedule_marriage_rolls(self.f.session,self.f.save)
        origin=self.f.session.scalar(select(Record).where(Record.data['annual_marriage'].as_boolean().is_(True)))
        domain.complete_roll(self.f.session,self.f.save,origin,1);self.f.session.commit()
        sim=self.f.session.get(Record,origin.data['sim_id']);spouse=rules.candidates(sim,self.f.save,rules.rows(self.f.session,self.f.save))[0]
        data={'marriage_save_id':self.f.save.id,'record_version':origin.version,'spouse_id':spouse.id}
        url='/api/marriage-rules/rolls/'+origin.id+'/match'
        self.assertEqual(self.client.post(url,data=data,follow_redirects=False).status_code,303)
        self.assertEqual(self.client.post(url,data=data,follow_redirects=False).status_code,409)

    def test_local_policy_can_block_class_and_faith(self):
        person=self.f.people[0];person.data={**person.data,'country':'England','social_class':'Noble','faith':'Catholic'};self.f.session.commit()
        response=self.client.post('/api/marriage-rules/policy',data={'marriage_save_id':self.f.save.id,'label':'Local ban',
            'region':'England','class':'Noble','faith':'Catholic','permission':'blocked','min_age':18},follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text[:200]);self.f.session.refresh(self.f.save)
        self.assertIn('Local ban',rules.eligibility(person,self.f.save,rules.rows(self.f.session,self.f.save)))


if __name__=='__main__':unittest.main()
