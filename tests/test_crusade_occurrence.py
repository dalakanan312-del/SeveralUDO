"""The imported Crusades rule is an occurrence check, then per-Sim survival."""
import copy
import unittest
from sqlalchemy import select
from app import action_previews, domain, play_clarity
from app.models import Record
from tests import test_event_targets as fixtures

NOTES = 'Roll a D20 every year to see if there will be a crusade 4 means there is Roll a D12 for all sims when there is a crusade 3 means that sim dies'
CATALOG = 'EVT-1000S-0180'


class CrusadeOccurrenceTests(unittest.TestCase):
    setUp=fixtures.EventTargetTests.setUp
    tearDown=fixtures.EventTargetTests.tearDown
    record=fixtures.EventTargetTests.record
    home=fixtures.EventTargetTests.home
    person=fixtures.EventTargetTests.person
    event=fixtures.EventTargetTests.event
    rolls=fixtures.EventTargetTests.rolls
    run_schedule=fixtures.EventTargetTests.run_schedule

    def crusade(self, broken=False, **extra):
        plan=(domain.source_event_roll_plan(NOTES) if broken else
              domain.event_source_roll_plan(NOTES,domain.ORIGINAL_EVENT_ROLL_OVERRIDES[CATALOG],CATALOG))
        row=self.event(NOTES,catalog_id=CATALOG,source_roll_plan=plan,
            configured_die='d20',configured_bad_results='4',configured_result_rules=plan[0]['result_rules'],**extra)
        row.label='Crusades';self.s.commit();return row

    def children(self,root):
        return [r for r in self.rolls() if r.data.get('origin_roll_id')==root.id]

    def test_one_occurrence_per_year_not_per_sim_or_household(self):
        self.person('A',self.home('One'));self.person('B',self.home('Two'));self.person('No household')
        self.crusade();self.save.global_day=28
        roots=self.run_schedule()
        self.assertEqual(sorted(r.global_day for r in roots),[20,24,28])
        self.assertTrue(all(r.data['die']=='d20' and r.data['roll_scope']=='event' for r in roots))
        self.assertTrue(all(r.data['sim_id'] is None and r.data['household_id'] is None for r in roots))
        self.assertTrue(all(len(r.data['eligible_sim_ids'])==3 for r in roots))
        self.assertEqual(domain.schedule_event_rolls(self.s,self.save),0)

    def test_twelve_day_years_and_event_end_are_honored(self):
        self.person();self.save.days_per_year=12;self.save.global_day=60
        self.crusade(end_global_day=44)
        self.assertEqual(sorted(r.global_day for r in self.run_schedule()),[20,32,44])

    def test_four_schedules_separate_survival_rolls_without_killing_anyone(self):
        home_a=self.home('A');home_b=self.home('B');a=self.person('A',home_a);b=self.person('B',home_b)
        self.crusade();root=self.run_schedule()[0]
        result=domain.complete_roll(self.s,self.save,root,4);self.s.commit()
        self.assertEqual(result['outcome'],'A crusade occurs');self.assertEqual(result['automatic_followups'],2)
        children=self.children(root)
        self.assertEqual({r.data['sim_id'] for r in children},{a.id,b.id})
        self.assertEqual({r.data['household_id'] for r in children},{home_a.id,home_b.id})
        self.assertTrue(all(r.data['die']=='d12' and r.data['bad_results']=='3' for r in children))
        self.assertFalse(a.data.get('death_global_day'));self.assertFalse(b.data.get('death_global_day'))
        self.assertEqual(domain._schedule_event_followup(self.s,self.save,root,4),0)
        expected_ids={r.id for r in self.rolls()}
        domain.schedule_event_rolls(self.s,self.save)
        self.assertEqual({r.id for r in self.rolls()},expected_ids)
        self.assertEqual(domain.schedule_event_rolls(self.s,self.save),0)

    def test_other_results_do_not_schedule_survival(self):
        self.person();self.crusade();root=self.run_schedule()[0]
        for actual in range(1,21):
            if actual!=4:self.assertEqual(domain._schedule_event_followup(self.s,self.save,root,actual),0)
        result=domain.complete_roll(self.s,self.save,root,9)
        self.assertEqual(result['outcome'],'No crusade this year');self.assertEqual(self.children(root),[])

    def test_only_survival_result_three_schedules_death(self):
        a=self.person('A');b=self.person('B');self.crusade();root=self.run_schedule()[0]
        domain.complete_roll(self.s,self.save,root,4)
        children={r.data['sim_id']:r for r in self.children(root)}
        start,end=domain._death_window(self.s,self.save,children[a.id],a)
        domain.complete_roll(self.s,self.save,children[a.id],3)
        domain.complete_roll(self.s,self.save,children[b.id],12);self.s.commit()
        self.assertLessEqual(start,a.data['death_global_day']);self.assertLessEqual(a.data['death_global_day'],end)
        self.assertEqual(a.data['death_source_roll_id'],children[a.id].id);self.assertFalse(b.data.get('death_global_day'))

    def test_preview_lists_real_followups_and_rolls_back_until_confirmed(self):
        self.person('A');self.person('B');self.crusade();root=self.run_schedule()[0]
        plan=action_previews.simulate(self.s,self.save,lambda:domain.complete_roll(self.s,self.save,root,4))
        description=action_previews.describe(plan,'roll')
        self.assertEqual(description['counts']['added'],2)
        self.assertTrue(any('Crusade survival' in effect for effect in description['effects']))
        self.assertFalse(any('no automatic follow-up' in effect for effect in description['effects']))
        self.assertEqual(self.children(root),[]);self.assertFalse(root.data['completed'])

    def test_excludes_dead_frozen_unborn_and_animals_and_rechecks_before_followup(self):
        living=self.person('Living');later_dead=self.person('Later dead')
        self.person('Dead',death_global_day=19);self.person('Frozen',infinite_frozen=True)
        self.person('Not born',birth_global_day=21);self.person('Horse',game_species='Horse')
        self.crusade();root=self.run_schedule()[0]
        self.assertEqual(set(root.data['eligible_sim_ids']),{living.id,later_dead.id})
        later_dead.data={**later_dead.data,'death_global_day':20};self.s.commit()
        domain.complete_roll(self.s,self.save,root,4)
        self.assertEqual([r.data['sim_id'] for r in self.children(root)],[living.id])

    def test_repairs_only_pending_broken_roots_and_orphan_survival(self):
        a=self.person('A');b=self.person('B');event=self.crusade(broken=True)
        old=[]
        for step,die,bad in [(0,'d20','4'),(1,'d12','3')]:
            for sim in (a,b):
                r=self.record('roll','Old',event_id=event.id,sim_id=sim.id,source=f'event:{event.id}:{sim.id}:step:{step}:occurrence:20',
                    source_roll_plan_index=step,source_roll_plan_root=True,die=die,bad_results=bad,completed=False)
                r.global_day=20;old.append(r)
        self.s.commit();roots=self.run_schedule()
        self.assertTrue(all(r.deleted for r in old));self.assertEqual(len(roots),1)
        self.assertEqual(roots[0].data['roll_scope'],'event');self.assertTrue(event.data['source_occurrence_repaired'])
        self.assertEqual(domain.schedule_event_rolls(self.s,self.save),0)

    def test_completed_history_is_not_changed_replayed_or_rolled_again(self):
        sim=self.person();event=self.crusade(broken=True)
        old=self.record('roll','Already decided',event_id=event.id,sim_id=sim.id,
            source=f'event:{event.id}:{sim.id}:occurrence:20',source_roll_plan_index=0,
            die='d20',bad_results='4',actual=4,outcome='there is Roll a',completed=True)
        old.global_day=20;self.s.commit();before=copy.deepcopy(old.data)
        self.run_schedule();self.assertEqual(old.data,before);self.assertFalse(old.deleted)
        self.assertEqual(self.children(old),[]);self.assertEqual(self.rolls(),[old])
        self.save.global_day=24;self.run_schedule()
        self.assertEqual([r.global_day for r in self.rolls() if not r.data.get('completed')],[24])

    def test_custom_and_frozen_source_tables_are_not_replaced(self):
        self.person();event=self.crusade(broken=True)
        event.data={**event.data,'notes':NOTES+' Player custom edit.'};self.s.commit()
        original=copy.deepcopy(event.data)
        self.assertEqual(domain.repair_source_occurrence_plans(self.s,self.save,[event]),0)
        self.assertEqual(event.data,original)
        event.data={**event.data,'notes':NOTES,'infinite_frozen':True}
        self.assertEqual(domain.repair_source_occurrence_plans(self.s,self.save,[event]),0)

    def test_disabled_automation_does_not_create_followups(self):
        self.person();self.crusade();root=self.run_schedule()[0]
        self.save.settings={'automation_enabled':False}
        domain.complete_roll(self.s,self.save,root,4);self.assertEqual(self.children(root),[])

    def test_custom_event_table_is_preserved_even_with_stale_parsed_plan(self):
        event=self.crusade(broken=True)
        original=copy.deepcopy(event.data)
        for field,value in [('configured_die','d6'),('die','d8'),('configured_bad_results','2'),
                            ('bad_results','5'),('configured_result_rules','4: Injury'),('roll_scope','household')]:
            with self.subTest(field=field):
                event.data={**original,field:value};before=copy.deepcopy(event.data)
                self.assertEqual(domain.repair_source_occurrence_plans(self.s,self.save,[event]),0)
                self.assertEqual(event.data,before)

    def test_fresh_catalog_uses_linked_plan(self):
        domain.seed_event_catalog(self.s,self.save)
        row=next(r for r in self.s.scalars(select(Record).where(Record.kind=='event')) if r.data.get('catalog_id')==CATALOG)
        self.assertEqual(row.data['source_roll_plan'][0]['roll_scope'],'event')
        self.assertEqual(row.data['source_roll_plan'][1]['parent_index'],0)

    def test_shared_occurrence_is_visible_in_each_affected_household_batch(self):
        a=self.home('One');b=self.home('Two');empty=self.home('Uninvolved')
        self.person('A',a);self.person('B',b);self.crusade();root=self.run_schedule()[0]
        for home in (a,b,empty):
            found=self.s.scalar(select(Record.id).where(Record.id==root.id,play_clarity.household_predicate(self.save,home.id)))
            self.assertEqual(bool(found),home!=empty)

    def test_late_import_is_included_only_if_alive_when_the_event_occurred(self):
        a=self.person('First');self.crusade();root=self.run_schedule()[0]
        b=self.person('Imported later',birth_global_day=1);self.person('Born after event',birth_global_day=21)
        self.save.global_day=24;domain.complete_roll(self.s,self.save,root,4)
        self.assertEqual({r.data['sim_id'] for r in self.children(root)},{a.id,b.id})


