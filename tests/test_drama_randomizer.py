"""Isolated d64 and request tests; never opens the installed database."""
import copy
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from app import drama_randomizer as randomizer, infinite_decades, storyline, sync
from app.models import ChronicleSave, Record
from tests import test_workflow as fixtures


class RandomizerCatalogTests(unittest.TestCase):
    def test_original_64_distinct_prompts_keep_their_numbering(self):
        self.assertEqual(len(randomizer.PROMPTS),64)
        self.assertEqual([p['number'] for p in randomizer.PROMPTS],list(range(1,65)))
        self.assertEqual(len({p['title'] for p in randomizer.PROMPTS}),64)
        expected={1:'Have a Baby',8:'Kill a Random Sim',17:'One of Your Children Must Die',
                  29:'Abandon Your Current Trade or Position',45:'House Fire',
                  48:'Try to Have Children With Every Eligible Sim Your Sim Knows',
                  54:'Kidnap or Illegally Claim Someone Else’s Baby',64:'Kill Someone and Keep Their Ghost in the Household'}
        for number,title in expected.items():
            self.assertEqual(randomizer.PROMPTS[number-1]['title'],title)
        self.assertTrue(all(p['action'] and p['emoji'] for p in randomizer.PROMPTS))

    def test_every_number_is_reachable_using_one_uniform_d64_draw(self):
        save=ChronicleSave(id='sample',global_day=20,settings={})
        for number in range(1,65):
            with patch.object(randomizer.secrets,'randbelow',return_value=number-1) as roll:
                state=randomizer.draw(save)
                roll.assert_called_once_with(64)
            self.assertEqual(randomizer.resolve(save,state)['prompt']['number'],number)

    def test_new_card_and_epoch_cannot_reuse_an_old_identity(self):
        save=ChronicleSave(id='sample',global_day=20,settings={})
        first,second=randomizer.draw(save),randomizer.draw(save)
        self.assertNotEqual(randomizer.record_id(first),randomizer.record_id(second))
        for invalid in (None,{}, {**first,'number':65}, {**first,'number':True},
                        {**first,'save_id':'elsewhere'}, {**first,'epoch':'old-branch'},
                        {**first,'draw_id':'invalid'}):
            with self.assertRaises(ValueError): randomizer.resolve(save,invalid)

    def test_reuses_synced_record_kind_and_mobile_theme_tokens(self):
        self.assertIn('drama_scene',sync.SYNC_KINDS)
        css=(Path(__file__).resolve().parents[1]/'app/static/drama_randomizer.css').read_text(encoding='utf-8')
        for fragment in ('var(--gold)','var(--panel-soft)','min-height:44px','@media(max-width:600px)','columns:1'):
            self.assertIn(fragment,css)


