"""Preferences are presented separately without deleting raw game evidence."""
import copy
import unittest
from sqlalchemy import select
from app import automation, trait_visibility as traits
from app.models import Record
from tests import test_trait_visibility as fixtures

PREF_ROWS = [
    {'name': 'Likes Gardening', 'tuning_id': '264181', 'is_hidden': True},
    {'name': 'Dislikes Painting', 'tuning_id': '102', 'is_hidden': False},
    {'name': 'Sim Preference Likes Music Hip Hop', 'tuning_id': '103'},
    {'name': 'Sim Preference Dislikes Color Blue', 'tuning_id': '104'},
    {'name': 'Unspecified taste', 'tuning_id': '105', 'is_preference': True},
]
PREF_LABELS = [r['name'] for r in PREF_ROWS]


class PreferenceClassificationTests(unittest.TestCase):
    def test_old_names_and_new_metadata_are_separated(self):
        grouped = traits.groups(fixtures.LABELS + PREF_LABELS, fixtures.ROWS + PREF_ROWS)
        self.assertEqual(grouped['visible'], ['Creative'])
        self.assertEqual(grouped['hidden'], ['Vampire', 'Influenza'])
        self.assertEqual(grouped['unknown'], ['Legacy custom trait'])
        self.assertEqual(grouped['likes'], [PREF_LABELS[0], PREF_LABELS[2]])
        self.assertEqual(grouped['dislikes'], [PREF_LABELS[1], PREF_LABELS[3]])
        self.assertEqual(grouped['preferences'], [PREF_LABELS[4]])
        self.assertEqual(traits.groups(PREF_LABELS)['likes'], grouped['likes'])
        self.assertEqual(traits.preference_category({'name': 'Musique', 'trait_type': 'TraitType.LIKE'}), 'likes')
        self.assertEqual(traits.preference_category({'name': 'Peinture', 'trait_type': 'DISLIKE'}), 'dislikes')
        self.assertEqual(traits.preference_category({'name': 'Localized', 'technical_name': 'trait_SimPreference_Dislikes_Activities_Comedy'}), 'dislikes')

    def test_internal_preferences_and_personality_names_are_not_guessed(self):
        labels = ['Baby Feed Preference Body', 'Kemzima Baby Diaper Preference SET', 'Childlike', 'Likely Winner', 'Dislikes Children']
        details = [{'name': 'Dislikes Children', 'trait_type': 'PERSONALITY'}]
        grouped = traits.groups(labels, details)
        self.assertEqual(grouped['unknown'], labels)
        self.assertFalse(grouped['likes'] or grouped['dislikes'] or grouped['preferences'])
        self.assertIsNone(traits.preference_category({'name': 'Likes attention', 'is_preference': False}))

    def test_native_preferences_merge_once_and_keep_unknown_polarity(self):
        result = traits.preference_groups(traits.groups(PREF_LABELS, PREF_ROWS), [
            'Likes Gardening', {'name': 'Piano', 'preference_type': 'DISLIKE'}, 'Favorite tradition'])
        self.assertEqual(result['likes'].count('Likes Gardening'), 1)
        self.assertIn('Piano', result['dislikes'])
        self.assertIn('Favorite tradition', result['preferences'])

    def test_older_forms_preserve_preferences_and_new_forms_can_remove_them(self):
        old = {'traits': 'Creative', 'hidden_traits': 'Vampire\nInfluenza', 'unclassified_traits': 'Legacy custom trait'}
        result = traits.reviewed(old, fixtures.ROWS + PREF_ROWS, values=fixtures.LABELS + PREF_LABELS)
        self.assertCountEqual(result['traits'], fixtures.LABELS + PREF_LABELS)
        self.assertCountEqual(result['trait_details'], fixtures.ROWS + PREF_ROWS)
        self.assertEqual(traits.reviewed(old, values=['Likes Fishing'])['traits'][-1], 'Likes Fishing')
        result = traits.reviewed({**old, 'preference_sections': '1'}, fixtures.ROWS + PREF_ROWS)
        self.assertEqual(result['traits'], fixtures.LABELS)

    def test_player_preferences_preserve_ids_and_can_be_reclassified(self):
        result = traits.reviewed({'preference_sections': '1', 'likes': 'Fishing'}, [{'name': 'Fishing', 'tuning_id': '77'}])
        self.assertEqual(result['traits'], ['Fishing'])
        self.assertEqual(result['trait_details'][0]['tuning_id'], '77')
        self.assertEqual(traits.groups(**{'values':result['traits'], 'details':result['trait_details']})['likes'], ['Fishing'])
        result = traits.reviewed({'preference_sections': '1', 'traits': 'Fishing'}, result['trait_details'])
        self.assertEqual(traits.groups(result['traits'], result['trait_details'])['visible'], ['Fishing'])
        self.assertEqual(result['trait_details'][0]['preference_source'], 'player')

    def test_older_report_preserves_metadata_but_new_classification_wins(self):
        old = [{'name': 'Gardening', 'tuning_id': '77', 'is_hidden': False, 'trait_type': 'LIKE'}]
        retained = traits.retain_classification([{'name': 'Jardinage', 'tuning_id': '77', 'is_hidden': None}], old)
        self.assertFalse(retained[0]['is_hidden'])
        self.assertEqual(traits.preference_category(retained[0]), 'likes')
        fresh = traits.retain_classification([{'name': 'Gardening', 'tuning_id': '77', 'trait_type': 'DISLIKE'}], old)
        self.assertEqual(traits.preference_category(fresh[0]), 'dislikes')


