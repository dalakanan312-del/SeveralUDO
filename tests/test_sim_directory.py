"""Whole-dynasty directory tests using disposable saves only."""
import copy
import re
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import main, sim_directory, infinite_decades as dynasty
from app.models import Record
from tests import test_infinite_decades as fixtures


class SimDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.InfiniteDecadesTests()
        self.f.setUp()
        self.f.save.settings = {**self.f.save.settings, 'automation_enabled':False}
        self.f.session.commit()
        self.patch = patch.object(main, 'SessionLocal', self.f.sessions)
        self.patch.start()
        self.client = TestClient(main.app)
        self.client.post('/saves/select', data={'save_id':self.f.save.id}, follow_redirects=False)

    def tearDown(self):
        self.client.close()
        self.patch.stop()
        self.f.tearDown()

    def directory(self, **params):
        return sim_directory.page(self.f.session, self.f.save, params)

    def ids(self, html):
        return re.findall(r'data-sim-id="([^"]+)"', html)

    def test_default_includes_other_branches_once_but_not_archived(self):
        self.f.enable()
        self.f.capture()
        archived = Record(save_id=self.f.save.id, kind='sim', label='Archived person', deleted=True, data={})
        self.f.session.add(archived)
        self.f.session.commit()
        response = self.client.get('/p/sims')
        self.assertEqual(response.status_code, 200, response.text[:200])
        self.assertCountEqual(self.ids(response.text), [s.id for s in self.f.people])
        self.assertNotIn(archived.id, self.ids(response.text))
        for text in ['All branches', 'Cara line', 'Main line', 'Read-only', 'Preserved GD 104']:
            self.assertIn(text, response.text)
        rows, ctx = self.directory()
        self.assertEqual(ctx['list_status'], 'all')
        self.assertEqual(len(rows), 5)
        self.assertEqual(len(set(r.id for r in rows)), 5)

    def test_filters_search_and_branch_ids(self):
        first = self.f.enable()
        child = self.f.capture()
        rows, _ = self.directory(sim_branch='active')
        self.assertCountEqual([r.label for r in rows], ['Ada','Ben','Dara'])
        rows, ctx = self.directory(sim_branch=child.id)
        self.assertEqual([r.label for r in rows], ['Cara'])
        self.assertTrue(ctx['sim_cards'][rows[0].id]['readonly'])
        self.assertEqual(self.directory(sim_branch=first.id)[1]['sim_branch'], 'active')
        self.assertEqual(self.directory(sim_branch='missing')[1]['list_count'], 5)
        rows, _ = self.directory(q='ara')
        self.assertEqual([r.label for r in rows], ['Cara','Dara'])

    def test_status_uses_each_branch_date_and_explicit_death(self):
        self.f.people[0].data = {**self.f.people[0].data, 'death_global_day':99}
        self.f.people[1].data = {**self.f.people[1].data, 'game_was_dead':True}
        self.f.people[2].data = {**self.f.people[2].data, 'death_global_day':120}
        self.f.session.commit()
        self.f.enable()
        self.f.capture(day=104)
        self.f.save.global_day = 150
        self.f.session.commit()
        living, _ = self.directory(record_status='living')
        dead, _ = self.directory(record_status='dead')
        self.assertCountEqual([r.label for r in living], ['Cara','Dara','Outside'])
        self.assertCountEqual([r.label for r in dead], ['Ada','Ben'])
        # Active branch GD 150 has passed Cara's death, but her branch is at 104.
        html = self.client.get('/p/sims?record_status=living').text
        self.assertIn(self.f.people[2].id, self.ids(html))
        self.assertNotIn(self.f.people[0].id, self.ids(html))

    def test_birth_sorting_spans_branches(self):
        for sim, day in zip(self.f.people, [4,8,2,10,6]):
            sim.data = {**sim.data, 'birth_global_day':day}
        self.f.session.commit()
        self.f.enable()
        self.f.capture()
        self.assertEqual([r.label for r in self.directory()[0]], ['Cara','Ada','Outside','Ben','Dara'])
        self.assertEqual([r.label for r in self.directory(sort='birth-newest')[0]], ['Dara','Ben','Outside','Ada','Cara'])
        self.assertEqual([r.label for r in self.directory(sort='name')[0]], ['Ada','Ben','Cara','Dara','Outside'])

    def test_pagination_retains_branch_and_everyone_filter(self):
        for n in range(52):
            self.f.session.add(Record(save_id=self.f.save.id,kind='sim',label=f'Extra {n:02}',data={'birth_global_day':n+1}))
        self.f.session.commit()
        self.f.enable()
        all_rows, ctx = self.directory()
        self.assertEqual((len(all_rows),ctx['list_count'],ctx['list_pages']), (48,57,2))
        second, ctx2 = self.directory(list_page='2')
        self.assertEqual(len(second),9)
        self.assertFalse({r.id for r in all_rows} & {r.id for r in second})
        self.assertEqual(self.directory(list_page='oops')[1]['list_page'],1)
        self.assertEqual(self.directory(list_page='999')[1]['list_page'],2)
        frozen_branch = next(b for b in dynasty.branches(self.f.session,self.f.save) if b.id != dynasty.state(self.f.save)['active_branch_id'])
        response = self.client.get('/p/sims',params={'sim_branch':frozen_branch.id,'record_status':'all'})
        self.assertIn('sim_branch='+frozen_branch.id, response.text)
        self.assertIn('record_status=all',response.text)
        links = re.findall(r'href="([^"]*list_page=2[^"]*)"',response.text)
        self.assertTrue(links)
        second_html = self.client.get(links[0].replace('&amp;','&') if links[0].startswith('/') else '/p/sims'+links[0].replace('&amp;','&')).text
        self.assertFalse(set(self.ids(response.text)) & set(self.ids(second_html)))
        self.assertEqual(len(self.ids(response.text))+len(self.ids(second_html)),53)

    def test_frozen_profile_redirect_and_paused_directory_are_read_only(self):
        self.f.enable()
        self.f.capture()
        self.f.finish_modern()
        saved = copy.deepcopy(self.f.save.settings)
        snapshot = {r.id:(r.deleted,copy.deepcopy(r.data),r.version) for r in self.f.session.scalars(select(Record))}
        response = self.client.get('/p/sims')
        self.assertEqual(response.status_code,200,response.text[:300])
        self.assertCountEqual(self.ids(response.text), [r.id for r in self.f.people])
        self.assertNotIn('<summary>Add a Sim</summary>', response.text)
        self.assertNotIn('/restore"',response.text)
        profile = self.client.get('/sims/'+self.f.people[2].id,follow_redirects=False)
        self.assertEqual(profile.status_code,303)
        self.assertEqual(profile.headers['location'],'/p/infinite-decades?sim_id='+self.f.people[2].id+'#dynasty-person')
        self.f.session.expire_all()
        self.assertEqual(snapshot,{r.id:(r.deleted,r.data,r.version) for r in self.f.session.scalars(select(Record))})
        self.assertEqual(saved,self.f.save.settings)

    def test_branch_filter_is_remembered_and_can_be_cleared(self):
        self.f.enable()
        child = self.f.capture()
        response = self.client.post('/api/ui/preferences',json={'save_id':self.f.save.id,'page':'sims','filters':{'sim_branch':child.id,'record_status':'all'}})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.ids(self.client.get('/p/sims').text), [self.f.people[2].id])
        self.assertEqual(len(self.ids(self.client.get('/p/sims?sim_branch=all').text)),5)
        self.assertEqual(len(self.ids(self.client.get('/p/sims?reset=1').text)),5)

    def test_frozen_portrait_uses_preserved_age_not_active_branch_date(self):
        self.f.enable()
        self.f.capture(day=104)
        self.f.save.global_day = 180
        self.f.session.commit()
        with patch.object(main.insights, 'life_stage', return_value='Teen') as stage:
            photo = self.client.get('/portraits/'+self.f.people[2].id+'/current')
            self.assertEqual(photo.status_code,200)
            self.assertEqual(photo.content,b'portrait')
            self.assertEqual(stage.call_args.args[1],104)

    def test_ordinary_saves_keep_living_default_and_no_branch_filter(self):
        self.f.people[0].data = {**self.f.people[0].data,'death_global_day':90}
        self.f.session.commit()
        rows, ctx = self.directory()
        self.assertEqual(len(rows),4)
        self.assertEqual(ctx['list_status'],'living')
        self.assertEqual(ctx['sim_branch_choices'],[])
        self.assertEqual(len(self.directory(record_status='all')[0]),5)
        self.assertNotIn('name="sim_branch"', self.client.get('/p/sims').text)


if __name__ == '__main__':
    unittest.main()
