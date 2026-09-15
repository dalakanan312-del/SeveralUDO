"""Event targeting uses disposable saves, never a player's tracker."""
import copy
import unittest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app import domain,event_targets
from app.models import Base,Workspace,ChronicleSave,Record


class EventTargetTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://');Base.metadata.create_all(self.engine)
        self.s=Session(self.engine,expire_on_commit=False)
        w=Workspace(name='Targets');self.s.add(w);self.s.flush()
        self.save=ChronicleSave(workspace_id=w.id,name='Targets',global_day=20,start_year=1000,days_per_year=4,settings={})
        self.s.add(self.save);self.s.flush()
    def tearDown(self):self.s.close();self.engine.dispose()
    def record(self,kind,label,**data):
        r=Record(save_id=self.save.id,kind=kind,label=label,global_day=1,data=data)
        self.s.add(r);self.s.flush();return r
    def home(self,name='Home',**data):return self.record('household',name,**data)
    def person(self,name='Sim',home=None,**data):
        return self.record('sim',name,**{'birth_global_day':1,'sex':'Female','current_household_id':home.id if home else None,**data})
    def event(self,notes='Every Sim rolls D6: 1: Injury; 2-6: Unaffected',**data):
        return self.record('event','Event',**{'start_global_day':20,'end_global_day':28,'active':True,'roll_required':True,
            'scope':'Global','location':'Global','notes':notes,'source_roll_plan':domain.source_event_roll_plan(notes),
            'configured_die':'d6','configured_bad_results':'1','configured_result_rules':'1: Injury; 2-6: Unaffected',**data})
    def rolls(self):return list(self.s.scalars(select(Record).where(Record.kind=='roll',Record.deleted.is_(False))))
    def run_schedule(self):domain.schedule_event_rolls(self.s,self.save);self.s.commit();return self.rolls()

    def test_merapi_not_worldwide_and_once_per_affected_household(self):
        england=self.home('England',country='England');java=self.home('Merapi',country='Java',location='Near Mount Merapi')
        self.person('English',england);a=self.person('A',java);b=self.person('B',java)
        event=self.event('Households near Mount Merapi must roll a D6: 1: One Sim dies; 2-6: The household escapes',
                         location='Global / See Notes',affected_class='Affected Local Sims')
        rolls=self.run_schedule();self.assertEqual(len(rolls),1)
        roll=rolls[0];self.assertEqual(roll.data['roll_scope'],'household');self.assertIsNone(roll.data['sim_id'])
        self.assertEqual(roll.data['household_id'],java.id);self.assertEqual(set(roll.data['eligible_sim_ids']),{a.id,b.id})
        self.assertEqual(domain.schedule_event_rolls(self.s,self.save),0)

    def test_defaults_and_birthplace_do_not_override_current_country(self):
        self.save.settings={'challenge_location':'France'}
        h=self.home(country='France');sim=self.person(home=h,country='England',birthplace='France')
        event=self.event(location='France',scope='Country')
        self.assertFalse(domain._event_applies(event,sim,20,household=h,save=self.save))
        self.assertEqual(self.run_schedule(),[])

    def test_unknown_sim_does_not_borrow_an_unrelated_households_country(self):
        self.home(country='France');self.person('Unassigned')
        self.event(location='France',scope='Country')
        self.assertEqual(self.run_schedule(),[])

    def test_dated_migration_uses_location_on_due_day(self):
        sim=self.person(country='England',birthplace='France')
        move=self.record('migration','Move',sim_id=sim.id,move_global_day=15,from_country='France',to_country='England')
        event=self.event(location='France',scope='Country')
        self.assertTrue(domain._event_applies(event,sim,10,save=self.save,migrations=[move]))
        self.assertFalse(domain._event_applies(event,sim,20,save=self.save,migrations=[move]))

    def test_global_does_not_bypass_class_and_structured_restrictions(self):
        self.person('Peasant',social_class='Peasant');noble=self.person('Noble',social_class='Noble')
        self.event(affected_class='Noble Sims',eligible_classes=['Noble'])
        self.assertEqual([r.data['sim_id'] for r in self.run_schedule()],[noble.id])

    def test_outcome_mentions_do_not_restrict_everyones_sex(self):
        woman=self.person('Woman');man=self.person('Man',sex='Male')
        self.event('Every Sim rolls D6: 1: The man loses wealth; 2-6: Unaffected')
        self.assertEqual({r.data['sim_id'] for r in self.run_schedule()},{woman.id,man.id})

    def test_step_gender_is_separate_from_other_tables(self):
        man=self.person('Man',sex='Male');woman=self.person('Woman')
        plan=[{'index':0,'die':'d6','bad_results':'1','result_rules':'1: Injured','selector':'Men roll', 'parent_index':None},
              {'index':1,'die':'d4','bad_results':'2','result_rules':'2: Injured','selector':'Women roll','parent_index':None}]
        self.event('Men and women have different checks',source_roll_plan=plan)
        self.assertEqual({(r.data['sim_id'],r.data['die']) for r in self.run_schedule()},{(man.id,'d6'),(woman.id,'d4')})

    def test_life_stage_scales_for_twelve_day_years(self):
        self.save.days_per_year=12;self.save.global_day=200
        young=self.person('Still Child',birth_global_day=140);teen=self.person('Teen',birth_global_day=1)
        self.event(start_global_day=200,end_global_day=200,eligible_life_stages=['teen'])
        self.assertEqual([r.data['sim_id'] for r in self.run_schedule()],[teen.id])

    def test_household_annual_rolls_and_individual_secondary_root(self):
        h=self.home();a=self.person('A',h);b=self.person('B',h)
        plan=[{'index':0,'die':'d2','bad_results':'1','result_rules':'1: Crop loss','selector':'Each household flips a coin', 'parent_index':None,'repeat_interval_years':1},
              {'index':1,'die':'d4','bad_results':'3','result_rules':'3: Famine injury','selector':'Each Sim rolls D4','parent_index':None,'repeat_interval_years':1}]
        self.event(source_roll_plan=plan);self.save.global_day=28
        rolls=self.run_schedule();self.assertEqual(len(rolls),9)
        self.assertEqual(sum(r.data['roll_scope']=='household' for r in rolls),3)
        self.assertEqual(domain.schedule_event_rolls(self.s,self.save),0)

    def test_household_followup_fans_out_only_on_trigger(self):
        h=self.home();a=self.person('A',h);b=self.person('B',h)
        plan=[{'index':0,'die':'d2','bad_results':'1','result_rules':'1: House damaged; 2: Unaffected','selector':'Each household flips a coin','parent_index':None},
              {'index':1,'die':'d6','bad_results':'1','result_rules':'1: Injury','selector':'Each affected Sim rolls','parent_index':0,'trigger_results':'1'}]
        self.event(source_roll_plan=plan)
        root=self.run_schedule()[0]
        self.assertEqual(domain._schedule_event_followup(self.s,self.save,root,2),0)
        self.assertEqual(domain._schedule_event_followup(self.s,self.save,root,1),2)
        self.assertEqual(domain._schedule_event_followup(self.s,self.save,root,1),0)
        children=[r for r in self.rolls() if r.id!=root.id]
        self.assertEqual({r.data['sim_id'] for r in children},{a.id,b.id})

    def test_legacy_pending_rolls_retire_but_completed_result_is_unchanged(self):
        h=self.home();a=self.person('A',h);b=self.person('B',h)
        e=self.event('Each household rolls D6: 1: Loss; 2-6: Unaffected')
        done=self.record('roll','Old complete',event_id=e.id,sim_id=a.id,source=f'event:{e.id}:{a.id}',source_roll_plan_index=0,completed=True,actual=2)
        done.global_day=20
        pending=self.record('roll','Old pending',event_id=e.id,sim_id=b.id,source=f'event:{e.id}:{b.id}',source_roll_plan_index=0,completed=False)
        pending.global_day=20;self.s.commit();before=copy.deepcopy(done.data)
        self.run_schedule();self.assertTrue(pending.deleted);self.assertEqual(done.data,before)
        self.assertEqual(self.rolls(),[done]);self.assertIn('household',pending.data['retired_reason'])

    def test_unknown_local_target_never_means_everyone(self):
        self.person(country='England')
        self.event('Affected local households roll D6: 1: Loss; 2-6: Unaffected',location='Global / See Notes',affected_class='Affected Local Sims')
        self.assertEqual(self.run_schedule(),[])

    def test_actual_worldwide_event_still_applies_without_location(self):
        self.person('A');self.person('B');self.event(location='Global / See Notes')
        self.assertEqual(len(self.run_schedule()),2)

    def test_location_aliases_never_match_substrings(self):
        self.assertFalse(domain._event_location_matches('US','Austria'))
        self.assertTrue(domain._event_location_matches('Europe','England'))
        self.assertFalse(domain._event_location_matches('England','Europe'))

    def test_unit_is_not_inferred_from_result_description(self):
        self.assertEqual(event_targets.roll_scope({'notes':'Every Sim rolls D6: 1: The household loses money'}),'sim')
        self.assertEqual(event_targets.roll_scope({'notes':'Roll D6: 1: One household loses money'}),'sim')

    def test_livestock_table_is_never_given_to_human_sims(self):
        human=self.person('Person');animal=self.person('Horse',game_species='Horse')
        plan=[{'index':0,'die':'d10','bad_results':'1','result_rules':'1: Dies','selector':'Roll for animals','parent_index':None},
              {'index':1,'die':'d12','bad_results':'2','result_rules':'2: Dies','selector':'Each Sim rolls','parent_index':None}]
        self.event(source_roll_plan=plan)
        self.assertEqual({(r.data['sim_id'],r.data['die']) for r in self.run_schedule()},{(human.id,'d12'),(animal.id,'d10')})

    def test_one_household_death_selects_only_one_member(self):
        h=self.home();a=self.person('A',h);b=self.person('B',h)
        self.event('Each household rolls D6: 1: One Sim dies; 2-6: Unaffected')
        roll=self.run_schedule()[0];domain.complete_roll(self.s,self.save,roll,1);self.s.commit()
        self.assertIn(roll.data['household_victim_sim_id'],{a.id,b.id})
        self.assertEqual(sum(s.data.get('death_global_day') is not None for s in (a,b)),1)
        self.assertEqual(len(self.rolls()),1)

    def test_household_losses_do_not_kill_a_representative(self):
        h=self.home();a=self.person('A',h);b=self.person('B',h)
        self.event('Each household rolls D6: 1: Home destroyed; 2-6: Unaffected')
        roll=self.run_schedule()[0];domain.complete_roll(self.s,self.save,roll,1);self.s.commit()
        self.assertIsNone(roll.data['sim_id']);self.assertFalse(a.data.get('death_global_day'));self.assertFalse(b.data.get('death_global_day'))

    def test_configured_household_followup_survives_refresh(self):
        h=self.home();a=self.person('A',h);b=self.person('B',h)
        self.event('Each household rolls D6: 1: House damaged; 2-6: Unaffected',source_roll_plan=[],
            followup_enabled=True,followup_die='d4',followup_bad_results='1: Injury',followup_trigger_results='1',followup_label='Per Sim injury check')
        root=self.run_schedule()[0]
        self.assertEqual(domain._schedule_event_followup(self.s,self.save,root,1),2)
        domain.repair_pending_event_rolls(self.s,self.save);self.s.commit()
        children=[r for r in self.rolls() if r.id!=root.id]
        self.assertEqual(len(children),2);self.assertTrue(all(r.data['die']=='d4' for r in children))

    def test_source_profession_requires_evidence(self):
        h=self.home('Farm',livelihood='Farming');other=self.home('Shop',livelihood='Trading')
        a=self.person('Farmer',h);self.person('Trader',other)
        self.event('Each farming household rolls D6: 1: Lost crop; 2-6: Unaffected')
        rolls=self.run_schedule();self.assertEqual(len(rolls),1);self.assertEqual(rolls[0].data['household_id'],h.id)

    def test_main_household_only_means_main_household(self):
        main=self.home('Main');side=self.home('Side');self.save.settings={'main_household_id':main.id}
        self.person('A',main);self.person('B',side)
        self.event('The main household rolls D6: 1: Loss; 2-6: Unaffected')
        rolls=self.run_schedule();self.assertEqual(len(rolls),1);self.assertEqual(rolls[0].data['household_id'],main.id)

    def test_refresh_keeps_separate_gender_dice(self):
        self.person('Woman');self.person('Man',sex='Male')
        self.event(die_by_sex={'female':'d4','male':'d6'},result_rules_by_sex={'female':'1: Injury','male':'2: Injury'})
        rolls=self.run_schedule();before={r.id:copy.deepcopy(r.data) for r in rolls}
        self.assertEqual(domain.schedule_event_rolls(self.s,self.save),0)
        self.assertEqual({r.id:r.data for r in rolls},before)

    def test_old_wrong_location_roll_is_archived_without_replacing_it(self):
        a=self.person(country='England');e=self.event(location='France',scope='Country')
        old=self.record('roll','Wrong place',source=f'event:{e.id}:{a.id}',event_id=e.id,sim_id=a.id,completed=False)
        old.global_day=20;self.s.commit()
        self.run_schedule();self.assertTrue(old.deleted);self.assertTrue(old.data['event_target_repair'])
        self.assertEqual(self.rolls(),[]);self.assertEqual(domain.schedule_event_rolls(self.s,self.save),0)


if __name__=='__main__':unittest.main()