from tests import test_play_clarity as api_fixtures

class CrusadePreviewTests(unittest.TestCase):
    setUp=api_fixtures.PlayClarityTests.setUp
    tearDown=api_fixtures.PlayClarityTests.tearDown
    add=api_fixtures.PlayClarityTests.add
    headers=api_fixtures.PlayClarityTests.headers

    def test_review_and_confirm_produces_the_advertised_survival_checks(self):
        self.f.save.settings={**self.f.save.settings,'automation_enabled':True};self.f.session.commit()
        plan=domain.event_source_roll_plan(NOTES,domain.ORIGINAL_EVENT_ROLL_OVERRIDES[CATALOG])
        event=self.add('event','Crusades',start_global_day=100,end_global_day=100,scope='Global',location='Global',
            active=True,roll_required=True,notes=NOTES,source_roll_plan=plan,configured_die='d20',configured_bad_results='4')
        domain.schedule_event_rolls(self.f.session,self.f.save);self.f.session.commit()
        root=self.f.session.scalar(select(Record).where(Record.kind=='roll',Record.data['event_id'].as_string()==event.id))
        response=self.client.post('/api/rolls/'+root.id+'/complete',data={'actual':4},headers=self.headers(root))
        self.assertEqual(response.status_code,200,response.text[:500]);preview=response.json()['preview']
        self.assertEqual(preview['outcome'],'A crusade occurs');self.assertEqual(preview['counts']['added'],5)
        self.assertTrue(all('Crusade survival' in effect for effect in preview['effects']))
        response=self.client.post('/api/previews/'+preview['token']+'/confirm',headers=self.headers(root))
        self.assertEqual(response.status_code,200,response.text[:500])
        children=list(self.f.session.scalars(select(Record).where(Record.data['origin_roll_id'].as_string()==root.id)))
        self.assertEqual(len(children),5)


if __name__=='__main__':unittest.main()
