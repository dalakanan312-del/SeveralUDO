"""Unknown ancestry is a deliberate d5, not fabricated grandparent evidence."""
import copy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app import domain, main, hp_bloodlines as blood, harry_potter_rules as hp
from app.models import Record, ChronicleSave, DiceAudit
from tests import test_infinite_decades as fixture


class UnknownAncestryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.InfiniteDecadesTests(); self.f.setUp()
        self.s, self.save, self.sim = self.f.session, self.f.save, self.f.people[0]
        self.save.settings = {**self.save.settings, "selected_rule_packs": [hp.PACK_ID], "automation_enabled": False}
        self.sim.data = {"birth_global_day": -100, "sex": "Female"}
        hp.sync_pack(self.s, self.save, [hp.PACK_ID]); self.s.commit()
        self.binding = patch.object(main, "SessionLocal", self.f.sessions); self.binding.start()
        self.client = TestClient(main.app)
        self.client.post('/saves/select', data={'save_id': self.save.id})

    def tearDown(self):
        self.client.close(); self.binding.stop(); self.f.tearDown()

    def headers(self):
        return {'X-Decades-Fragment': 'preview', 'X-UI-Save': self.save.id}

    def roll(self):
        row, created = blood.create_unknown_roll(self.s, self.save, self.sim)
        self.s.commit()
        return row

    def preview(self, row, actual):
        response = self.client.post('/api/rolls/'+row.id+'/complete', data={'actual': actual}, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['preview']

    def confirm(self, preview, status=200):
        response = self.client.post('/api/previews/'+preview['token']+'/confirm', headers=self.headers())
        self.assertEqual(response.status_code, status, response.text)
        return response

    def test_exact_d5_table_all_five_results_and_ability(self):
        expected = {1: ('Muggle', 'Muggle'), 2: ('Muggle-Born', 'Witch'), 3: ('Squib', 'Squib'),
                    4: ('Half-Blood', 'Witch'), 5: ('Pureblood', 'Witch')}
        for face, (status, ability) in expected.items():
            with self.subTest(face=face):
                person = Record(save_id=self.save.id, kind='sim', label='Unknown '+str(face),
                    global_day=-100, data={'birth_global_day': -100, 'sex': 'Female'})
                self.s.add(person); self.s.flush()
                row, _ = blood.create_unknown_roll(self.s, self.save, person)
                result = domain.complete_roll(self.s, self.save, row, face)
                self.assertEqual(result['outcome'], status)
                self.assertEqual((person.data['hp_blood_status'], person.data['hp_magical_ability']), (status, ability))
                self.assertTrue(blood.classify(person, blood.people(self.s, self.save))['rolled'])
                self.assertFalse(blood.manual(person.data))
                self.assertEqual(person.data['hp_blood_status_evidence'], {})
                self.assertEqual(person.data['hp_ancestry_roll_id'], row.id)
                self.assertEqual(row.data['die'], 'd5')
                self.assertTrue(row.data['nonlethal'])
                self.assertNotIn('death_global_day', person.data)
                self.assertNotIn('mother_id', person.data)

    def test_preview_does_not_apply_identity_until_confirmation(self):
        row = self.roll(); before = copy.deepcopy(self.sim.data)
        preview = self.preview(row, 5)
        self.s.refresh(self.sim); self.s.refresh(row)
        self.assertEqual(self.sim.data, before)
        self.assertFalse(row.data['completed'])
        self.assertIn('Blood status (rolled ancestry)', ' '.join(preview['effects']))
        self.confirm(preview)
        self.s.refresh(self.sim); self.s.refresh(row)
        self.assertEqual(self.sim.data['hp_blood_status'], 'Pureblood')
        self.assertEqual(row.data['actual'], 5)
        self.assertTrue(row.data['completed'])
        self.assertEqual(self.sim.data['hp_magical_ability'], 'Witch')

    def test_native_button_reuses_pending_roll_decline_draws_again(self):
        url = '/api/harry-potter/sims/'+self.sim.id+'/ancestry/roll'
        first = self.client.post(url, headers=self.headers())
        self.assertEqual(first.status_code, 200, first.text)
        p1 = first.json()['preview']
        second = self.client.post(url, headers=self.headers())
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(p1['actual'], second.json()['preview']['actual'])
        self.assertEqual(self.client.post('/api/previews/'+p1['token']+'/decline', headers=self.headers()).status_code, 200)
        third = self.client.post(url, headers=self.headers())
        self.assertEqual(third.status_code, 200, third.text)
        self.assertIn(third.json()['preview']['actual'], range(1, 6))
        self.s.expire_all()
        rolls = list(self.s.scalars(select(Record).where(Record.kind=='roll', Record.data['hp_unknown_ancestry'].as_boolean().is_(True))))
        self.assertEqual(len(rolls), 1)
        self.assertEqual(self.s.scalar(select(func.count()).select_from(DiceAudit).where(DiceAudit.context_id==rolls[0].id)), 2)
        self.confirm(p1, 409)
        self.confirm(third.json()['preview'])

    def test_roll_survives_refresh_and_form_preserves_provenance(self):
        row = self.roll(); domain.complete_roll(self.s, self.save, row, 5)
        self.save.settings = {**self.save.settings, 'automation_enabled': True}
        blood.refresh(self.s, self.save)
        self.assertEqual(self.sim.data['hp_blood_status'], 'Pureblood')
        result = blood.classify(self.sim, blood.people(self.s, self.save))
        self.assertEqual((result['status'], result['display'], result['spellcasters']), ('Unknown', 'Pureblood', 0))
        self.assertEqual(blood.form_updates('rolled', self.sim.data), {})
        self.s.commit()
        page = self.client.get('/p/harry-potter')
        self.assertIn('Keep rolled ancestry', page.text)
        response = self.client.post('/api/harry-potter/sims/'+self.sim.id,
            data={'magical_ability':'Witch', 'blood_status':'rolled'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.s.refresh(self.sim)
        self.assertEqual(self.sim.data['hp_blood_status_source'], 'Unknown ancestry d5')
        self.assertTrue(blood.rolled(self.sim.data))
        self.sim.data = {**self.sim.data, **blood.form_updates('auto')}
        blood.refresh(self.s, self.save)
        self.assertEqual(self.sim.data['hp_blood_status'], 'Unknown')

    def test_birth_identity_rolls_do_not_overwrite_confirmed_fallback(self):
        self.sim.global_day = 1
        self.sim.data = {**self.sim.data, 'birth_global_day': 1}
        pending = Record(save_id=self.save.id, kind='roll', label='Birth magic', global_day=1,
            data={'sim_id':self.sim.id, 'hp_rule_code':'HP-05', 'completed':False})
        completed = Record(save_id=self.save.id, kind='roll', label='Earlier birth', global_day=1,
            data={'sim_id':self.sim.id, 'hp_rule_code':'HP-05', 'completed':True, 'actual':2})
        self.s.add_all([pending, completed]); self.s.flush()
        row = self.roll(); domain.complete_roll(self.s, self.save, row, 3)
        self.assertTrue(pending.deleted); self.assertFalse(completed.deleted)
        self.assertEqual(completed.data['actual'], 2)
        self.save.settings = {**self.save.settings, 'automation_enabled': True}
        domain._schedule_harry_potter_rolls(self.s, self.save, [self.sim])
        active = list(self.s.scalars(select(Record).where(Record.save_id==self.save.id, Record.kind=='roll',
            Record.deleted.is_(False), Record.data['sim_id'].as_string()==self.sim.id)))
        self.assertFalse(any(r.data.get('hp_rule_code')=='HP-05' and not r.data.get('completed') for r in active))
        self.assertEqual(domain._apply_hp_roll_result(self.s, self.save, completed, 2), 0)
        self.assertEqual(self.sim.data['hp_magical_ability'], 'Squib')

    def test_known_manual_dead_and_frozen_sims_cannot_roll(self):
        original = copy.deepcopy(self.sim.data)
        for extra in ({'hp_magical_ability':'Muggle'}, {'hp_blood_status':'Pureblood'},
                      {'death_confirmed':True}, {'game_was_dead':True}, {'death_global_day':50}, {'infinite_frozen':True}):
            with self.subTest(extra=extra):
                self.sim.data = {**original, **extra}
                with self.assertRaises(ValueError):blood.create_unknown_roll(self.s, self.save, self.sim)

    def test_disabled_pack_module_and_foreign_sim_are_rejected(self):
        self.save.settings = {**self.save.settings, 'selected_rule_packs':[]}
        with self.assertRaises(ValueError):self.roll()
        self.save.settings = {**self.save.settings, 'selected_rule_packs':[hp.PACK_ID]}
        rule = self.s.scalar(select(Record).where(Record.kind=='addon_rule', Record.data['code'].as_string()=='HP-04'))
        rule.data = {**rule.data, 'active':False}
        with self.assertRaises(ValueError):self.roll()
        other = ChronicleSave(workspace_id=self.save.workspace_id, name='Other')
        self.s.add(other); self.s.flush()
        outsider = Record(save_id=other.id, kind='sim', label='Outside', data={})
        self.s.add(outsider); self.s.commit()
        response = self.client.post('/api/harry-potter/sims/'+outsider.id+'/ancestry/roll', headers=self.headers())
        self.assertEqual(response.status_code, 404)

    def test_completed_roll_cannot_be_repeated_or_reopened(self):
        row = self.roll(); self.confirm(self.preview(row, 4))
        self.s.refresh(self.sim)
        with self.assertRaises(ValueError):self.roll()
        response = self.client.post('/api/rolls/'+row.id+'/reopen', follow_redirects=False)
        self.assertEqual(response.status_code, 409, response.text)

    def test_invalid_d5_results_rejected(self):
        row = self.roll()
        for face in (0,6,-1):
            response = self.client.post('/api/rolls/'+row.id+'/complete', data={'actual':face}, headers=self.headers())
            self.assertEqual(response.status_code, 400, response.text)
        self.s.refresh(self.sim)
        self.assertNotIn('hp_ancestry_roll_id', self.sim.data)

    def test_new_ancestry_evidence_blocks_stale_confirmation(self):
        row = self.roll(); preview = self.preview(row, 5)
        self.sim.data = {**self.sim.data, 'hp_magical_ability':'Muggle'}; self.s.commit()
        self.confirm(preview, 409)
        self.s.refresh(self.sim)
        self.assertNotIn('hp_ancestry_roll_id', self.sim.data)

    def test_profile_button_and_rolled_evidence_are_visible(self):
        page = self.client.get('/sims/'+self.sim.id)
        self.assertIn('Roll unknown ancestry', page.text)
        row = self.roll(); self.confirm(self.preview(row, 3))
        page = self.client.get('/sims/'+self.sim.id)
        self.assertIn('Rolled ancestry', page.text)
        self.assertIn('3 → Squib', page.text)
        self.assertNotIn('Roll unknown ancestry', page.text)
