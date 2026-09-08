"""Disposable single-save dynasty tests; no installed tracker or game is used."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app import infinite_decades as dynasty, domain, backup_service, clock, main, sync
from app.db import Base
from app.models import ChronicleSave, Record, Portrait, ClockLink, Workspace, User, Membership, BackupSnapshot


class InfiniteDecadesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread':False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.session = self.sessions()
        self.workspace = Workspace(name='Dynasty test')
        self.user = User(email='local@decades.invalid', display_name='Test')
        self.session.add_all([self.workspace,self.user]); self.session.flush()
        self.session.add(Membership(user_id=self.user.id, workspace_id=self.workspace.id))
        self.save = ChronicleSave(workspace_id=self.workspace.id,name='Cooley Dynasty',global_day=100,start_year=1300,days_per_year=4,
            settings={'game_mode':'sims4','defaults_schema_version':domain.DEFAULTS_SCHEMA_VERSION,
                      'event_catalog_version':domain.EVENT_CATALOG_VERSION,'private_token':'private','clock_game_day_high_watermark':999})
        self.session.add(self.save); self.session.flush()
        self.home = Record(save_id=self.save.id,kind='household',label='Cooley',data={'game_household_id':'77'})
        self.session.add(self.home);self.session.flush()
        self.people=[]
        for i,name in enumerate(['Ada','Ben','Cara','Dara','Outside']):
            row=Record(save_id=self.save.id,kind='sim',label=name,global_day=1,data={'birth_global_day':1,'game_sim_id':str(10+i),
                'current_household_id':self.home.id if i<4 else None})
            self.people.append(row);self.session.add(row)
        self.session.flush()
        self.people[2].data={**self.people[2].data,'mother_id':self.people[0].id,'father_id':self.people[1].id}
        self.roll=Record(save_id=self.save.id,kind='roll',label='Cara aging roll',global_day=120,data={'sim_id':self.people[2].id,'completed':False,'source_key':'age:'+self.people[2].id+':120'})
        self.session.add(self.roll)
        self.session.add(Portrait(save_id=self.save.id,record_id=self.people[2].id,stage='default',image=b'portrait',mime_type='image/png'))
        self.session.commit()

    def tearDown(self):
        self.session.close();self.engine.dispose()

    def enable(self):
        row=dynasty.enable(self.session,self.save,[s.id for s in self.people[:4]],'Main line',1400,'Main checkpoint')
        self.session.commit();return row

    def capture(self,index=2,day=104):
        self.save.global_day=day;self.session.commit()
        row=dynasty.capture(self.session,self.save,[self.people[index].id],self.people[index].label+' line','Split checkpoint')
        self.session.commit();return row

    def finish_modern(self):
        self.save.global_day=401;self.session.commit()
        dynasty.finish(self.session,self.save);self.session.commit()

    def counts(self):
        return (self.session.scalar(select(func.count()).select_from(ChronicleSave)),
                self.session.scalar(select(func.count()).select_from(Record).where(Record.kind=='sim')))

    def test_toggle_is_per_save_and_preserves_working_records(self):
        ordinary=ChronicleSave(workspace_id=self.workspace.id,name='Normal play',global_day=20,
            settings={'automation_enabled':True})
        self.session.add(ordinary);self.session.commit()
        self.assertFalse(dynasty.enabled(ordinary));self.assertFalse(dynasty.frozen(ordinary))
        self.assertTrue(dynasty.import_allowed(ordinary))
        original=copy.deepcopy(ordinary.settings)
        first=self.enable();child=self.capture()
        dynasty.confirm_game(self.session,self.save)
        candidate=Record(save_id=self.save.id,kind='game_candidate',label='Pending delivery',data={'status':'pending'})
        link=ClockLink(save_id=self.save.id,token_hash='toggle-test',enabled=True,game_anchor_day=7,tracker_anchor_day=104)
        self.session.add_all([candidate,link]);self.save.global_day=112;self.session.commit()
        people=[(r.id,r.deleted,copy.deepcopy(r.data)) for r in self.people]
        child_point=copy.deepcopy(child.data);old_epoch=dynasty.state(self.save)['epoch']
        dynasty.set_enabled(self.session,self.save,False);self.session.commit()
        self.assertFalse(dynasty.enabled(self.save));self.assertTrue(dynasty.frozen(self.save))
        self.assertFalse(domain.automation_enabled(self.save));self.assertFalse(dynasty.import_allowed(self.save))
        self.assertEqual(dynasty.state(self.save)['status'],'active')
        self.assertNotEqual(dynasty.state(self.save)['epoch'],old_epoch)
        self.assertEqual(self.save.global_day,112)
        self.assertEqual(dynasty.unpack_snapshot(first.data['snapshot'])['global_day'],112)
        self.assertEqual(people,[(r.id,r.deleted,r.data) for r in self.people]);self.assertEqual(child.data,child_point)
        self.assertFalse(candidate.deleted);self.assertEqual(candidate.data['status'],'pending')
        self.assertFalse(link.enabled);self.assertIsNone(link.game_anchor_day)
        paused_epoch=dynasty.state(self.save)['epoch']
        dynasty.set_enabled(self.session,self.save,False);self.session.commit()
        self.assertEqual(paused_epoch,dynasty.state(self.save)['epoch'])
        self.assertTrue(domain.automation_enabled(ordinary));self.assertEqual(ordinary.settings,original)
        ordinary.global_day=21;self.session.commit()
        dynasty.set_enabled(self.session,self.save,True);self.session.commit()
        self.assertTrue(dynasty.enabled(self.save));self.assertFalse(dynasty.frozen(self.save))
        self.assertEqual(self.save.global_day,112);self.assertFalse(dynasty.import_allowed(self.save))
        self.assertEqual(people,[(r.id,r.deleted,r.data) for r in self.people])
        dynasty.confirm_game(self.session,self.save);self.session.commit()
        ready=copy.deepcopy(dynasty.state(self.save))
        dynasty.set_enabled(self.session,self.save,True);self.session.commit()
        self.assertEqual(dynasty.state(self.save),ready);self.assertTrue(dynasty.import_allowed(self.save))
        self.assertEqual(ordinary.global_day,21)

    def test_paused_toggle_blocks_writes_imports_and_branch_actions(self):
        self.enable();self.capture()
        dynasty.set_enabled(self.session,self.save,False);self.session.commit()
        actions=[lambda:dynasty.activate_next(self.session,self.save),
            lambda:dynasty.capture(self.session,self.save,[self.people[1].id],'Ben','checkpoint'),
            lambda:dynasty.capture_starting(self.session,self.save,[self.people[4].id],'Outside','checkpoint'),
            lambda:dynasty.finish(self.session,self.save),
            lambda:dynasty.confirm_game(self.session,self.save),
            lambda:dynasty.filter_scan(self.session,self.save,{'sims':[]})]
        for action in actions:
            with self.assertRaises(ValueError):action()
        self.people[0].label='An accidental edit'
        with self.assertRaises(dynasty.BranchFrozenError):self.session.flush()
        self.session.rollback();self.assertEqual(self.people[0].label,'Ada')
        with patch.object(main,'SessionLocal',self.sessions):
            client=TestClient(main.app)
            client.post('/saves/select',data={'save_id':self.save.id})
            page=client.get('/p/infinite-decades')
            self.assertEqual(page.status_code,200,page.text[:400]);self.assertIn('This dynasty is paused',page.text)
            self.assertNotIn('Capture a new split',page.text);self.assertNotIn('name="confirmed"',page.text)
            result=client.post('/api/saves/'+self.save.id+'/advance',data={'days':'7'},
                headers={'X-Dynasty-Epoch':dynasty.state(self.save)['epoch']})
            self.assertEqual(result.status_code,409)
            client.close()

    def test_paused_toggle_round_trips_and_legacy_defaults_on(self):
        self.enable();self.capture()
        with dynasty.branch_operation(self.session,self.save):
            meta={k:v for k,v in dynasty.state(self.save).items() if k!='enabled'}
            self.save.settings={**self.save.settings,dynasty.KEY:meta}
        self.session.commit();self.assertTrue(dynasty.enabled(self.save))
        dynasty.set_enabled(self.session,self.save,False);self.session.commit()
        restored=backup_service.restore_as_copy(self.session,self.workspace.id,backup_service.build_package(self.session,self.save))
        self.session.commit()
        self.assertFalse(dynasty.enabled(restored));self.assertTrue(dynasty.frozen(restored))
        self.assertEqual(len(dynasty.branches(self.session,restored)),3)
        dynasty.set_enabled(self.session,restored,True);self.session.commit()
        self.assertTrue(dynasty.enabled(restored));self.assertFalse(dynasty.enabled(self.save))
        self.assertEqual(restored.global_day,self.save.global_day)

    def test_http_toggle_normal_save_requires_setup_and_valid_choice(self):
        with patch.object(main,'SessionLocal',self.sessions):
            client=TestClient(main.app)
            client.post('/saves/select',data={'save_id':self.save.id})
            page=client.get('/p/saves')
            self.assertEqual(page.status_code,200,page.text[:400])
            self.assertIn('role="switch" aria-checked="false"',page.text)
            self.session.expire_all()
            original=copy.deepcopy(self.save.settings)
            response=client.post('/infinite/'+self.save.id+'/toggle',data={'enabled':'false'},follow_redirects=False)
            self.assertEqual(response.status_code,303);self.session.expire_all()
            self.assertEqual(self.save.settings,original)
            response=client.post('/infinite/'+self.save.id+'/toggle',data={'enabled':'true'},follow_redirects=False)
            self.assertEqual(response.status_code,303);self.assertEqual(response.headers['location'],'/p/infinite-decades')
            self.session.expire_all();self.assertFalse(dynasty.state(self.save));self.assertEqual(self.counts(),(1,5))
            for value in ('yes','invalid',''):
                response=client.post('/infinite/'+self.save.id+'/toggle',data={'enabled':value})
                self.assertIn(response.status_code,(400,422))
            response=client.post('/infinite/'+self.save.id+'/toggle',data={'enabled':'false','return_to':'https://example.invalid'},follow_redirects=False)
            self.assertEqual(response.headers['location'],'/p/saves')
            client.close()

    def test_http_toggle_other_save_uses_its_own_epoch_and_ownership(self):
        self.enable()
        other=backup_service.restore_as_copy(self.session,self.workspace.id,backup_service.build_package(self.session,self.save))
        other.name='Second dynasty';self.session.commit()
        foreign_workspace=Workspace(name='Someone else');self.session.add(foreign_workspace);self.session.flush()
        foreign=ChronicleSave(workspace_id=foreign_workspace.id,name='Private save')
        self.session.add(foreign);self.session.commit()
        epoch=dynasty.state(other)['epoch'];selected_epoch=dynasty.state(self.save)['epoch']
        with patch.object(main,'SessionLocal',self.sessions):
            client=TestClient(main.app)
            client.post('/saves/select',data={'save_id':self.save.id})
            page=client.get('/p/saves')
            self.assertEqual(page.status_code,200,page.text[:400])
            self.assertIn('data-dynasty-epoch="'+epoch+'"',page.text)
            self.assertIn('/infinite/'+other.id+'/toggle?_dynasty_epoch='+epoch,page.text)
            response=client.post('/infinite/'+other.id+'/toggle',data={'enabled':'false'},headers={'X-Dynasty-Epoch':selected_epoch})
            self.assertEqual(response.status_code,409)
            response=client.post('/infinite/'+other.id+'/toggle?_dynasty_epoch='+epoch,data={'enabled':'false'},headers={'X-Dynasty-Epoch':selected_epoch})
            self.assertEqual(response.status_code,200,response.text[:400]);self.session.expire_all()
            self.assertTrue(dynasty.enabled(self.save));self.assertFalse(dynasty.enabled(other))
            page=client.get('/p/infinite-decades')
            self.assertIn('Play current branch',page.text);self.assertIn('Cooley Dynasty',page.text)
            self.assertNotIn('This dynasty is paused.',page.text)
            response=client.post('/infinite/'+foreign.id+'/toggle',data={'enabled':'true'})
            self.assertEqual(response.status_code,404)
            response=client.post('/infinite/'+other.id+'/toggle?_dynasty_epoch='+dynasty.state(other)['epoch'],data={'enabled':'true'})
            self.assertEqual(response.status_code,200,response.text[:400]);self.session.expire_all()
            self.assertTrue(dynasty.enabled(other));self.assertTrue(dynasty.enabled(self.save))
            client.close()

    def test_toggle_keeps_completed_branch_completed(self):
        self.enable();self.capture();self.finish_modern()
        before=copy.deepcopy(dynasty.active_branch(self.session,self.save).data)
        dynasty.set_enabled(self.session,self.save,False);self.session.commit()
        with self.assertRaises(ValueError):dynasty.activate_next(self.session,self.save)
        dynasty.set_enabled(self.session,self.save,True);self.session.commit()
        self.assertEqual(dynasty.state(self.save)['status'],'modern');self.assertTrue(dynasty.frozen(self.save))
        self.assertEqual(dynasty.active_branch(self.session,self.save).data,before)
        dynasty.activate_next(self.session,self.save);self.session.commit();self.assertEqual(self.save.global_day,104)

    def test_enable_keeps_one_save_and_all_identities(self):
        save_id=self.save.id;ids=[r.id for r in self.people]
        first=self.enable()
        self.assertEqual(self.counts(),(1,5));self.assertEqual(self.save.id,save_id)
        self.assertEqual([r.id for r in self.people],ids)
        self.assertEqual(self.people[2].data['mother_id'],self.people[0].id)
        self.assertFalse(self.home.deleted);self.assertTrue(self.people[4].deleted)
        self.assertFalse(dynasty.frozen(self.save));self.assertFalse(dynasty.import_allowed(self.save))
        self.assertEqual(dynasty.state(self.save)['active_branch_id'],first.id)
        self.assertNotIn('clock_game_day_high_watermark',self.save.settings)
        self.assertEqual(self.save.settings['private_token'],'private')
        self.assertEqual(self.session.scalar(select(Portrait)).record_id,self.people[2].id)
        self.assertIn(dynasty.KIND,sync.SYNC_KINDS)

    def test_split_freezes_sim_and_roll_without_moving_day(self):
        self.enable();child=self.capture()
        self.assertEqual(self.counts(),(1,5));self.assertEqual(self.save.global_day,104)
        self.assertTrue(self.people[2].deleted);self.assertTrue(self.roll.deleted)
        point=dynasty.unpack_snapshot(child.data['snapshot'])
        self.assertEqual(point['global_day'],104)
        self.assertEqual(point['member_sim_ids'],[self.people[2].id])
        self.assertEqual(next(r for r in point['records'] if r['kind']=='roll')['id'],self.roll.id)
        before=copy.deepcopy(self.people[2].data)
        self.save.global_day=300;self.session.commit()
        self.assertEqual(before,self.people[2].data)
        self.assertEqual(dynasty.unpack_snapshot(child.data['snapshot']),point)

    def test_latest_split_first_and_completed_outcomes_retained(self):
        first=self.enable();cara=self.capture();dara=self.capture(3,108)
        with self.assertRaisesRegex(ValueError,'Finish the active'): dynasty.activate_next(self.session,self.save)
        story=Record(save_id=self.save.id,kind='story_entry',label='Main line ending',global_day=400,data={'sim_id':self.people[0].id,'outcome':'Survived to modern day'})
        self.session.add(story);self.session.commit();self.finish_modern()
        before=copy.deepcopy(first.data)
        target=dynasty.activate_next(self.session,self.save);self.session.commit()
        self.assertEqual(target.id,dara.id);self.assertEqual(self.save.global_day,108)
        self.assertEqual(first.data,before);self.assertTrue(story.deleted)
        self.assertEqual(self.counts(),(1,5));self.assertFalse(self.people[3].deleted);self.assertTrue(self.people[0].deleted)
        self.people[3].data={**self.people[3].data,'death_confirmed':True,'death_global_day':108};self.session.commit()
        self.assertEqual(dynasty.finish(self.session,self.save),'extinct');self.session.commit()
        self.assertEqual(dynasty.activate_next(self.session,self.save).id,cara.id);self.session.commit()
        self.assertEqual(self.save.global_day,104);self.assertFalse(self.roll.deleted);self.assertFalse(self.roll.data['completed'])
        self.assertTrue(self.people[3].data['death_confirmed'])
        self.assertIn('Survived to modern day',str(dynasty.unpack_snapshot(first.data['snapshot'])))

    def test_new_birth_belongs_to_active_line_and_remains_in_dynasty(self):
        self.enable();self.capture()
        baby=Record(save_id=self.save.id,kind='sim',label='New child',global_day=105,data={'birth_global_day':105,'mother_id':self.people[0].id})
        self.session.add(baby);self.session.commit()
        self.assertEqual(baby.data['infinite_branch_id'],dynasty.state(self.save)['active_branch_id'])
        self.finish_modern();dynasty.activate_next(self.session,self.save);self.session.commit()
        self.assertTrue(baby.deleted);self.assertEqual(baby.data['mother_id'],self.people[0].id)
        self.assertEqual(self.counts(),(1,6))

    def test_starting_branch_uses_original_snapshot_without_reusing_people(self):
        self.enable();self.save.global_day=230;self.session.commit()
        child=dynasty.capture_starting(self.session,self.save,[self.people[4].id],'Outside line','Original world');self.session.commit()
        self.assertEqual(self.save.global_day,230);self.assertEqual(dynasty.metadata(child)['split_global_day'],100)
        with self.assertRaises(ValueError): dynasty.capture_starting(self.session,self.save,[self.people[0].id],'Duplicate','world')
        with self.assertRaises(ValueError): dynasty.capture_starting(self.session,self.save,[self.people[4].id],'Duplicate','world')

    def test_reject_invalid_finish_departure_and_reenable(self):
        self.enable()
        with self.assertRaisesRegex(ValueError,'living Sims'): dynasty.finish(self.session,self.save)
        with self.assertRaisesRegex(ValueError,'Leave a living'): dynasty.capture(self.session,self.save,[r.id for r in self.people[:4]],'Empty parent','checkpoint')
        with self.assertRaises(ValueError): dynasty.capture(self.session,self.save,['foreign'],'foreign','checkpoint')
        with self.assertRaisesRegex(ValueError,'already enabled'): self.enable()

    def test_frozen_edits_and_hidden_branch_mutation_are_blocked(self):
        first=self.enable();self.capture()
        self.people[2].deleted=False
        with self.assertRaises(dynasty.BranchFrozenError): self.session.flush()
        self.session.rollback();self.assertTrue(self.people[2].deleted)
        first.label='Changed elsewhere'
        with self.assertRaises(dynasty.BranchFrozenError): self.session.flush()
        self.session.rollback();self.assertEqual(first.label,'Main line')
        self.save.settings={k:v for k,v in self.save.settings.items() if k!=dynasty.KEY}
        with self.assertRaises(dynasty.BranchFrozenError):self.session.flush()
        self.session.rollback();self.assertTrue(dynasty.state(self.save))
        self.save.global_day=1
        with self.assertRaises(dynasty.BranchFrozenError):self.session.flush()
        self.session.rollback()

    def test_clock_filters_after_checksum_validation(self):
        self.enable()
        link=ClockLink(save_id=self.save.id,token_hash='fake',enabled=True)
        self.session.add(link);self.session.commit()
        result=clock.receive(self.session,link,{'game_day':500})
        self.assertEqual(result['status'],'paused');self.assertIsNone(link.last_game_day)
        dynasty.confirm_game(self.session,self.save);self.session.commit()
        self.save.settings={**self.save.settings,'automation_enabled':False};self.session.commit()
        report={'protocol_version':2,'report_sequence':1,'game_day':500,'game_edition':'sims4','save_identity':'checkpoint',
                'household_sims':[{'game_sim_id':'10','name':'Ada','household_id':'77'},{'game_sim_id':'14','name':'Outside','household_id':'77'}]}
        report['report_checksum']=clock.report_checksum(report)
        original=copy.deepcopy(report)
        result=clock.receive(self.session,link,report);self.session.commit()
        self.assertTrue(result['ok']);self.assertEqual(result['report_sequence'],1)
        self.assertEqual(report,original)
        self.assertTrue(clock.receive(self.session,link,report)['duplicate'])
        bad={**report,'game_day':999}
        self.assertEqual(clock.receive(self.session,link,bad)['reason'],'checksum_mismatch')
        filtered=dynasty.filter_members(self.session,self.save,report['household_sims']+[{'game_sim_id':'99','name':'Baby','parent_game_sim_ids':['10']}])
        self.assertEqual([r['game_sim_id'] for r in filtered],['10','99'])

    def test_scan_scope_preserves_household_and_blocks_other_branch(self):
        self.enable();dynasty.confirm_game(self.session,self.save);self.session.commit()
        scan={'sims':[{'game_sim_id':'10','game_household_id':'77'},{'game_sim_id':'14','game_household_id':'77'}],
              'households':[{'game_household_id':'77','member_game_ids':['10','14']}],'population_complete':True}
        filtered=dynasty.filter_scan(self.session,self.save,scan)
        self.assertEqual(filtered['households'][0]['member_game_ids'],['10']);self.assertFalse(filtered['population_complete'])
        self.assertEqual(len(scan['sims']),2)

    def test_export_restore_contains_whole_dynasty_with_remapped_ids(self):
        self.enable();self.capture();self.finish_modern()
        package=backup_service.build_package(self.session,self.save)
        restored=backup_service.restore_as_copy(self.session,self.workspace.id,package);self.session.commit()
        self.assertEqual(len(dynasty.branches(self.session,restored)),3)
        self.assertNotEqual(restored.id,self.save.id)
        members=list(self.session.scalars(select(Record).where(Record.save_id==restored.id,Record.kind=='sim')))
        restored_ids={r.id for r in members}
        self.assertFalse(restored_ids.intersection(r.id for r in self.people))
        for row in dynasty.branches(self.session,restored):
            point=dynasty.unpack_snapshot(row.data['snapshot'])
            self.assertTrue(set(point['member_sim_ids'])<=restored_ids)
        self.assertFalse(dynasty.import_allowed(restored))
        dynasty.activate_next(self.session,restored);self.session.commit()
        self.assertEqual(restored.global_day,104);self.assertEqual(self.save.global_day,401)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Portrait).where(Portrait.save_id==restored.id)),1)

    def test_corrupt_checkpoint_and_failed_capture_roll_back(self):
        self.enable();before=len(dynasty.branches(self.session,self.save))
        with patch('app.infinite_dynasty.pack_snapshot',side_effect=RuntimeError('checkpoint failure')):
            with self.assertRaises(RuntimeError):self.capture()
        self.session.rollback();self.assertEqual(len(dynasty.branches(self.session,self.save)),before)
        self.assertFalse(self.people[2].deleted)
        child=self.capture();self.finish_modern()
        broken=copy.deepcopy(child.data);broken['snapshot']['sha256']='invalid'
        with dynasty.branch_operation(self.session,self.save): child.data=broken
        self.session.commit()
        with self.assertRaisesRegex(ValueError,'checkpoint is invalid'): dynasty.activate_next(self.session,self.save)
        self.session.rollback();self.assertEqual(self.save.global_day,401)

    def test_starting_backup_not_pruned(self):
        self.enable()
        point=self.session.scalar(select(BackupSnapshot).where(BackupSnapshot.reason=='infinite:before-enable'))
        for i in range(16):backup_service.create_snapshot(self.session,self.save,'ordinary',force=True)
        self.session.commit();self.assertIsNotNone(self.session.get(BackupSnapshot,point.id))

    def test_routes_shared_tree_profile_history_and_stale_tab(self):
        self.enable();child=self.capture()
        with patch.object(main,'SessionLocal',self.sessions):
            client=TestClient(main.app)
            client.post('/saves/select',data={'save_id':self.save.id})
            page=client.get('/p/infinite-decades')
            self.assertEqual(page.status_code,200,page.text[:400]);self.assertIn('one tracker save',page.text)
            self.assertIn('Capture a new split',page.text)
            for url in ('/p/today','/p/sims'):
                view=client.get(url)
                self.assertEqual(view.status_code,200,view.text[:400])
            tree=client.get('/p/family-tree?focus='+self.people[2].id)
            self.assertEqual(tree.status_code,200,tree.text[:400]);self.assertIn('Ada',tree.text);self.assertIn('Frozen',tree.text)
            profile=client.get('/sims/'+self.people[2].id)
            self.assertEqual(profile.status_code,200);self.assertIn('READ-ONLY DYNASTY PROFILE',profile.text)
            history=client.get('/p/infinite-decades?branch_id='+child.id)
            self.assertEqual(history.status_code,200);self.assertIn('Cara aging roll',history.text)
            response=client.post('/api/saves/'+self.save.id+'/advance',data={'days':7},headers={'X-Dynasty-Epoch':'old-tab'})
            self.assertEqual(response.status_code,409)
            response=client.post('/infinite/'+self.save.id+'/finish',headers={'X-Dynasty-Epoch':dynasty.state(self.save)['epoch']})
            self.assertEqual(response.status_code,409)
            client.close()

    def test_partial_cloud_sync_pauses_before_network_or_record_writes(self):
        from app import sync_client
        self.enable()
        with patch.object(sync_client,'SessionLocal',self.sessions), patch.object(sync_client,'load_config',return_value={
                'token':'fake','local_save_id':self.save.id,'remote_url':'https://example.invalid'}), patch.object(sync_client.httpx,'post') as network:
            self.assertEqual(sync_client.cycle()['status'],'paused')
            network.assert_not_called()
        with self.assertRaises(dynasty.BranchFrozenError):
            sync.apply_change(self.session,self.save,SimpleNamespace(id='fake'),{'kind':'sim','payload':{'data':{}}})

    def test_http_enable_capture_and_duplicate_keep_one_dynasty_per_save(self):
        with patch.object(main,'SessionLocal',self.sessions):
            client=TestClient(main.app)
            client.post('/saves/select',data={'save_id':self.save.id})
            response=client.post('/infinite/'+self.save.id+'/enable',data={
                'sim_ids':[r.id for r in self.people[:4]],'label':'HTTP main','modern_year':'1400',
                'game_save_name':'Main game checkpoint','checkpoint_confirmed':'yes'})
            self.assertEqual(response.status_code,200,response.text[:400]);self.session.expire_all()
            self.assertEqual(self.counts(),(1,5))
            response=client.post('/infinite/'+self.save.id+'/capture',data={
                'sim_ids':[self.people[2].id],'label':'HTTP child','game_save_name':'Child checkpoint','checkpoint_confirmed':'yes'},
                headers={'X-Dynasty-Epoch':dynasty.state(self.save)['epoch']})
            self.assertEqual(response.status_code,200,response.text[:400]);self.session.expire_all()
            self.assertEqual(self.counts(),(1,5))
            self.assertEqual(len(dynasty.branches(self.session,self.save)),3)
            response=client.post('/saves/'+self.save.id+'/duplicate',data={'name':'Independent dynasty'})
            self.assertEqual(response.status_code,200,response.text[:400]);self.session.expire_all()
            self.assertEqual(self.counts(),(2,10))
            copied=self.session.scalar(select(ChronicleSave).where(ChronicleSave.name=='Independent dynasty'))
            self.assertEqual(len(dynasty.branches(self.session,copied)),3)
            self.assertFalse(dynasty.import_allowed(copied))
            client.close()


if __name__=='__main__':unittest.main()