class RandomizerRequestTests(unittest.TestCase):
    def setUp(self):
        self.harness=fixtures.WorkflowTests();self.harness.setUp()
        self.client=self.harness.client;self.f=self.harness.fixture
        self.save_id=self.f.save.id

    def tearDown(self): self.harness.tearDown()

    def draw(self,number=1,**extra):
        with patch.object(randomizer.secrets,'randbelow',return_value=number-1):
            response=self.client.post('/drama-randomizer/draw',data={'save_id':self.save_id,**extra})
        self.assertEqual(response.status_code,200,response.text[:200])
        return re.search(r'name="draw_id" value="([a-f0-9]{32})"',response.text).group(1),response.text

    def record(self,draw_id,**extra):
        return self.client.post('/drama-randomizer/record',data={
            'save_id':self.save_id,'draw_id':draw_id,'global_day':99,
            'notes':'Ada held a feast for Ben. The meal cost 100 coins.',**extra})

    def scenes(self):
        self.f.session.expire_all()
        return list(self.f.session.scalars(select(Record).where(Record.kind=='drama_scene')))

    def test_draw_discard_and_refresh_do_not_record_or_change_people(self):
        before=[(s.id,copy.deepcopy(s.data)) for s in self.f.people]
        draw_id,html=self.draw(45,sim_id=self.f.people[0].id)
        self.assertIn('House Fire',html)
        self.assertIn('Ada',html)
        self.assertIn('Cooley',html)
        self.assertEqual(len(self.scenes()),0)
        self.assertIn(draw_id,self.client.get('/p/drama-randomizer').text)
        response=self.client.post('/drama-randomizer/discard',data={'save_id':self.save_id,'draw_id':draw_id})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('name="draw_id"',response.text)
        self.assertEqual(len(self.scenes()),0)
        self.assertEqual(before,[(s.id,s.data) for s in self.f.people])

    def test_outcome_has_exact_prompt_notes_day_and_is_idempotent(self):
        draw_id,_=self.draw(53,sim_id=self.f.people[0].id)
        for _ in range(2): self.assertEqual(self.record(draw_id).status_code,200)
        scenes=self.scenes();self.assertEqual(len(scenes),1)
        scene=scenes[0]
        self.assertEqual(scene.global_day,99)
        self.assertEqual(scene.data['drawn_global_day'],100)
        self.assertEqual(scene.data['actual'],53)
        self.assertEqual(scene.data['sim_id'],self.f.people[0].id)
        sentence=storyline._drama_scene_sentence(scene)
        self.assertIn('Hold a Great Feast or Celebration',sentence)
        self.assertIn('The meal cost 100 coins.',sentence)
        html=self.client.get('/p/storyline').text
        self.assertIn('The meal cost 100 coins.',html)
        self.assertEqual(self.record(draw_id,notes='Overwrite attempt').status_code,200)
        self.assertNotIn('Overwrite attempt',self.scenes()[0].data['body'])

    def test_record_does_not_apply_the_death_or_money_effects(self):
        before=[(s.id,copy.deepcopy(s.data)) for s in self.f.people]
        draw_id,_=self.draw(8,sim_id=self.f.people[0].id)
        response=self.record(draw_id,notes='Played the death in game; awaiting its normal game report.')
        self.assertEqual(response.status_code,200)
        self.scenes()
        self.assertEqual(before,[(s.id,s.data) for s in self.f.people])
        self.assertFalse(self.f.session.scalars(select(Record).where(Record.kind=='death')).first())

    def test_stale_reroll_cannot_be_recorded_and_new_draw_can(self):
        old,_=self.draw(1);new,_=self.draw(64)
        self.assertEqual(self.record(old).status_code,409)
        self.assertEqual(self.record(new).status_code,200)
        self.assertEqual(len(self.scenes()),1)

    def test_bad_dates_blank_notes_and_foreign_people_are_rejected(self):
        draw_id,_=self.draw()
        for fields in ({'global_day':101},{'global_day':-1},{'notes':'  '},{'notes':'x'*4001}):
            self.assertEqual(self.record(draw_id,**fields).status_code,400)
        for fields in ({'sim_id':'another-save-sim'},{'household_id':'another-save-household'}):
            response=self.client.post('/drama-randomizer/draw',data={'save_id':self.save_id,**fields})
            self.assertEqual(response.status_code,400)
        self.assertEqual(len(self.scenes()),0)

    def test_dead_and_archived_sims_are_not_draw_targets_but_death_after_draw_can_be_recorded(self):
        person=self.f.people[0]
        draw_id,_=self.draw(8,sim_id=person.id)
        person.data={**person.data,'death_global_day':100};self.f.session.commit()
        self.assertEqual(self.record(draw_id).status_code,200)
        response=self.client.post('/drama-randomizer/draw',data={'save_id':self.save_id,'sim_id':person.id})
        self.assertEqual(response.status_code,400)
        person.deleted=True;self.f.session.commit()
        response=self.client.post('/drama-randomizer/draw',data={'save_id':self.save_id,'sim_id':person.id})
        self.assertEqual(response.status_code,400)

    def test_switching_saves_rejects_old_draw_and_old_form(self):
        draw_id,_=self.draw()
        second=ChronicleSave(workspace_id=self.f.workspace.id,name='Other save',global_day=100,
                             settings=copy.deepcopy(self.f.save.settings))
        self.f.session.add(second);self.f.session.commit()
        self.client.post('/saves/select',data={'save_id':second.id})
        self.assertEqual(self.record(draw_id).status_code,409)
        self.assertEqual(self.record(draw_id,save_id=second.id).status_code,409)
        self.assertEqual(len(self.scenes()),0)

    def test_branch_epoch_and_frozen_save_block_old_actions(self):
        draw_id,_=self.draw()
        self.f.enable()
        self.assertEqual(self.record(draw_id).status_code,409)
        epoch=infinite_decades.state(self.f.save)['epoch']
        self.client.headers['X-Dynasty-Epoch']=epoch
        self.assertEqual(self.record(draw_id).status_code,409)
        self.f.finish_modern()
        self.client.headers['X-Dynasty-Epoch']=infinite_decades.state(self.f.save)['epoch']
        response=self.client.post('/drama-randomizer/draw',data={'save_id':self.save_id})
        self.assertEqual(response.status_code,409)

    def test_notes_are_escaped_when_rendered(self):
        draw_id,_=self.draw()
        html=self.record(draw_id,notes='<script>alert(1)</script>').text
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;',html)


if __name__=='__main__': unittest.main()
