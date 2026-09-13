"""Disposable Family Fortunes tests. Never uses installed saves or game files."""
import copy
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import main, family_fortunes as g, family_fortunes_ui as ui, sync, storyline, infinite_decades
from app.models import Record, ChronicleSave
from tests.test_infinite_decades import InfiniteDecadesTests


class FamilyFortunesTests(unittest.TestCase):
    def setUp(self):
        self.f = InfiniteDecadesTests(); self.f.setUp()
        self.f.save.settings = {**self.f.save.settings, 'automation_enabled': False}
        self.f.home.data = {**self.f.home.data, 'last_game_funds': 2000}
        self.f.session.commit()
        self.patch = patch.object(main, 'SessionLocal', self.f.sessions); self.patch.start()
        self.client = TestClient(main.app)
        self.client.post('/saves/select', data={'save_id': self.f.save.id}, follow_redirects=False)

    def tearDown(self):
        self.client.close(); self.patch.stop(); self.f.tearDown()

    def rows(self):
        self.f.session.expire_all()
        return ui.records(self.f.session, self.f.save)

    def add(self, kind, label, **data):
        row = Record(save_id=self.f.save.id, kind=kind, label=label, global_day=100, data=data)
        self.f.session.add(row); self.f.session.commit(); return row

    def new(self, mode='matchmaking', scenario='security'):
        rows = self.rows()
        d = g.create(rows, self.f.save, self.f.home, mode, 1000, 0, 'Family cottage\nWorkshop',
                     [self.f.people[0].id, self.f.people[4].id], scenario)
        if mode == 'matchmaking':
            d.update(first_id=self.f.people[0].id, second_id=self.f.people[4].id)
            d['allocations'] = {**{f'cash-{i}': 'offer' for i in range(5)}, g.SCENARIOS[scenario]['promise'][0]: 'offer'}
        else:
            d['allocations'] = {**{f'cash-{i}': self.f.people[0].id if i < 5 else self.f.people[4].id for i in range(10)},
                'property-0': self.f.people[0].id, 'property-1': self.f.people[4].id}
        row = Record(save_id=self.f.save.id, kind=g.KIND, label='Test board', global_day=100, data=d)
        self.f.session.add(row); self.f.session.commit(); return row

    def act(self, row, action, layout=None, **overrides):
        self.f.session.refresh(row)
        data = {'save_id': self.f.save.id, 'epoch': g.epoch(self.f.save), 'version': row.version, 'action': action,
                'layout': layout or {k: row.data[k] for k in ('allocations', 'first_id', 'second_id', 'marriage_day')}, **overrides}
        return self.client.post('/family-fortunes/' + row.id + '/action', json=data,
                               headers={'X-Dynasty-Epoch': str(g.epoch(self.f.save)[0] or '')})

    def played(self, row):
        for index in range(2):
            self.f.session.refresh(row)
            layout = {k: copy.deepcopy(row.data[k]) for k in ('allocations', 'first_id', 'second_id', 'marriage_day')}
            if index == 1 and row.data['mode'] == 'matchmaking':
                layout['allocations'][g.SCENARIOS[row.data['scenario']]['extra'][0]] = 'offer'
            r = self.act(row, 'negotiate', layout); self.assertEqual(r.status_code, 200, r.text)
        r = self.act(row, 'review'); self.assertEqual(r.status_code, 200, r.text)

    def test_page_start_and_resume(self):
        r = self.client.get('/p/family-fortunes'); self.assertEqual(r.status_code, 200, r.text[:500])
        self.assertIn('Matchmaker’s Table', r.text); self.assertIn('Inheritance Dispute', r.text)
        r = self.client.post('/family-fortunes/start', json={'save_id': self.f.save.id, 'epoch': g.epoch(self.f.save),
            'household_id': self.f.home.id, 'mode': 'inheritance', 'budget': 1000, 'debts': 0,
            'beneficiary_ids': [self.f.people[0].id], 'properties': 'The cottage'})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(r.json()['url']); self.assertEqual(r.status_code, 200, r.text[:500]); self.assertIn('ff-reserve', r.text)
        self.assertIn(g.KIND, sync.SYNC_KINDS)

    def test_match_confirm_is_idempotent_and_records_exact_details(self):
        row = self.new(); self.played(row)
        self.assertFalse(any(r.kind in {'dowry_plan', 'drama_scene'} for r in self.rows()))
        self.assertEqual(self.act(row, 'confirm').status_code, 200)
        self.assertEqual(self.act(row, 'confirm', version=1).status_code, 200)
        self.f.session.expire_all()
        rows = list(self.f.session.scalars(select(Record).where(Record.save_id == self.f.save.id)))
        self.assertEqual(len([r for r in rows if r.kind == 'dowry_plan']), 1)
        relation = next(r for r in rows if r.kind == 'relationship')
        self.assertFalse(relation.data['legally_married']); self.assertEqual(relation.data['suggested_marriage_global_day'], 104)
        dowry = next(r for r in rows if r.kind == 'dowry_plan'); self.assertEqual(dowry.data['amount'], 500); self.assertFalse(dowry.data['paid'])
        scene = next(r for r in rows if r.kind == 'drama_scene')
        self.assertIn('Round 1:', scene.data['body']); self.assertIn('Support the dependent relative', scene.data['body'])
        self.assertEqual(storyline._drama_scene_sentence(scene), scene.data['body'])
        task = next(r for r in rows if r.kind == 'task' and r.data.get('family_fortune_game_id') == row.id)
        self.assertEqual(task.data['feature'], 'heritage_thread')
        today = self.client.get('/p/today')
        self.assertEqual(today.status_code, 200)
        self.assertIn(task.label, today.text)
        self.assertEqual(self.f.home.data['last_game_funds'], 2000)
        page = self.client.get('/p/family-fortunes?game=' + row.id)
        self.assertEqual(page.status_code, 200); self.assertIn('What was recorded', page.text)

    def test_inheritance_preserves_notes_ownership_and_dowry_unpaid(self):
        asset = self.add('heirloom', 'Silver locket', household_id=self.f.home.id, current_holder_sim_id=self.f.people[0].id)
        estate = self.add('estate_plan', 'Original', household_id=self.f.home.id, assets='Keep the ancestral garden.', notes='Never sell the orchard.')
        pledge = self.add('dowry_plan', 'Prior wedding', payer_household_id=self.f.home.id, recipient_sim_id=self.f.people[4].id, amount=300, paid=False, status='Planned')
        row = self.new('inheritance', 'codicil')
        # The recorded heirloom is principal; give it to the succession winner.
        row.data = {**row.data, 'allocations': {**row.data['allocations'], 'heirloom-' + asset.id: self.f.people[0].id}}
        self.f.session.commit(); self.played(row)
        self.assertEqual(self.act(row, 'confirm').status_code, 200)
        self.f.session.refresh(estate); self.f.session.refresh(asset); self.f.session.refresh(pledge)
        self.assertIn('Keep the ancestral garden.', estate.data['assets']); self.assertEqual(estate.data['notes'], 'Never sell the orchard.')
        self.assertEqual(asset.data['current_holder_sim_id'], self.f.people[0].id); self.assertFalse(pledge.data['paid'])
        self.assertEqual(len(estate.data['family_fortunes_allocations']), 2)

    def test_round_limits_and_cannot_sneak_in_unassessed_offer(self):
        row = self.new(); self.played(row)
        layout = {k: copy.deepcopy(row.data[k]) for k in ('allocations', 'first_id', 'second_id', 'marriage_day')}
        layout['allocations']['cash-0'] = 'reserve'
        self.assertEqual(self.act(row, 'review', layout).status_code, 400)
        self.assertEqual(self.act(row, 'negotiate', layout).status_code, 200)
        self.assertEqual(self.act(row, 'negotiate').status_code, 400)
        self.assertEqual(self.act(row, 'review').status_code, 200)

    def test_clock_and_unrelated_traits_do_not_block_confirmation(self):
        row = self.new(); self.played(row)
        self.f.session.refresh(self.f.save); self.f.save.revision += 50; self.f.save.global_day += 1
        other = self.f.people[1]; other.data = {**other.data, 'game_skills': ['Cooking 5']}; other.version += 1
        self.f.session.commit()
        r = self.act(row, 'confirm'); self.assertEqual(r.status_code, 200, r.text)

    def test_material_changes_and_stale_versions_block_safely(self):
        row = self.new(); self.played(row)
        other = self.f.people[4]; other.data = {**other.data, 'death_confirmed': True}; self.f.session.commit()
        self.assertEqual(self.act(row, 'confirm').status_code, 400)
        self.assertFalse(any(r.kind == 'dowry_plan' for r in self.rows()))
        self.assertEqual(self.act(row, 'save', version=1).status_code, 409)

    def test_estate_change_needs_fresh_review(self):
        estate = self.add('estate_plan', 'Estate', household_id=self.f.home.id, assets='Original')
        row = self.new('inheritance', 'codicil'); self.played(row)
        estate.data = {**estate.data, 'assets': 'Manual correction'}; estate.version += 1; self.f.session.commit()
        self.assertEqual(self.act(row, 'confirm').status_code, 409)
        self.assertEqual(self.act(row, 'edit').status_code, 200)
        self.assertEqual(self.act(row, 'review').status_code, 200)
        self.assertEqual(self.act(row, 'confirm').status_code, 200)

    def test_kinship_married_deceased_and_scaled_age(self):
        row = self.new(); d = copy.deepcopy(row.data); d['second_id'] = self.f.people[2].id
        with self.assertRaisesRegex(ValueError, 'kinship'): g.validate(d, self.rows(), self.f.save)
        self.add('relationship', 'Existing', partner1_id=self.f.people[0].id, partner2_id=self.f.people[1].id, type='Marriage', legally_married=True)
        self.assertNotIn(self.f.people[0].id, [p.id for p in g.eligible(self.rows(), self.f.save)])
        self.f.save.days_per_year = 12
        self.f.session.commit()
        self.assertNotIn(self.f.people[4].id, [p.id for p in g.eligible(self.rows(), self.f.save)])

    def test_cash_conservation_and_invalid_destinations(self):
        d = g.create(self.rows(), self.f.save, self.f.home, 'matchmaking', 997, 0, '', [], 'security')
        self.assertEqual(sum(a['amount'] for a in d['assets']), 997)
        for allocations in ({'forged': 'offer'}, {'cash-0': 'stranger'}, {'cash-0': {'not': 'a target'}}):
            with self.assertRaises(ValueError): g.layout(d, {'allocations': allocations}, self.rows(), self.f.save)
        d = g.create(self.rows(), self.f.save, self.f.home, 'inheritance', 997, 0, 'House', [self.f.people[0].id], 'creditor')
        with self.assertRaisesRegex(ValueError, 'Only cash'): g.layout(d, {'allocations': {'property-0': 'debts'}}, self.rows(), self.f.save)

    def test_old_marriage_promises_are_summed_and_flow_to_estate(self):
        for i in range(2): self.add('dowry_plan', 'Promise ' + str(i), payer_household_id=self.f.home.id, recipient_sim_id=self.f.people[4].id, amount=300, status='Planned')
        row = self.new('inheritance', 'codicil')
        result = g.evaluate(row.data, self.rows(), self.f.save)
        claim = next(c for c in result['checks'] if c['label'].startswith('Earlier marriage'))
        self.assertFalse(claim['met']); self.assertIn('§600', claim['detail'])

    def test_cross_save_checkpoint_and_abandon(self):
        row = self.new()
        self.assertEqual(self.act(row, 'save', save_id='another-save').status_code, 409)
        self.assertEqual(self.act(row, 'save', epoch=['wrong', 'epoch']).status_code, 409)
        other = ChronicleSave(workspace_id=self.f.workspace.id, name='Other', settings={}); self.f.session.add(other); self.f.session.commit()
        self.client.post('/saves/select', data={'save_id': other.id}, follow_redirects=False)
        self.assertEqual(self.client.get('/p/family-fortunes?game=' + row.id).status_code, 404)
        self.client.post('/saves/select', data={'save_id': self.f.save.id}, follow_redirects=False)
        self.assertEqual(self.act(row, 'abandon').status_code, 200)
        self.assertFalse(any(r.kind in {'dowry_plan', 'estate_plan'} for r in self.rows()))

    def test_all_six_scenarios_have_specific_rounds(self):
        for key, scenario in g.SCENARIOS.items():
            with self.subTest(scenario=key):
                row = self.new(scenario['mode'], key)
                d = g.negotiate(row.data, self.rows(), self.f.save)
                self.assertEqual(d['history'][0]['revelation'], scenario['twist'])
                self.assertGreater(len(d['history'][0]['offer']), 30)
                d = g.negotiate(d, self.rows(), self.f.save)
                self.assertIn('ending', g.review(d, self.rows(), self.f.save))

    def test_enabled_infinite_branch_can_play_and_frozen_branch_cannot(self):
        infinite_decades.enable(self.f.session, self.f.save, [p.id for p in self.f.people], 'Main line', 2000, 'Sample checkpoint')
        self.f.session.commit()
        row = self.new(); self.played(row)
        self.assertEqual(self.act(row, 'confirm').status_code, 200)
        other = self.new('inheritance', 'codicil')
        infinite_decades.set_enabled(self.f.session, self.f.save, False); self.f.session.commit()
        self.assertEqual(self.act(other, 'negotiate').status_code, 409)


if __name__ == '__main__': unittest.main()
