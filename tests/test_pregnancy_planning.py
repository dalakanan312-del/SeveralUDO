import copy
import unittest
from sqlalchemy import select
from app import domain, main, pregnancy_planning as planning, roll_automation as auto, roll_automation_ui as web, usability_ui
from app.models import Record
from tests import test_play_clarity as fixtures


class PregnancyPlanningTests(unittest.TestCase):
    setUp=fixtures.PlayClarityTests.setUp
    tearDown=fixtures.PlayClarityTests.tearDown
    add=fixtures.PlayClarityTests.add

    def prepare(self, **settings):
        self.f.save.settings={**self.f.save.settings,'automation_enabled':True,'roll_automation':{
            'pregnancy':True,'pregnancy_yearly':True,'pregnancy_yearly_die':'d6','pregnancy_yearly_success':'1-2',
            'pregnancy_yearly_mode':'custom',
            'pregnancy_side_only':False,'pregnancy_married_only':False,**settings}}
        self.sim=self.f.people[0]
        self.sim.data={**self.sim.data,'sex':'Female'}
        self.rule=self.add('planner_rule','Side Household Pregnancy',die='d6',bad_results='1-5: Schedule that many pregnancies; 6: No pregnancy',active=True)
        self.f.session.commit()

    def allowance(self, result=3):
        roll,_=domain.create_pregnancy_count_roll(self.f.session,self.f.save,self.sim)
        domain.complete_roll(self.f.session,self.f.save,roll,result)
        self.f.session.commit()
        return roll

    def annual(self):
        return self.f.session.scalar(select(Record).where(Record.save_id==self.f.save.id,Record.kind=='roll',Record.deleted.is_(False),Record.data['pregnancy_yearly_roll'].as_boolean().is_(True)))

    def test_one_lifetime_count_across_years_and_era_changes(self):
        self.prepare();roll=self.allowance()
        self.f.save.global_day+=12
        again,created=domain.create_pregnancy_count_roll(self.f.session,self.f.save,self.sim)
        self.assertFalse(created);self.assertEqual(again.id,roll.id)
        self.assertEqual(planning.status(self.f.session,self.f.save,self.sim)['current']['allowed'],3)
        self.assertEqual(len(planning.count_rolls(self.f.session,self.f.save,self.sim)),1)

    def test_separate_yearly_rolls_wait_for_allowance_and_respect_scaled_year(self):
        self.prepare();self.f.save.days_per_year=12;self.sim.data={**self.sim.data,'birth_global_day':-200}
        auto.schedule(self.f.session,self.f.save);self.assertIsNone(self.annual())
        self.allowance();first=self.annual();self.assertIsNotNone(first)
        self.assertEqual(first.data['die'],'d6');self.assertFalse(first.data.get('pregnancy_count_roll'))
        domain.complete_roll(self.f.session,self.f.save,first,6)
        self.f.save.global_day=108;auto.schedule(self.f.session,self.f.save)
        self.assertEqual(self.annual().id,first.id)
        self.f.save.global_day=109;auto.schedule(self.f.session,self.f.save)
        rows=list(self.f.session.scalars(select(Record).where(Record.kind=='roll',Record.data['pregnancy_yearly_roll'].as_boolean().is_(True))))
        self.assertEqual(len(rows),2);self.assertEqual(len(planning.count_rolls(self.f.session,self.f.save,self.sim)),1)

    def test_allowance_used_across_years_twins_count_once(self):
        self.prepare();self.allowance()
        for day,babies,status in [(50,2,'Delivered'),(75,3,'Stillborn')]:
            self.add('pregnancy','Recorded pregnancy',day,mother_id=self.sim.id,conception_global_day=day,babies_delivered=babies,status=status)
        self.add('pregnancy','Cancelled',80,mother_id=self.sim.id,conception_global_day=80,status='Cancelled')
        self.add('pregnancy','Future',150,mother_id=self.sim.id,conception_global_day=150,status='Planned')
        state=planning.status(self.f.session,self.f.save,self.sim)['current']
        self.assertEqual((state['allowed'],state['used'],state['remaining']),(3,2,1))
        plan=self.f.session.scalar(select(Record).where(Record.kind=='family_plan',Record.data['sim_id'].as_string()==self.sim.id))
        self.assertIsNone(plan.data['planner_year']);self.assertEqual(plan.data['pregnancy_allowance_scope'],'lifetime')

    def test_success_creates_task_not_pregnancy_or_allowance_change(self):
        self.prepare();self.allowance();roll=self.annual()
        result=domain.complete_roll(self.f.session,self.f.save,roll,2);self.f.session.commit()
        self.assertIn('Have one baby',result['outcome']);self.assertIsNone(self.sim.data.get('death_global_day'))
        self.assertEqual(planning.status(self.f.session,self.f.save,self.sim)['current']['used'],0)
        tasks=list(self.f.session.scalars(select(Record).where(Record.kind=='task',Record.data['pregnancy_yearly_task'].as_boolean().is_(True))))
        self.assertEqual(len(tasks),1)
        groups=usability_ui.board(self.f.session,self.f.save,{})['board_groups']
        self.assertIn(tasks[0].id,[r.id for g in groups if g['id']=='decisions' for r in g['rows']])
        self.assertIsNone(self.f.session.scalar(select(Record).where(Record.kind=='pregnancy')))
        pregnancy=self.add('pregnancy','Actual pregnancy',mother_id=self.sim.id,conception_global_day=100,status='Active')
        auto.schedule(self.f.session,self.f.save);self.assertFalse(tasks[0].data['completed'])
        self.assertEqual(planning.status(self.f.session,self.f.save,self.sim)['current']['remaining'],2)
        pregnancy.data={**pregnancy.data,'status':'Delivered','actual_delivery_global_day':100}
        auto.schedule(self.f.session,self.f.save);self.assertTrue(tasks[0].data['completed'])

    def test_zero_allowance_and_infertility_block_yearly_rolls(self):
        self.prepare();self.allowance(6);self.assertIsNone(self.annual())
        with self.assertRaisesRegex(ValueError,'used their lifetime'):planning.create_annual(self.f.session,self.f.save,self.sim)

    def test_existing_results_preserved_pending_duplicates_retired(self):
        self.prepare(pregnancy_yearly=False)
        first=self.add('roll','Original',60,sim_id=self.sim.id,pregnancy_count_roll=True,pregnancy_count=5,actual=5,completed=True,planner_year=1314,completed_global_day=60)
        latest=self.add('roll','Later old result',80,sim_id=self.sim.id,pregnancy_count_roll=True,pregnancy_count=3,actual=3,completed=True,planner_year=1319,completed_global_day=80)
        pending=self.add('roll','Duplicate',sim_id=self.sim.id,pregnancy_count_roll=True,completed=False)
        self.sim.data={**self.sim.data,'pregnancy_allowance_roll_id':latest.id,'pregnancy_allowance_count':3}
        original=[copy.deepcopy(r.data) for r in [first,latest]]
        self.assertGreater(planning.reconcile(self.f.session,self.f.save),0)
        self.assertTrue(pending.deleted);self.assertEqual([first.data,latest.data],original)
        self.assertEqual(planning.status(self.f.session,self.f.save,self.sim)['current']['allowed'],3)
        self.assertEqual(planning.reconcile(self.f.session,self.f.save),0)

    def test_missing_yearly_odds_not_invented(self):
        self.prepare(pregnancy_yearly_die='',pregnancy_yearly_success='')
        self.allowance();self.assertIsNone(self.annual())
        with self.assertRaisesRegex(ValueError,'custom odds are missing'):planning.create_annual(self.f.session,self.f.save,self.sim)
        for cfg in ({'pregnancy_yearly_die':'d6','pregnancy_yearly_success':'7'},{'pregnancy_yearly_die':'d1','pregnancy_yearly_success':'1'}):
            with self.assertRaises(ValueError):planning.annual_table(cfg)

    def test_yearly_outcome_does_not_change_lifetime_total(self):
        self.prepare();self.allowance();roll=self.annual()
        result=domain.complete_roll(self.f.session,self.f.save,roll,6)
        self.assertEqual(result['outcome'],'No pregnancy this year')
        self.assertEqual(self.sim.data['pregnancy_allowance_count'],3)
        self.assertEqual(planning.status(self.f.session,self.f.save,self.sim)['current']['used'],0)
        self.assertFalse(roll.data.get('pregnancy_count_roll'))

    def test_prior_year_conception_delivered_this_year_blocks_extra_attempt(self):
        self.prepare();self.allowance();roll=self.annual()
        self.add('pregnancy','Delivered this year',99,mother_id=self.sim.id,conception_global_day=95,actual_delivery_global_day=99,status='Delivered')
        self.assertIn('birth is already recorded',planning.annual_reason(self.f.session,self.f.save,self.sim))
        auto.schedule(self.f.session,self.f.save)
        self.assertTrue(roll.deleted)

    def test_imported_birth_without_pregnancy_blocks_extra_attempt(self):
        self.prepare();self.allowance();roll=self.annual()
        child=self.add('sim','Future child',101,birth_global_day=101,mother_id=self.sim.id)
        self.assertEqual(planning.annual_reason(self.f.session,self.f.save,self.sim),'')
        child.data={**child.data,'birth_global_day':99};self.f.session.commit()
        auto.schedule(self.f.session,self.f.save)
        self.assertTrue(roll.deleted)

    def test_invalid_imported_custom_odds_do_not_break_scheduling(self):
        self.prepare(pregnancy_yearly_success='not a range')
        self.allowance()
        auto.schedule(self.f.session,self.f.save)
        self.assertIsNone(self.annual())

    def test_expired_pending_yearly_check_is_retired(self):
        self.prepare();self.allowance();old=self.annual()
        self.f.save.global_day+=4;auto.schedule(self.f.session,self.f.save)
        self.assertTrue(old.deleted);self.assertNotEqual(self.annual().id,old.id)

    def test_manual_forms_and_independent_toggles(self):
        self.prepare(pregnancy_yearly=False);self.allowance();self.assertIsNone(self.annual())
        response=self.client.post('/api/today/pregnancy-count-rolls',data={'sim_id':self.sim.id,'save_id':self.f.save.id,'view':'workboard','pregnancy_kind':'yearly'},follow_redirects=False)
        self.assertEqual(response.status_code,303);self.assertIsNotNone(self.annual())
        page=self.client.get('/p/roll-automation')
        for field in ('pregnancy','pregnancy_yearly','pregnancy_yearly_die','pregnancy_yearly_success'):
            self.assertIn('name="'+field+'"',page.text)
        self.assertIn('Lifetime allowance',self.client.get('/p/today').text)

    def test_supplied_age_bands_and_no_natural_roll_over_49(self):
        self.prepare(pregnancy_yearly_mode='age_table')
        start=97
        for age,base in [(13,1),(17,1),(18,7),(24,7),(25,6),(29,6),(30,5),(34,5),(35,4),(39,4),(40,2),(44,2),(45,1),(49,1)]:
            self.sim.data={**self.sim.data,'birth_global_day':start-age*4}
            self.assertEqual(planning.annual_specification(self.f.session,self.f.save,self.sim)['base'],base)
        for age in (12,50,65):
            self.sim.data={**self.sim.data,'birth_global_day':start-age*4}
            self.assertIsNone(planning.annual_specification(self.f.session,self.f.save,self.sim))

    def test_user_married_27_example_and_previous_birth(self):
        self.prepare(pregnancy_yearly_mode='age_table')
        self.sim.data={**self.sim.data,'birth_global_day':97-27*4}
        self.add('relationship','Marriage',80,type='Marriage',partner1_id=self.sim.id,partner2_id=self.f.people[1].id,status='Active')
        self.assertEqual(planning.annual_specification(self.f.session,self.f.save,self.sim)['success_results'],'1-7')
        self.add('pregnancy','Previous birth',94,mother_id=self.sim.id,status='Delivered',actual_delivery_global_day=94,conception_global_day=90)
        spec=planning.annual_specification(self.f.session,self.f.save,self.sim)
        self.assertEqual(spec['success_results'],'1-3')
        self.assertEqual([m['value'] for m in spec['modifiers']],[1,-4])

    def test_modifier_caps_heir_fertility_and_infant_household(self):
        self.prepare(pregnancy_yearly_mode='age_table')
        self.sim.data={**self.sim.data,'birth_global_day':97-20*4,'pregnancy_fertility_boost':True,'pregnancy_established_partnership':True}
        self.f.save.settings={**self.f.save.settings,'current_heir_id':self.sim.id}
        spec=planning.annual_specification(self.f.session,self.f.save,self.sim)
        self.assertEqual(spec['success_results'],'1-10')
        self.add('sim','Toddler',94,birth_global_day=94,current_household_id=self.sim.data.get('current_household_id'))
        spec=planning.annual_specification(self.f.session,self.f.save,self.sim)
        self.assertEqual(spec['success_results'],'1-9')
        self.assertIn(-2,[m['value'] for m in spec['modifiers']])
        self.sim.data={**self.sim.data,'birth_global_day':97-45*4,'pregnancy_established_partnership':False,'pregnancy_fertility_boost':False}
        self.assertEqual(planning.annual_specification(self.f.session,self.f.save,self.sim)['success_results'],'1')

    def test_teen_and_household_rule_permissions(self):
        self.prepare(pregnancy_yearly_mode='age_table',pregnancy_min_age=13)
        self.sim.data={**self.sim.data,'birth_global_day':97-16*4}
        self.assertIn('Confirm',planning.eligibility_reason(self.f.session,self.f.save,self.sim))
        self.sim.data={**self.sim.data,'pregnancy_rule_permission':'allowed'}
        self.assertEqual(planning.eligibility_reason(self.f.session,self.f.save,self.sim),'')
        self.f.home.data={**self.f.home.data,'pregnancy_rule_permission':'blocked'}
        self.assertIn('not permitted',planning.eligibility_reason(self.f.session,self.f.save,self.sim))

    def test_default_age_table_has_ready_to_use_odds(self):
        self.prepare(pregnancy_yearly_mode='age_table',pregnancy_yearly_die='',pregnancy_yearly_success='')
        self.allowance();annual=self.annual();self.assertIsNotNone(annual)
        self.assertEqual(annual.data['die'],'d20')
        self.assertEqual(annual.data['annual_baby_spec']['mode'],'age_table')
        self.assertIn('Annual baby modifiers',self.client.get('/p/today').text)

    def test_reopen_yearly_result_retracts_unfinished_task(self):
        self.prepare();self.allowance();roll=self.annual()
        domain.complete_roll(self.f.session,self.f.save,roll,1);self.f.session.commit()
        task=self.f.session.scalar(select(Record).where(Record.kind=='task',Record.data['source_pregnancy_yearly_roll_id'].as_string()==roll.id))
        response=self.client.post('/api/rolls/'+roll.id+'/reopen',follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.f.session.refresh(roll);self.f.session.refresh(task)
        self.assertTrue(task.deleted);self.assertFalse(roll.data.get('completed'))
        domain.complete_roll(self.f.session,self.f.save,roll,1)
        self.assertFalse(task.deleted)

    def test_original_legacy_dismissal_does_not_return_as_lifetime(self):
        self.prepare(pregnancy_yearly=False)
        row=self.add('roll','Dismissed old yearly count',sim_id=self.sim.id,pregnancy_count_roll=True,completed=False,source='planner:pregnancy-count:'+self.sim.id+':1324')
        row.deleted=True;self.f.session.commit()
        auto.schedule(self.f.session,self.f.save)
        self.assertEqual(planning.count_rolls(self.f.session,self.f.save,self.sim),[])

    def test_settings_accept_source_table_without_custom_numbers(self):
        self.prepare(pregnancy_yearly=False)
        response=self.client.post('/api/roll-automation/settings',data={
            'save_id':self.f.save.id,'config_stamp':web.stamp(self.f.save),'pregnancy':'on','pregnancy_yearly':'on',
            'pregnancy_yearly_mode':'age_table','pregnancy_yearly_die':'','pregnancy_yearly_success':'',
            'pregnancy_min_age':'13','pregnancy_max_age':'49'},follow_redirects=False)
        self.assertEqual(response.status_code,303,response.text)
        self.f.session.refresh(self.f.save)
        self.assertEqual(auto.config(self.f.save)['pregnancy_yearly_mode'],'age_table')

    def test_yearly_success_preview_does_not_create_tasks_before_confirm(self):
        from app import action_previews
        self.prepare();self.allowance();roll=self.annual()
        before=list(self.f.session.scalars(select(Record).where(Record.kind=='task')))
        with self.f.session.begin_nested() as transaction:
            domain.complete_roll(self.f.session,self.f.save,roll,1)
            self.assertIsNotNone(self.f.session.scalar(select(Record).where(Record.kind=='task',Record.data['pregnancy_yearly_task'].as_boolean().is_(True))))
            transaction.rollback()
        self.assertEqual(list(self.f.session.scalars(select(Record).where(Record.kind=='task'))),before)
