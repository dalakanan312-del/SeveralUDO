import copy
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import sims3_save as parser


def text(value):
    raw = value.encode()
    assert len(raw) < 128
    return bytes([len(raw)]) + raw


def dbpf(raw, resource_type):
    header = bytearray(96)
    header[:4] = b'DBPF'
    struct.pack_into('<I', header, 4, 2)
    struct.pack_into('<I', header, 36, 1)
    struct.pack_into('<I', header, 64, 96 + len(raw))
    return bytes(header) + raw + struct.pack('<9I', 0, resource_type, 0, 0, 1, 96, len(raw), len(raw), 0)


def objs(first_name='Alice', sim_id=123, household_id=37):
    # Minimal actual OBJS format: string, human Sim and household.
    definitions = [
        ('System.String', []),
        ('Sims3.Gameplay.CAS.SimDescription', [('mSimDescriptionId',15),('mSimFlags',14),('mFirstName',1),('mLastName',1),('mHousehold',1),('Pregnancy',1)]),
        ('Sims3.Gameplay.CAS.Household', [('mHouseholdId',15),('mFamilyFunds',9),('mLotId',15),('mName',1)]),
    ]
    values = [b'\x10'+struct.pack('<I',0)+text(first_name), b'\x10'+struct.pack('<I',0)+text('Test'),
              b'\x10'+struct.pack('<IQIIIII',1,sim_id,0x2110,1,2,4,0),
              b'\x10'+struct.pack('<IQiQI',2,household_id,2000,987,2)]
    offsets, content = [], b''
    for value in values:
        offsets.append(28+len(content)); content += value
    index_offset = 28+len(content)
    index = struct.pack('<4I',*offsets)
    types_offset = index_offset+len(index)
    types = b''
    for name, fields in definitions:
        types += b'\0'+text(name)+bytes([len(fields)])
        for field, code in fields: types += text(field)+bytes([code])
    return struct.pack('<7I',0x500,int.from_bytes(b'OBJS','little'),3,4,types_offset,index_offset,types_offset+len(types))+content+index+types


def make_save(root, name='Test.sims3'):
    folder = Path(root)/name
    folder.mkdir()
    utf16 = lambda s: struct.pack('<I',len(s))+s.encode('utf-16le')
    meta = struct.pack('<I',3)+utf16('Test')+utf16('Test')+struct.pack('<IQ',0,37)
    (folder/'Meta.data').write_bytes(dbpf(meta,0x628A788F))
    (folder/'Test_0x00000000.nhd').write_bytes(dbpf(objs(),parser.OBJS))
    for p in folder.iterdir(): os.utime(p,(100,100))
    return folder


