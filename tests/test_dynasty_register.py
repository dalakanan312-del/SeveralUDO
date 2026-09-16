import copy
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import dynasty_register, main
from app.models import Record
from tests import test_infinite_decades as fixtures


class DynastyRegisterTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.InfiniteDecadesTests(); self.f.setUp()
        self.parent=self.f.enable(); self.child=self.f.capture()
    def tearDown(self): self.f.tearDown()
    def register(self):
        with self.f.session.no_autoflush:
            branches=main.infinite_decades.branches(self.f.session,self.f.save)
        return dynasty_register.context(self.f.save,self.f.people,branches)

    def test_all_branches_and_frozen_sims_once_without_writes(self):
        before=copy.deepcopy([(p.id,p.deleted,p.data) for p in self.f.people])
        settings=copy.deepcopy(self.f.save.settings)
        result=self.register(); rows=[r for g in result['groups'] for r in g['members']]
        self.assertEqual(result['count'],5)
        self.assertEqual(len({r['id'] for r in rows}),5)
        self.assertEqual({g['name'] for g in result['groups']},{'Main line','Cara line','Starting world'})
        self.assertEqual([(p.id,p.deleted,p.data) for p in self.f.people],before)
        self.assertEqual(settings,self.f.save.settings)
        self.assertFalse(self.f.session.dirty)

    def test_preserved_age_dead_age_zero_generation_and_same_name(self):
        frozen=self.f.people[2]
        frozen.data={**frozen.data,'birth_global_day':0,'generation':0,'sex':'Gender.FEMALE'}
        self.f.people[0].data={**self.f.people[0].data,'death_global_day':9}
        self.f.people[1].label=frozen.label
        self.f.save.global_day=200
        rows={r['id']:r for g in self.register()['groups'] for r in g['members']}
        self.assertEqual(rows[frozen.id]['age'],26)
        self.assertEqual(rows[frozen.id]['generation'],0)
        self.assertEqual(rows[frozen.id]['sex'],'Female')
        self.assertEqual(rows[self.f.people[0].id]['age'],2)
        self.assertEqual(rows[self.f.people[0].id]['status'],'Deceased')
        self.assertEqual(rows[self.f.people[0].id]['children'],1)
        self.assertEqual(sum(r['name']=='Cara' for r in rows.values()),2)

    def test_year_length_and_future_death_do_not_age_paused_sim(self):
        self.f.save.days_per_year=12
        self.f.people[2].data={**self.f.people[2].data,'death_global_day':150}
        self.f.save.global_day=250
        row=next(r for g in self.register()['groups'] for r in g['members'] if r['id']==self.f.people[2].id)
        self.assertEqual(row['age'],8);self.assertEqual(row['status'],'Living')
        self.assertEqual(row['observed_day'],104)

    def test_archived_nonfrozen_is_not_resurrected_and_unknown_stays_unknown(self):
        self.f.people[1].deleted=True
        self.f.people[0].data={'death_confirmed':True}
        rows={r['id']:r for g in self.register()['groups'] for r in g['members']}
        self.assertNotIn(self.f.people[1].id,rows)
        self.assertIsNone(rows[self.f.people[0].id]['age'])
        self.assertEqual(rows[self.f.people[0].id]['birthplace'],'—')

    def test_empty_finished_branch_and_missing_branch_are_kept(self):
        self.child.data={**self.child.data,'meta':{**self.child.data['meta'],'status':'extinct'}}
        self.f.people[2].data={**self.f.people[2].data,'infinite_branch_id':'unavailable'}
        result=self.register()
        self.assertTrue(any(g['name']=='Cara line' and not g['members'] for g in result['groups']))
        self.assertTrue(any(g['name']=='No recorded branch' and g['members'] for g in result['groups']))

    def test_http_register_includes_frozen_and_escapes_names(self):
        self.f.people[1].label='<script>bad()</script>';self.f.session.commit()
        with patch.object(main,'SessionLocal',self.f.sessions):
            with TestClient(main.app) as client:
                client.post('/saves/select',data={'save_id':self.f.save.id})
                response=client.get('/p/infinite-decades')
                self.assertEqual(response.status_code,200,response.text[:300])
                self.assertIn('id="dynasty-register"',response.text)
                self.assertEqual(response.text.count('data-register-person='),5)
                self.assertNotIn('<script>bad()</script>',response.text)
                self.assertIn('Gen.',response.text);self.assertIn('Birthplace',response.text)
                self.assertIn('id="choose-branch"',response.text)
                self.assertIn('whole dynasty register',client.get('/p/family-tree').text)


if __name__=='__main__': unittest.main()
