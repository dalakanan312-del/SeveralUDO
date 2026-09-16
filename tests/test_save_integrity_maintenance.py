"""Regression tests for save-audit corrections, using a disposable database."""
import copy
import unittest
from app import domain, insights
from app.models import Record
from tests.test_infinite_decades import InfiniteDecadesTests


class SaveIntegrityMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.f = InfiniteDecadesTests()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def test_generation_reads_frozen_parent_without_changing_history(self):
        parent, _, child, spouse, grandchild = self.f.people
        parent.data = {**parent.data, 'generation': 0, 'generation_source': 'manual',
                       'infinite_frozen': True}
        parent.deleted = True
        child.data = {**child.data, 'generation': None}
        grandchild.data = {**grandchild.data, 'mother_id': child.id}
        self.f.session.add(Record(save_id=self.f.save.id, kind='relationship', label='Marriage',
            data={'partner1_id':child.id, 'partner2_id':spouse.id, 'type':'Marriage', 'status':'Widowed'}))
        self.f.session.commit()
        before = (copy.deepcopy(parent.data), parent.version, parent.deleted)
        self.assertGreaterEqual(domain.sync_generations(self.f.session, self.f.save), 3)
        self.f.session.commit()
        self.assertEqual(child.data['generation'], 1)
        self.assertEqual(spouse.data['generation'], 1)
        self.assertEqual(grandchild.data['generation'], 2)
        self.assertEqual((parent.data, parent.version, parent.deleted), before)
        self.assertEqual(domain.sync_generations(self.f.session, self.f.save), 0)

    def test_ordinary_archived_parent_is_not_restored(self):
        parent, _, child, *_ = self.f.people
        parent.deleted = True
        parent.data = {**parent.data, 'generation': 6}
        self.f.session.commit()
        domain.sync_generations(self.f.session, self.f.save)
        self.assertIsNone(child.data.get('generation'))

    def test_health_accepts_frozen_parent_but_flags_real_missing_reference(self):
        parent, _, child, *_ = self.f.people
        parent.deleted = True
        parent.data = {**parent.data, 'infinite_frozen':True}
        child.data = {**child.data, 'father_id':'not-a-sim'}
        rows = self.f.people + [self.f.home]
        issues = insights.health_report(rows, self.f.save)['issues']
        self.assertFalse(any('missing mother reference' in i['message'] for i in issues))
        self.assertTrue(any('missing father reference' in i['message'] for i in issues))

    def plan(self):
        sim = self.f.people[0]
        roll = Record(save_id=self.f.save.id, kind='roll', label='Count', global_day=100,
                      data={'planner_year':1324})
        self.f.session.add(roll); self.f.session.flush()
        plan, _, _ = domain.sync_family_plan_from_pregnancy_roll(self.f.session, self.f.save, roll, sim, 2)
        return sim, roll, plan

    def test_family_plan_closes_after_death_and_stays_closed(self):
        sim, roll, plan = self.plan()
        self.assertTrue(plan.data['active'])
        sim.data = {**sim.data, 'death_global_day':100}
        domain.sync_family_plan_from_pregnancy_roll(self.f.session, self.f.save, roll, sim, 2)
        self.assertFalse(plan.data['active'])
        self.assertEqual(plan.data['target_pregnancies'], 2)
        self.assertFalse(domain.sync_family_plan_from_pregnancy_roll(self.f.session, self.f.save, roll, sim, 2)[1])

    def test_closed_plan_not_reopened_and_future_death_not_premature(self):
        sim, roll, plan = self.plan()
        sim.data = {**sim.data, 'death_global_day':150}
        domain.sync_family_plan_from_pregnancy_roll(self.f.session, self.f.save, roll, sim, 2)
        self.assertTrue(plan.data['active'])
        plan.data = {**plan.data, 'active':False}
        domain.sync_family_plan_from_pregnancy_roll(self.f.session, self.f.save, roll, sim, 2)
        self.assertFalse(plan.data['active'])


if __name__ == '__main__':
    unittest.main()
