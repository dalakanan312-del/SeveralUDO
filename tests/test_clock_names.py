import io
from enum import IntEnum
import json
import struct
import sys
import types
import unittest
from unittest.mock import patch

from tests import test_clock_mod_source


class LocalizedString:
    def __init__(self, key):
        self.hash = key

    def __str__(self):
        return 'hash: {}\ntokens {{ type: RAW_TEXT }}'.format(self.hash)


class ClockNamesTests(unittest.TestCase):
    load_module = test_clock_mod_source.ClockModSourceTests.load_module

    def setUp(self):
        self.mod = self.load_module()
        self.names = self.mod._names
        self.names._bundled = {101: 'Creative', 202: 'Logic', 303: 'First Steps'}
        self.trait_type = IntEnum('TraitType', {'PERSONALITY': 0, 'GAMEPLAY': 1, 'HIDDEN': 4})
        self.enterContext(patch.dict(sys.modules, {'traits.trait_type': types.SimpleNamespace(TraitType=self.trait_type)}))

    def test_localized_proto_is_not_stringified_as_a_name(self):
        trait = type('trait_Creative', (), {'display_name': LocalizedString(101), 'guid64': 50})
        self.assertEqual(self.mod._humanize(trait, ('trait_',)), 'Creative')
        row = self.mod._name_row(trait, ('trait_',))
        self.assertEqual(row, {'name': 'Creative', 'tuning_id': '50', 'localization_key': 101, 'name_source': 'string-table'})

    def test_factory_hash_is_resolved_without_calling_the_factory(self):
        class Factory:
            _string_id = 101

            def __call__(self, *args):
                raise AssertionError('Localized factory must not be called')

        trait = type('trait_Other', (), {'display_name': Factory(), 'guid64': 51})
        self.assertEqual(self.mod._humanize(trait), 'Creative')

    def test_missing_translation_uses_tuning_name_and_keeps_identifiers(self):
        trait = type('trait_LovesTheOutdoors', (), {'display_name': LocalizedString(999), 'guid64': 52})
        row = self.mod._name_row(trait, ('trait_',), 'trait')
        self.assertEqual(row['name'], 'Loves The Outdoors')
        self.assertEqual(row['localization_key'], 999)
        self.assertEqual(row['tuning_id'], '52')
        self.assertEqual(row['name_source'], 'tuning-name')

    def test_unknown_hash_is_explicit_not_a_fake_game_name(self):
        row = self.mod._name_row(LocalizedString(999), kind='trait')
        self.assertEqual(row['name'], 'Unidentified trait')
        self.assertEqual(row['name_source'], 'unresolved')
        self.assertEqual(row['localization_key'], 999)
        self.assertEqual(self.mod._humanize(LocalizedString(0)), '')
        for value in ('hash: 0', 'hash#999', '0', '123', '0x00ff', '<proxy at 0x00ff>'):
            self.assertEqual(self.names.readable_text(value), '')

    def test_literal_text_keeps_accents_and_punctuation(self):
        self.assertEqual(self.mod._humanize(types.SimpleNamespace(display_name='Self-Assured · Élan')), 'Self-Assured · Élan')
        self.assertEqual(self.names.readable_text('{M0.Wizard}{F0.Witch}'), 'Wizard / Witch')
        self.assertEqual(self.names.readable_text('Friend of {0.SimName}'), '')

    def test_broken_optional_display_property_does_not_stop_reporting(self):
        class trait_Creative:
            guid64 = 51

            @property
            def display_name(self):
                raise RuntimeError('Unavailable optional tuning')

        self.assertEqual(self.mod._humanize(trait_Creative(), ('trait_',)), 'Creative')

    def test_traits_skills_and_milestones_are_named_in_the_outgoing_snapshot(self):
        trait = type('trait_Creative', (), {'display_name': LocalizedString(101), 'guid64': 50})
        hidden = type('trait_Lifestyle_CloseKnit', (), {'display_name': LocalizedString(404), 'guid64': 51})
        self.names._bundled[404] = 'Close-Knit'
        skill = type('Skill_Logic', (), {'display_name': LocalizedString(202), 'guid64': 60, 'is_skill': True})
        milestone = type('Milestone_FirstSteps', (), {'display_name': LocalizedString(303), 'guid64': 70})
        self.mod._previous_extended_snapshot = lambda *args: {'traits': ['hash: 101']}
        sim = types.SimpleNamespace(
            trait_tracker=types.SimpleNamespace(equipped_traits=(trait, hidden), traits=(hidden, trait)),
            all_skills=(skill,),
            get_statistic=lambda _: types.SimpleNamespace(stat_type=skill, get_user_value=lambda: 7),
            developmental_milestone_tracker=types.SimpleNamespace(get_all_completed_milestones=lambda: (milestone,)),
        )
        result = self.mod._extended_snapshot(sim, None)
        self.assertEqual(result['traits'], ['Close-Knit', 'Creative'])
        self.assertEqual(result['lifestyles'], ['Close-Knit'])
        self.assertEqual(result['skills'][0]['name'], 'Logic')
        self.assertEqual(result['skills'][0]['level'], 7)
        self.assertEqual(result['milestones'], ['First Steps'])
        self.assertEqual(result['stable_tuning_ids']['traits']['Creative'], '50')
        self.assertTrue(result['traits_scan_supported'])
        self.assertNotIn('hash:', json.dumps(result))
        self.assertEqual(result['trait_details'][1]['localization_key'], 101)
        self.assertEqual(result['milestone_details'][0]['localization_key'], 303)

    def test_equipped_and_hidden_traits_are_unioned_without_duplicates(self):
        first = type('trait_Creative', (), {'guid64': 1, 'trait_type': self.trait_type.PERSONALITY})
        second = type('trait_HiddenFlag', (), {'guid64': 2, 'trait_type': self.trait_type.HIDDEN})
        sim = types.SimpleNamespace(trait_tracker=types.SimpleNamespace(equipped_traits=(first,), traits=(first, second)))
        result = self.mod._extended_snapshot(sim, None)
        self.assertEqual(result['traits'], ['Creative', 'Hidden Flag'])
        self.assertEqual(len(result['trait_details']), 2)
        self.assertFalse(result['trait_details'][0]['is_hidden'])
        self.assertTrue(result['trait_details'][1]['is_hidden'])
        self.assertEqual(result['trait_details'][1]['trait_type'], 'HIDDEN')
        self.assertEqual(result['trait_details'][1]['trait_type_id'], 4)

    def test_visibility_comes_from_type_not_name_or_cas_flag(self):
        values = (
            type('trait_HiddenTalent', (), {'guid64': 1, 'trait_type': self.trait_type.GAMEPLAY, 'cas_trait_hidden': True}),
            type('trait_FriendlyLookingName', (), {'guid64': 2, 'trait_type': self.trait_type.HIDDEN, 'cas_trait_hidden': False}),
            type('trait_HiddenUnknown', (), {'guid64': 3}),
            type('trait_InvalidType', (), {'guid64': 4, 'trait_type': 'broken'}),
        )
        rows, supported = self.mod._trait_snapshot(types.SimpleNamespace(trait_tracker=types.SimpleNamespace(equipped_traits=values)))
        by_id = {row['tuning_id']: row for row in rows}
        self.assertTrue(supported)
        self.assertFalse(by_id['1']['is_hidden'])
        self.assertTrue(by_id['2']['is_hidden'])
        self.assertNotIn('is_hidden', by_id['3'])
        self.assertNotIn('is_hidden', by_id['4'])

    def test_legacy_traits_resolve_by_key_not_sorted_position(self):
        self.mod._previous_extended_snapshot = lambda *args: {'traits': ['hash: 202', 'hash: 101', 'Custom readable name']}
        result = self.mod._extended_snapshot(types.SimpleNamespace(), None)
        self.assertEqual(result['traits'], ['Logic', 'Creative', 'Custom readable name'])
        self.assertFalse(result['traits_scan_supported'])

    def test_localized_relationship_keeps_family_classification(self):
        bit = type('relationshipBit_Family_Parent', (), {'guid64': 99, 'display_name': 'Dear one'})
        relative = types.SimpleNamespace(sim_id=7, first_name='Ada', last_name='Family')
        sim = types.SimpleNamespace(relationship_tracker=types.SimpleNamespace(
            get_target_sim_infos=lambda: (relative,), get_all_bits=lambda _: (bit,),
        ))
        result, _ = self.mod._relationship_details(sim, [])
        self.assertEqual(result['relationships'][0]['category'], 'Family')
        self.assertEqual(result['relationships'][0]['relationship_bits'], ['Dear one'])

    def test_localized_health_buff_keeps_underlying_disease_evidence(self):
        buff = type('adeepindigo_HealthcareRedux_Diseases_InfluenzaBuff', (), {'display_name': 'Feeling awful', 'guid64': 80})
        self.assertEqual(self.mod._health_condition_name(buff, 'Healthcare Redux'), 'Influenza')

    def test_ids_are_never_mistaken_for_localization_keys(self):
        self.assertIsNone(self.names.localization_key(types.SimpleNamespace(guid64=101)))
        self.assertEqual(self.names.localization_key('hash: 0x65'), 101)
        self.assertEqual(self.names.localization_key(LocalizedString(-1)), 0xffffffff)

    @staticmethod
    def table(values):
        header = b'STBL' + struct.pack('<HBQHI', 5, 0, len(values), 0, 0)
        return header + b''.join(struct.pack('<IBH', key, 0, len(text.encode('utf-8'))) + text.encode('utf-8') for key, text in values.items())

    def test_corrupt_or_oversize_string_tables_are_ignored(self):
        data = self.table({101: 'Creative', 202: 'Logique'})
        self.assertEqual(self.names.read_stbl(data), {101: 'Creative', 202: 'Logique'})
        self.assertEqual(self.names.read_stbl(data[:-1]), {})
        self.assertEqual(self.names.read_stbl(b'wrong'), {})
        self.assertEqual(self.names.read_stbl(b'STBL' + b'\0' * self.names.MAX_TABLE_BYTES), {})

    def test_native_resources_are_cached_in_bounded_english_only_batches(self):
        resources = types.ModuleType('sims4.resources')
        english = [types.SimpleNamespace(instance=i, group=0x80000000) for i in range(1, 7)]
        other_language = types.SimpleNamespace(instance=(7 << 56) + 1, group=0x80000000)
        calls = []
        resources.get_all_resources_of_type = lambda kind: english + [other_language]
        resources.ResourceLoader = lambda key: types.SimpleNamespace(load=lambda: (
            calls.append(key.instance), io.BytesIO(self.table({1000 + key.instance: 'Custom trait {}'.format(key.instance)}))
        )[1])
        sims4 = types.ModuleType('sims4')
        sims4.resources = resources
        with patch.dict(sys.modules, {'sims4': sims4, 'sims4.resources': resources}), patch.object(self.names.time, 'monotonic', return_value=100):
            self.names.advance_resources()
            self.assertEqual(len(calls), 4)
            self.names.advance_resources()
            self.assertEqual(len(calls), 4)
        with patch.dict(sys.modules, {'sims4': sims4, 'sims4.resources': resources}), patch.object(self.names.time, 'monotonic', return_value=111):
            self.names.advance_resources()
        self.assertEqual(calls, [1, 2, 3, 4, 5, 6])
        self.assertEqual(self.mod._humanize(LocalizedString(1006)), 'Custom trait 6')
        self.assertEqual(self.names.diagnostics()['remaining_string_tables'], 0)

    def test_resource_failure_can_retry_and_missing_dictionary_is_safe(self):
        resources = types.ModuleType('sims4.resources')
        resources.get_all_resources_of_type = lambda _: (_ for _ in ()).throw(RuntimeError('not ready'))
        sims4 = types.ModuleType('sims4')
        sims4.resources = resources
        with patch.dict(sys.modules, {'sims4': sims4, 'sims4.resources': resources}):
            self.names.advance_resources()
        self.assertIsNone(self.names._resources)
        self.names._bundled = None
        with patch.object(self.names.pkgutil, 'get_data', side_effect=OSError('missing')):
            self.assertEqual(self.names.bundled_labels(), {})
        self.assertEqual(self.mod._humanize(type('trait_Creative', (), {}), ('trait_',)), 'Creative')


if __name__ == '__main__':
    unittest.main()
