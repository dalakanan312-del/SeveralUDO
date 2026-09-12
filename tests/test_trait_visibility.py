import unittest
from sqlalchemy import select
from app.models import Record

from app import automation, game_metadata, trait_visibility as traits
from tests import test_usability as fixtures


ROWS = [
    {'name': 'Creative', 'tuning_id': '1', 'is_hidden': False, 'trait_type': 'PERSONALITY', 'visibility_source': 'game-trait-type'},
    {'name': 'Vampire', 'tuning_id': '2', 'is_hidden': True, 'trait_type': 'HIDDEN', 'visibility_source': 'game-trait-type'},
    {'name': 'Influenza', 'tuning_id': '3', 'is_hidden': True, 'visibility_source': 'game-trait-type'},
    {'name': 'Legacy custom trait', 'tuning_id': '4'},
]
LABELS = [row['name'] for row in ROWS]


class TraitVisibilityTests(unittest.TestCase):
    setUp = fixtures.UsabilityTests.setUp
    tearDown = fixtures.UsabilityTests.tearDown
    add = fixtures.UsabilityTests.add

    def prepare(self):
        person = self.f.people[0]
        person.data = {**person.data, 'game_traits': LABELS, 'game_trait_details': ROWS}
        self.f.session.commit()
        return person

    def test_grouping_preserves_unknown_and_never_guesses_from_names(self):
        self.assertEqual(traits.groups(LABELS, ROWS), {
            'visible': ['Creative'], 'hidden': ['Vampire', 'Influenza'], 'unknown': ['Legacy custom trait'],
        })
        self.assertEqual(traits.groups(['Creative', 'Hidden Talent'])['unknown'], ['Creative', 'Hidden Talent'])
        localized = [{'name': 'Vampire', 'localization_key': 987654321, 'is_hidden': True}]
        self.assertEqual(traits.groups(['hash: 987654321'], localized)['hidden'], ['Vampire'])

    def test_profile_sections_and_edit_roundtrip_keep_all_traits(self):
        person = self.prepare()
        response = self.client.get('/sims/' + person.id)
        self.assertEqual(response.status_code, 200)
        visible = response.text.split('data-trait-group="visible"', 1)[1].split('data-trait-group="hidden"', 1)[0]
        hidden = response.text.split('data-trait-group="hidden"', 1)[1].split('data-trait-group="unknown"', 1)[0]
        self.assertIn('Creative', visible)
        self.assertNotIn('Influenza', visible)
        self.assertIn('Influenza', hidden)
        self.assertIn('<details><summary>Hidden traits (2)', hidden)
        form = {'first_name': person.data.get('first_name') or 'Trait test', 'birth_global_day': '1',
                'trait_sections': '1', 'game_traits': 'Creative', 'game_hidden_traits': 'Vampire\nInfluenza',
                'game_unclassified_traits': 'Legacy custom trait'}
        response = self.client.post('/sims/' + person.id, data=form, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.f.session.refresh(person)
        self.assertEqual(person.data['game_traits'], LABELS)
        self.assertEqual(person.data['game_trait_details'], ROWS)

    def test_new_sim_review_separates_and_saves_hidden_traits(self):
        candidate = self.add('game_candidate', 'Detected trait test', action='new_sim', status='pending',
                             payload={'game_sim_id': 'trait-review-test', 'first_name': 'Trait', 'last_name': 'Review',
                                      'traits': LABELS, 'trait_details': ROWS, 'age_stage': 'Young Adult'})
        page = self.client.get('/p/automation')
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="hidden_traits">Vampire\nInfluenza</textarea>', page.text)
        self.assertIn('name="traits">Creative</textarea>', page.text)
        response = self.client.post('/automation/' + candidate.id + '/accept', data={
            'first_name': 'Trait', 'last_name': 'Review', 'birth_global_day': '1', 'trait_sections': '1',
            'traits': 'Creative', 'hidden_traits': 'Vampire\nInfluenza',
            'unclassified_traits': 'Legacy custom trait',
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        person = self.f.session.scalar(select(Record).where(Record.save_id == self.f.save.id,
                                      Record.kind == 'sim', Record.data['game_sim_id'].as_string() == 'trait-review-test'))
        self.assertIsNotNone(person)
        self.assertEqual(person.data['game_traits'], LABELS)
        self.assertEqual(person.data['game_trait_details'], ROWS)

    def test_receiver_keeps_hidden_health_and_occult_evidence(self):
        person = self.prepare()
        snapshot = {'traits': LABELS, 'trait_details': ROWS, 'traits_scan_supported': True, 'telemetry_version': 6}
        self.assertEqual(game_metadata.occult_identity(snapshot, {})['types'], ['Vampire'])
        self.assertEqual(game_metadata.trait_illnesses(snapshot, {})[0]['name'], 'Influenza')
        automation.reconcile_sim(self.f.session, self.f.save, person, snapshot)
        self.assertEqual(traits.groups(person.data['game_traits'], person.data['game_trait_details'])['hidden'], ['Vampire', 'Influenza'])
        automation.reconcile_sim(self.f.session, self.f.save, person, {'traits': [], 'trait_details': [], 'traits_scan_supported': False, 'telemetry_version': 6})
        self.assertEqual(person.data['game_trait_details'], ROWS)
        automation.reconcile_sim(self.f.session, self.f.save, person, {'traits': ['Creative'], 'trait_details': [{'name': 'Creative', 'tuning_id': '1'}], 'telemetry_version': 6})
        self.assertFalse(person.data['game_trait_details'][0]['is_hidden'])
        automation.reconcile_sim(self.f.session, self.f.save, person, {'traits': [], 'trait_details': [], 'traits_scan_supported': True, 'telemetry_version': 6})
        self.assertEqual(person.data['game_traits'], [])
        self.assertEqual(person.data['game_trait_details'], [])

    def test_manual_classification_is_marked_and_ids_preserved(self):
        rows = traits.reviewed({'traits': 'Legacy custom trait', 'hidden_traits': 'Creative'}, ROWS)['trait_details']
        self.assertEqual(rows[0]['tuning_id'], '4')
        self.assertFalse(rows[0]['is_hidden'])
        self.assertEqual(rows[0]['visibility_source'], 'player')
        self.assertEqual(rows[1]['tuning_id'], '1')
        self.assertTrue(rows[1]['is_hidden'])


if __name__ == '__main__':
    unittest.main()