class ParserTests(unittest.TestCase):
    def test_traits_use_full_sims3_ids_and_deduplicate_rewards(self):
        class Graph:
            def resolve(self, value): return value
        brave, swimmer, custom = 13271263770231521728, 8068762026313529312, 18446744073709551001
        traits = parser.extract_traits(Graph(), {'mTraitManager':{
            'mValues':{brave:{'mTraitGuid':brave}, swimmer:{'mTraitGuid':swimmer}, custom:{'mTraitGuid':custom}},
            'mRewardTraits':[brave, {'mTraitGuid':swimmer}]}})
        self.assertEqual(traits['traits'], ['Brave', 'Loves to Swim', f'Unknown Sims 3 trait (ID {custom})'])
        self.assertTrue(traits['traits_scan_supported'])
        self.assertEqual(len(traits['trait_details']),3)
        self.assertEqual(traits['trait_details'][-1]['trait_id'],str(custom))
        from app import game_metadata
        self.assertIsNone(game_metadata.localization_hash(traits['traits'][-1]))
        self.assertEqual(game_metadata.readable_trait_labels(traits['traits']),traits['traits'])

    def test_empty_traits_distinct_from_missing_or_malformed_traits(self):
        class Graph:
            def resolve(self, value): return value
        graph=Graph()
        self.assertEqual(parser.extract_traits(graph,{}),{})
        self.assertEqual(parser.extract_traits(graph,{'mTraitManager':{'mValues':None}}),{})
        self.assertEqual(parser.extract_traits(graph,{'mTraitManager':{'mValues':{}}})['traits'],[])
        for manager in [{'mValues':{123:{'mTraitGuid':456}}}, {'mValues':{123:None}},
                        {'mValues':{},'mRewardTraits':{'unknown':'shape'}}, {'mValues':{123:True}}]:
            with self.assertRaises(parser.SaveReadError): parser.extract_traits(graph,{'mTraitManager':manager})

    def test_multiple_worlds_same_sim_and_household_local_ids_do_not_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            folder=make_save(root)
            (folder/'Away_0x11111111.nhd').write_bytes(dbpf(objs('OlderCopy',123),parser.OBJS))
            (folder/'Other_0x22222222.nhd').write_bytes(dbpf(objs('Bob',456),parser.OBJS))
            scan=parser.inspect_save(folder,quiet_seconds=0,all_worlds=True)
            self.assertEqual(scan['world_count'],3)
            self.assertEqual(scan['sim_count'],2)
            self.assertEqual(scan['duplicate_sim_copies'],1)
            people={s['game_sim_id']:s for s in scan['sims']}
            self.assertEqual(people['123']['name'],'Alice Test')
            self.assertEqual(people['456']['name'],'Bob Test')
            self.assertNotEqual(people['123']['game_household_id'],people['456']['game_household_id'])
            self.assertEqual(len({h['game_household_id'] for h in scan['households']}),3)
            self.assertEqual(sum(len(h['member_game_ids']) for h in scan['households']),2)

    def test_unsupported_inactive_world_does_not_block_active_world(self):
        with tempfile.TemporaryDirectory() as root:
            folder=make_save(root)
            (folder/'Unsupported_0x11111111.nhd').write_bytes(b'unknown format')
            scan=parser.inspect_save(folder,quiet_seconds=0,all_worlds=True)
            self.assertEqual(scan['sim_count'],1)
            self.assertEqual(scan['skipped_worlds'],['Unsupported_0x11111111.nhd'])
            self.assertTrue(scan['warnings'])

    def test_save_scope_includes_homeless_service_and_householdless_without_claiming_played(self):
        from app.sims3_save_sync import _population
        from app.save_scanner import relevant_population
        scan={'game_edition':'sims3','slot':{'active_household_game_id':'a'},'households':[
            {'game_household_id':'a','has_home_lot':True,'is_player':True},
            {'game_household_id':'b','has_home_lot':True,'is_player':False},
            {'game_household_id':'c','has_home_lot':False,'is_player':False}],
            'sims':[{'game_sim_id':str(i),'game_household_id':h} for i,h in enumerate(['a','b','c',''])]}
        self.assertEqual(len(relevant_population(_population(scan,'household'))[1]),1)
        self.assertEqual(len(relevant_population(_population(scan,'residents'))[1]),2)
        homes,sims=relevant_population(_population(scan,'save'))
        self.assertEqual(len(sims),4)
        self.assertEqual(sum(h['is_player'] for h in homes),1)

    def test_actual_dbpf_objs_round_trip_no_clock_or_false_delivery(self):
        with tempfile.TemporaryDirectory() as root:
            folder = make_save(root)
            original = {p.name:p.read_bytes() for p in folder.iterdir()}
            scan = parser.inspect_save(folder)
            sim = scan['sims'][0]
            self.assertEqual(sim['name'],'Alice Test')
            self.assertEqual(sim['age_stage'],'youngadult')
            self.assertEqual(sim['game_sim_id'],'123')
            self.assertEqual(scan['households'][0]['funds'],2000)
            self.assertIsNone(scan['slot']['game_day'])
            for key in ['is_pregnant','is_dead','traits','illnesses','age_days']:
                self.assertNotIn(key,sim)
            self.assertEqual(original,{p.name:p.read_bytes() for p in folder.iterdir()})
            self.assertEqual(scan['fingerprint'],parser.inspect_save(folder)['fingerprint'])

    def test_pending_save_is_not_read(self):
        with tempfile.TemporaryDirectory() as root:
            folder=make_save(root)
            os.utime(folder/'Meta.data',None)
            with self.assertRaisesRegex(parser.SaveReadError,'finish saving'):
                parser.inspect_save(folder)

    def test_save_changing_during_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            folder=make_save(root)
            signature=parser.file_signature(folder)
            with patch.object(parser,'file_signature',side_effect=[signature,signature+(('new',1,2),)]):
                with self.assertRaisesRegex(parser.SaveReadError,'changed while copying'):
                    parser.inspect_save(folder)

    def test_backups_and_ambiguous_worlds_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            folder=make_save(root)
            backup=make_save(root,'Test.sims3.backup')
            self.assertEqual(parser.discover_saves(root),[folder])
            with self.assertRaises(parser.SaveReadError): parser.inspect_save(backup)
            (folder/'Test_0x11111111.nhd').write_bytes((folder/'Test_0x00000000.nhd').read_bytes())
            with self.assertRaisesRegex(parser.SaveReadError,'active saved world'): parser.inspect_save(folder,quiet_seconds=0)

    def test_compression_bounds_and_invalid_graph(self):
        self.assertEqual(parser.decompress(b'\x50\xfb\0\0\x03\xffabc',3),b'abc')
        for data in [b'',b'\x50\xfb\0\0\x03\x00\x00',b'\x50\xfb\0\0\x03\xffab']:
            with self.assertRaises(parser.SaveReadError): parser.decompress(data,3)
        with self.assertRaises(parser.SaveReadError): parser.ObjectGraph(b'bad')
        bad=bytearray(objs());struct.pack_into('<I',bad,20,0xffffffff)
        with self.assertRaises(parser.SaveReadError): parser.ObjectGraph(bad)

    def test_family_references_and_positive_pregnancy_only(self):
        class Graph:
            def __init__(self):
                self.values={1:{'mSimDescriptionId':1,'mSimFlags':0x2104,'mFirstName':'Child','mLastName':'Test',
                                'mHousehold':parser.Ref(3),'mGenealogy':parser.Ref(4),'Pregnancy':parser.Ref(0)},
                             2:{'mSimDescriptionId':2,'mSimFlags':0x2120,'mFirstName':'Mother','mLastName':'Test',
                                'mHousehold':parser.Ref(3),'Pregnancy':parser.Ref(99)},
                             3:{'mHouseholdId':37,'mName':'Test','mFamilyFunds':10,'mLotId':100},
                             4:{'mNaturalParents':parser.Ref(5)},5:[parser.Ref(6)],6:{'mSim':parser.Ref(2)}}
            def resolve(self,v): return self.values.get(v.index) if isinstance(v,parser.Ref) else v
            def objects(self,name):
                return [(i,self.values[i]) for i in ([1,2] if name.endswith('SimDescription') else [3])]
        homes,sims,warnings=parser.extract_population(Graph(),{'active_household_game_id':'37','world_name':'Test'})
        self.assertEqual(sims[0]['parent_game_sim_ids'],['2'])
        self.assertTrue(sims[1]['is_pregnant'])
        self.assertNotIn('is_pregnant',sims[0])


