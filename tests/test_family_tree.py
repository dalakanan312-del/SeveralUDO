"""Read-only graph tests and routes backed by disposable in-memory data."""
import copy
import json
import re
import unittest
from types import SimpleNamespace as Row
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import family_tree, main
from tests import test_infinite_decades as fixtures

def sim(sid, **data):
    return Row(id=sid, label=sid, kind='sim', data=data, deleted=False, global_day=1)

def graph(rows, **kwargs):
    return family_tree.make_graph(rows, Row(id='save'), status=lambda r,s:'Frozen' if r.data.get('infinite_frozen') else 'Alive',
        birth=lambda s,r:'1300', death=lambda s,r:'1400', **kwargs)

class FamilyGraphTests(unittest.TestCase):
    def test_read_only_parentage_and_duplicate_relationship_history(self):
        rows=[sim('a'),sim('b'),sim('child',mother_id='a',father_id='b'),
            Row(id='r1',kind='relationship',label='',data={'partner1_id':'a','partner2_id':'b','type':'Marriage','status':'Divorced'},deleted=False,global_day=2),
            Row(id='r2',kind='relationship',label='',data={'partner1_id':'b','partner2_id':'a','type':'Marriage','status':'Active'},deleted=False,global_day=4)]
        before=copy.deepcopy([r.__dict__ for r in rows]);result=graph(rows)
        self.assertEqual(len(result['nodes']),3)
        partnerships=[e for e in result['edges'] if e['type']=='partner'];self.assertEqual(len(partnerships),1)
        self.assertEqual(partnerships[0]['role'],'Spouse');self.assertEqual(len(partnerships[0]['history']),2)
        self.assertEqual(before,[r.__dict__ for r in rows])

    def test_coparents_are_not_romance_and_adoption_is_explicit(self):
        result=graph([sim('a'),sim('b'),sim('c',mother_id='a',adoptive_parent_ids=['b'],birth_status='Adopted')])
        self.assertEqual(len(result['edges']),2);self.assertTrue(all(e['type']=='parent' for e in result['edges']))
        self.assertEqual(sum(e['adoptive'] for e in result['edges']),1)

    def test_excluded_and_missing_parents_are_not_fabricated(self):
        removed=sim('removed');removed.deleted=True
        result=graph([sim('a',mother_id='missing',father_id='a'),sim('hidden',include_in_family_tree=False),removed])
        self.assertEqual([n['id'] for n in result['nodes']],['a']);self.assertFalse(result['edges'])
        self.assertEqual(result['nodes'][0]['missingParents'],['Mother']);self.assertEqual(len(result['warnings']),1)

    def test_frozen_dates_branch_and_profile_are_preserved(self):
        frozen=sim('a',infinite_frozen=True,infinite_frozen_global_day=100,infinite_branch_id='branch');frozen.deleted=True
        branch=Row(id='branch',label='First line',data={'meta':{'status':'modern'}})
        result=graph([frozen],photo_ids=['a'],branches=[branch]);n=result['nodes'][0]
        self.assertTrue(n['frozen']);self.assertEqual(n['frozenDay'],100);self.assertEqual(n['branch'],'First line')
        self.assertIn('/p/infinite-decades?',n['profile']);self.assertEqual(n['portrait'],'/portraits/a/current')
        self.assertTrue(frozen.deleted)

class FamilyRouteTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.InfiniteDecadesTests();self.fixture.setUp()
        self.patch=patch.object(main,'SessionLocal',self.fixture.sessions);self.patch.start()
        self.client=TestClient(main.app);self.client.post('/saves/select',data={'save_id':self.fixture.save.id})

    def tearDown(self):
        self.client.close();self.patch.stop();self.fixture.tearDown()

    def payload(self,response):
        self.assertEqual(response.status_code,200,response.text[:300])
        return json.loads(re.search(r'data-family-graph>(.*?)</script>',response.text,re.S).group(1))

    def test_normal_save_renders_new_controls_and_no_writes(self):
        f=self.fixture;before=copy.deepcopy([r.data for r in f.people]);day=f.save.global_day
        response=self.client.get('/p/family-tree');payload=self.payload(response)
        self.assertEqual(len(payload['nodes']),5);self.assertIn('Download SVG',response.text);self.assertIn('Previous focus',response.text)
        f.session.expire_all();self.assertEqual(f.save.global_day,day);self.assertEqual(before,[r.data for r in f.people])

    def test_infinite_tree_has_one_canonical_person_and_frozen_dates(self):
        f=self.fixture;f.enable();f.capture();before=f.counts();response=self.client.get('/p/family-tree')
        payload=self.payload(response);self.assertTrue(payload['infinite']);self.assertEqual(len({n['id'] for n in payload['nodes']}),len(payload['nodes']))
        self.assertTrue(any(n['frozen'] for n in payload['nodes']));self.assertEqual(f.counts(),before)

    def test_empty_and_invalid_options(self):
        f=self.fixture
        for r in f.people:r.deleted=True
        f.session.commit();response=self.client.get('/p/family-tree?depth=oops&scope=bad&mode=bad&view=bad')
        self.assertEqual(self.payload(response)['nodes'],[]);self.assertIn('No Sims in this tree yet',response.text)

if __name__=='__main__':unittest.main()