class PreferenceUiTests(unittest.TestCase):
    setUp = fixtures.TraitVisibilityTests.setUp
    tearDown = fixtures.TraitVisibilityTests.tearDown
    add = fixtures.TraitVisibilityTests.add

    def prepare(self):
        person = self.f.people[0]
        person.data = {**person.data, 'game_traits': fixtures.LABELS + PREF_LABELS,
                       'game_trait_details': fixtures.ROWS + PREF_ROWS,
                       'game_preferences': ['Likes Gardening', 'Likes Dancing', 'Unspecified native preference']}
        self.f.session.commit()
        return person

    def form(self, prefix=''):
        return {'trait_sections': '1', 'preference_sections': '1',
                prefix+'traits': 'Creative', prefix+'hidden_traits': 'Vampire\nInfluenza',
                prefix+'unclassified_traits': 'Legacy custom trait',
                prefix+'likes': '\n'.join([PREF_LABELS[0], PREF_LABELS[2]]),
                prefix+'dislikes': '\n'.join([PREF_LABELS[1], PREF_LABELS[3]]), prefix+'preferences': PREF_LABELS[4]}

    def test_profile_and_editor_keep_preferences_out_of_traits(self):
        person = self.prepare()
        response = self.client.get('/sims/' + person.id)
        self.assertEqual(response.status_code, 200)
        html = response.text
        regular = html.split('data-trait-group="visible"', 1)[1].split('data-preference-group="likes"', 1)[0]
        self.assertIn('Creative', regular)
        for value in PREF_LABELS:
            self.assertNotIn(value, regular)
        likes = html.split('data-preference-group="likes"', 1)[1].split('data-preference-group="dislikes"', 1)[0]
        self.assertIn('Likes <span>3</span>', likes)
        self.assertEqual(likes.count('<span>Likes Gardening</span>'), 1)
        self.assertIn('Likes Dancing', likes)
        self.assertNotIn('Degrees &amp; preferences', html)
        self.assertNotIn('Degrees & preferences', html)
        self.assertIn('name="game_likes">Likes Gardening\nSim Preference Likes Music Hip Hop</textarea>', html)
        self.assertIn('name="game_dislikes">Dislikes Painting', html)

    def test_editor_roundtrip_retains_all_raw_traits_ids_and_native_preferences(self):
        person = self.prepare()
        native = copy.deepcopy(person.data['game_preferences'])
        response = self.client.post('/sims/' + person.id, data={**self.form('game_'),
            'first_name': 'Ada', 'birth_global_day': '1'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.f.session.refresh(person)
        self.assertCountEqual(person.data['game_traits'], fixtures.LABELS + PREF_LABELS)
        self.assertCountEqual(person.data['game_trait_details'], fixtures.ROWS + PREF_ROWS)
        self.assertEqual(person.data['game_preferences'], native)

    def test_new_sim_review_saves_separate_preferences(self):
        candidate = self.add('game_candidate', 'Preference review', action='new_sim', status='pending', payload={
            'game_sim_id': 'preferences-review', 'first_name': 'Taste', 'last_name': 'Review',
            'traits': fixtures.LABELS + PREF_LABELS, 'trait_details': fixtures.ROWS + PREF_ROWS, 'age_stage': 'Young Adult'})
        page = self.client.get('/p/automation')
        self.assertEqual(page.status_code, 200)
        self.assertIn('name="likes">Likes Gardening', page.text)
        self.assertIn('name="dislikes">Dislikes Painting', page.text)
        response = self.client.post('/automation/' + candidate.id + '/accept', data={**self.form(),
            'first_name': 'Taste', 'last_name': 'Review', 'birth_global_day': '1'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        person = self.f.session.scalar(select(Record).where(Record.save_id==self.f.save.id, Record.kind=='sim',
                         Record.data['game_sim_id'].as_string()=='preferences-review'))
        self.assertCountEqual(person.data['game_traits'], fixtures.LABELS + PREF_LABELS)
        self.assertCountEqual(person.data['game_trait_details'], fixtures.ROWS + PREF_ROWS)

    def test_live_reports_keep_raw_preferences_and_health_evidence(self):
        person = self.prepare()
        automation.reconcile_sim(self.f.session, self.f.save, person, {'traits': fixtures.LABELS + PREF_LABELS,
            'trait_details': fixtures.ROWS + PREF_ROWS, 'traits_scan_supported': True, 'telemetry_version': 6})
        self.assertCountEqual(person.data['game_traits'], fixtures.LABELS + PREF_LABELS)
        grouped = traits.groups(person.data['game_traits'], person.data['game_trait_details'])
        self.assertIn('Influenza', grouped['hidden'])
        self.assertIn('Likes Gardening', grouped['likes'])
        self.assertNotIn('Likes Gardening', grouped['hidden'])

    def test_preserved_branch_profile_is_separated_without_rewriting_history(self):
        person = self.prepare()
        self.f.enable()
        self.f.capture(index=0)
        self.f.finish_modern()
        before = {r.id: (copy.deepcopy(r.data), r.version) for r in self.f.session.scalars(select(Record))}
        response = self.client.get('/p/infinite-decades', params={'sim_id':person.id})
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-preference-group="likes"', response.text)
        self.assertIn('<span>Likes Gardening</span>', response.text)
        self.f.session.expire_all()
        self.assertEqual(before, {r.id:(r.data,r.version) for r in self.f.session.scalars(select(Record))})


if __name__ == '__main__':
    unittest.main()