class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.db import Base,engine
        from app import models
        Base.metadata.create_all(engine)

    def setUp(self):
        from app import sims3_save_sync as reader
        self.reader=reader
        self.temp=tempfile.TemporaryDirectory()
        self.folder=make_save(self.temp.name)
        self.env=patch.dict(os.environ,{'SIMS3_SAVES_DIR':self.temp.name,'DECADES_SIMS3_READER_STATE':str(Path(self.temp.name)/'reader.json')})
        self.env.start()
        from app.db import SessionLocal
        from app.models import ChronicleSave,Workspace
        with SessionLocal() as session:
            workspace=Workspace(name='S3 reader test');session.add(workspace);session.flush()
            save=ChronicleSave(workspace_id=workspace.id,name='S3 reader test',global_day=42,settings={'game_mode':'sims3'})
            session.add(save);session.commit();self.save_id=save.id

    def tearDown(self):
        self.env.stop();self.temp.cleanup()

    def test_background_import_dedup_restart_and_master_pause(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave,Record
        from sqlalchemy import select
        self.reader.configure(self.save_id,'Test.sims3',True)
        self.reader.tick()
        with SessionLocal() as session:
            rows=list(session.scalars(select(Record).where(Record.save_id==self.save_id,Record.kind=='game_candidate')))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0].label,'Alice Test')
            self.assertEqual(rows[0].data['status'],'pending')
            self.assertEqual(rows[0].data['payload']['source'],'read-only Sims 3 save scan')
            self.assertEqual(session.get(ChronicleSave,self.save_id).global_day,42)
        self.reader._observed.clear() # process restart
        with patch.object(self.reader,'apply_scan',side_effect=AssertionError('duplicate import')):
            self.reader.tick()
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id);save.settings={**save.settings,'automation_enabled':False};session.commit()
        with patch.object(parser,'inspect_save',side_effect=AssertionError('disabled read')):
            self.reader.tick()
        self.assertIn('master automation',self.reader.status(self.save_id)['status'])

    def test_reject_unknown_folder_and_game_edition(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave
        with self.assertRaises(parser.SaveReadError): self.reader.configure(self.save_id,'../elsewhere',True)
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id);save.settings={'game_mode':'sims4'}
            with self.assertRaises(parser.SaveReadError): self.reader.apply_scan(session,save,parser.inspect_save(self.folder),{'123'})

    def test_scope_switch_rescans_without_duplicating_existing_sim_or_legacy_household(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave,Record
        from sqlalchemy import select
        with SessionLocal() as session:
            home=Record(save_id=self.save_id,kind='household',label='Test',data={'game_household_id':'37','last_game_world':'Test'})
            session.add(home);session.commit();home_id=home.id
        self.reader.configure(self.save_id,'Test.sims3',True)
        self.reader.tick()
        self.reader.configure(self.save_id,'Test.sims3',True,'save')
        self.reader.tick()
        self.assertEqual(self.reader.status(self.save_id)['scope'],'save')
        with SessionLocal() as session:
            homes=list(session.scalars(select(Record).where(Record.save_id==self.save_id,Record.kind=='household')))
            self.assertEqual(len(homes),1)
            self.assertEqual(homes[0].id,home_id)
            self.assertTrue(homes[0].data['game_household_id'].startswith('sims3:'))
            candidates=list(session.scalars(select(Record).where(Record.save_id==self.save_id,Record.kind=='game_candidate')))
            self.assertEqual(len(candidates),1)

    def test_old_world_snapshot_cannot_regress_existing_age(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave,Record
        scan=parser.inspect_save(self.folder)
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            sim=Record(save_id=save.id,kind='sim',label='Alice Test',data={'game_sim_id':'123','game_age_stage':'elder','sims3_save_observed_at':'2026-09-08T00:00:00+00:00'})
            session.add(sim);session.flush()
            result=self.reader.apply_scan(session,save,scan,{'123'})
            self.assertEqual(result['stale_sim_snapshots_skipped'],1)
            self.assertEqual(sim.data['game_age_stage'],'elder')

    def test_residents_mode_reads_housed_households_in_other_worlds(self):
        from app.db import SessionLocal
        from app.models import Record
        from sqlalchemy import select
        (self.folder/'Away_0x11111111.nhd').write_bytes(dbpf(objs('Bob',456),parser.OBJS))
        os.utime(self.folder/'Away_0x11111111.nhd',(100,100))
        self.reader.configure(self.save_id,'Test.sims3',True,'residents')
        self.reader.tick()
        with SessionLocal() as session:
            items=list(session.scalars(select(Record).where(Record.save_id==self.save_id,Record.kind=='game_candidate')))
            self.assertEqual({item.label for item in items},{'Alice Test','Bob Test'})
        self.assertEqual(self.reader.status(self.save_id)['world_count'],2)

    def test_existing_sim_unknown_fields_are_preserved(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave,Record
        from sqlalchemy import select
        scan=parser.inspect_save(self.folder)
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            sim=Record(save_id=save.id,kind='sim',label='Alice Test',global_day=1,data={'game_sim_id':'123','first_name':'Alice','last_name':'Test','sex':'Female','game_traits':['Brave'],'game_skills':['Cooking (level 5)'],'game_was_pregnant':True})
            session.add(sim);session.flush()
            self.reader.apply_scan(session,save,scan,{'123'})
            self.assertEqual(sim.data['game_traits'],['Brave'])
            self.assertEqual(sim.data['game_skills'],['Cooking (level 5)'])
            self.assertTrue(sim.data['game_was_pregnant'])
            actions=[r.data['action'] for r in session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='game_candidate'))]
            self.assertNotIn('pregnancy_ended',actions)
            self.assertNotIn('delivery',actions)
            self.assertEqual(save.global_day,42)

    def test_dismissed_new_sim_is_not_reopened(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave,Record
        from sqlalchemy import select
        scan=parser.inspect_save(self.folder)
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            self.reader.apply_scan(session,save,scan,{'123'})
            item=session.scalar(select(Record).where(Record.save_id==save.id,Record.kind=='game_candidate'))
            item.data={**item.data,'status':'dismissed'}
            session.flush()
            self.reader.apply_scan(session,save,scan,{'123'})
            rows=list(session.scalars(select(Record).where(Record.save_id==save.id,Record.kind=='game_candidate')))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0].data['status'],'dismissed')

    def test_commit_marker_prevents_replay_if_local_state_write_failed(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave
        scan=parser.inspect_save(self.folder)
        self.reader.configure(self.save_id,'Test.sims3',True)
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            save.settings={**save.settings,'sims3_reader_import_key':self.reader._import_key(scan,self.reader.status(self.save_id))}
            session.commit()
        with patch.object(self.reader,'apply_scan',side_effect=AssertionError('replayed committed scan')): self.reader.tick()
        self.assertIn('already imported',self.reader.status(self.save_id)['status'])

    def test_clock_page_preview_and_local_reader_controls(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            from app.db import SessionLocal
            from app.models import ChronicleSave,User,Membership
            from sqlalchemy import select
            with SessionLocal() as session:
                save=session.get(ChronicleSave,self.save_id)
                user=session.scalar(select(User).where(User.email=='local@decades.invalid'))
                session.add(Membership(user_id=user.id,workspace_id=save.workspace_id));session.commit()
            response=client.post('/saves/select',data={'save_id':self.save_id},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            page=client.get('/p/clock')
            self.assertEqual(page.status_code,200)
            self.assertIn('Read Sims 3 saves automatically',page.text)
            preview=client.post('/api/sims3-save/preview',data={'file_name':'Test.sims3'},follow_redirects=True)
            self.assertEqual(preview.status_code,200)
            self.assertIn('Alice Test',preview.text)
            configured=client.post('/api/sims3-save/settings',data={'file_name':'Test.sims3','enabled':'on'})
            self.assertEqual(configured.status_code,200)
            self.assertTrue(self.reader.status(self.save_id)['enabled'])
            bad=client.post('/api/sims3-save/settings',data={'file_name':'../bad'})
            self.assertEqual(bad.status_code,400)

    def test_decoder_upgrade_enriches_same_save_once_and_keeps_candidate(self):
        from app.db import SessionLocal
        from app.models import Record
        from sqlalchemy import select
        self.reader.configure(self.save_id,'Test.sims3',True,'save')
        self.reader.tick()
        with SessionLocal() as session:
            item=session.scalar(select(Record).where(Record.save_id==self.save_id,Record.kind=='game_candidate'))
            candidate_id=item.id
        scan=parser.inspect_save(self.folder)
        scan['sims'][0].update(traits=['Brave'],trait_details=[{'name':'Brave','trait_id':'13271263770231521728'}],traits_scan_supported=True)
        self.reader._observed.clear()
        with patch.object(parser,'READER_REVISION',parser.READER_REVISION+1), patch.object(parser,'inspect_save',return_value=scan):
            self.reader.tick()
            self.reader._observed.clear()
            with patch.object(self.reader,'apply_scan',side_effect=AssertionError('replayed upgrade')): self.reader.tick()
        with SessionLocal() as session:
            items=list(session.scalars(select(Record).where(Record.save_id==self.save_id,Record.kind=='game_candidate')))
            self.assertEqual([i.id for i in items],[candidate_id])
            self.assertEqual(items[0].data['payload']['traits'],['Brave'])

    def test_upgrade_still_rejects_conflicting_same_time_bytes(self):
        self.reader.configure(self.save_id,'Test.sims3',True)
        self.reader.tick()
        scan=parser.inspect_save(self.folder);scan['fingerprint']='different'
        self.reader._observed.clear()
        with patch.object(parser,'READER_REVISION',parser.READER_REVISION+1), patch.object(parser,'inspect_save',return_value=scan):
            self.reader.tick()
        self.assertIn('older or conflicting',self.reader.status(self.save_id)['status'])

    def test_traits_update_preview_existing_profile_and_supported_empty_only(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave,Record
        from app.save_scanner import compare_scan
        scan=parser.inspect_save(self.folder)
        traits={'traits':['Brave'],'trait_details':[{'name':'Brave','trait_id':'13271263770231521728'}], 'traits_scan_supported':True}
        scan['sims'][0].update(traits)
        with SessionLocal() as session:
            save=session.get(ChronicleSave,self.save_id)
            sim=Record(save_id=save.id,kind='sim',label='Alice Test',data={'game_sim_id':'123','game_traits':['Artistic'],
                'game_trait_details':[{'name':'Artistic'}],'game_career':'Artist','game_education':'University','game_skills':['Cooking (level 5)']})
            session.add(sim);session.flush()
            comparison=compare_scan(session,save,scan)
            self.assertTrue(any(d['field']=='traits' for d in comparison['rows'][0]['differences']))
            self.reader.apply_scan(session,save,scan,{'123'})
            self.assertEqual(sim.data['game_traits'],['Brave'])
            self.assertEqual(sim.data['game_trait_details'],traits['trait_details'])
            scan['sims'][0].update(traits=[],trait_details=[])
            self.reader.apply_scan(session,save,scan,{'123'})
            self.assertEqual(sim.data['game_traits'],[])
            self.assertEqual(sim.data['game_trait_details'],[])
            self.assertEqual(sim.data['game_career'],'Artist')
            self.assertEqual(sim.data['game_education'],'University')
            self.assertEqual(sim.data['game_skills'],['Cooking (level 5)'])
            self.assertEqual(save.global_day,42)

    def test_failed_import_rolls_back_and_does_not_mark_done(self):
        from app.db import SessionLocal
        from app.models import ChronicleSave
        self.reader.configure(self.save_id,'Test.sims3',True)
        def fail(session,save,*args,**kwargs):
            save.global_day=999
            session.flush()
            raise RuntimeError('test rollback')
        with patch.object(self.reader,'apply_scan',side_effect=fail): self.reader.tick()
        self.assertNotIn('last_fingerprint',self.reader.status(self.save_id))
        with SessionLocal() as session: self.assertEqual(session.get(ChronicleSave,self.save_id).global_day,42)


if __name__ == '__main__': unittest.main()
