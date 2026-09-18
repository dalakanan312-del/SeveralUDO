"""Audit reproductions in disposable databases. Never touch installed saves."""
import copy
import io
import json
import unittest
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import Text, cast, select
from app import backup_service as backups, dynasty_history as history, dynasty_tools as tools
from app import infinite_dynasty as dynasty, sync, sync_client, main, advanced, historical_life, usability
from app.models import ChronicleSave, Record, Device, Change, BackupSnapshot
from app.change_storage import encode, decode, compact_batch, MARKER
from app.public_settings import public_settings
from tests import test_infinite_decades, test_dynasty_history


class SaveIsolationTests(unittest.TestCase):
    def setUp(self):
        self.f=test_infinite_decades.InfiniteDecadesTests();self.f.setUp();self.s=self.f.session
    def tearDown(self): self.f.tearDown()

    def other(self):
        row=ChronicleSave(workspace_id=self.f.workspace.id,name='Other',settings={})
        self.s.add(row);self.s.commit();return row

    def test_duplicate_change_cannot_disclose_another_save(self):
        other=self.other();person=self.f.people[0]
        device=Device(save_id=other.id,name='Other device',token_hash='test-device')
        change=Change(save_id=self.f.save.id,device_id='local',record_id=person.id,kind='sim',operation='upsert',new_version=1,payload=sync.serialize(person))
        self.s.add_all([device,change]);self.s.commit()
        with self.assertRaises(ValueError):sync.apply_change(self.s,other,device,{'kind':'sim','change_id':change.id,'record_id':person.id})

    def test_duplicate_same_save_is_idempotent(self):
        device=Device(save_id=self.f.save.id,name='Device',token_hash='same-save');self.s.add(device);self.s.commit()
        person=self.f.people[0]
        body={'kind':'sim','change_id':'a'*32,'record_id':person.id,'base_version':person.version,'payload':sync.serialize(person)}
        self.assertEqual(sync.apply_change(self.s,self.f.save,device,body)['status'],'applied')
        self.assertEqual(sync.apply_change(self.s,self.f.save,device,body)['status'],'duplicate')

    def test_pull_checks_ownership_before_version_and_does_not_advance_cursor(self):
        other=self.other();person=self.f.people[0];before=copy.deepcopy(person.data)
        config={'local_save_id':other.id,'remote_save_id':'remote','remote_url':'https://audit.invalid','token':'dummy'}
        response=Mock();response.json.return_value={'changes':[{'record_id':person.id,'kind':'sim','new_version':0,'device_id':'remote','payload':{'label':'Wrong','data':{}}}],'cursor':100}
        sink=Mock()
        with patch.object(sync_client,'SessionLocal',self.f.sessions),patch.object(sync_client,'load_config',return_value=config),patch.object(sync_client.httpx,'post',return_value=response),patch.object(sync_client,'CONFIG',sink):
            with self.assertRaises(ValueError):sync_client.cycle()
        self.s.expire_all();self.assertEqual(person.data,before);sink.write_text.assert_not_called()

    def test_wrong_remote_save_rejected(self):
        config={'local_save_id':self.f.save.id,'remote_save_id':'expected','remote_url':'https://audit.invalid','token':'dummy'}
        response=Mock();response.json.return_value={'save_id':'wrong','changes':[]}
        with patch.object(sync_client,'SessionLocal',self.f.sessions),patch.object(sync_client,'load_config',return_value=config),patch.object(sync_client.httpx,'post',return_value=response):
            with self.assertRaises(ValueError):sync_client.cycle()

    def test_cross_site_browser_cannot_pause_automation(self):
        @contextmanager
        def db():
            with self.f.sessions() as s:yield s;s.commit()
        with patch.object(main,'db',db):
            response=TestClient(main.app).post('/api/automation/toggle',data={'enabled':''},headers={'Origin':'https://untrusted.example','Sec-Fetch-Site':'cross-site'},follow_redirects=False)
        self.assertEqual(response.status_code,403)
        self.s.expire_all();self.assertNotEqual(self.f.save.settings.get('automation_enabled'),False)

    def test_confirmed_death_agrees_across_active_views(self):
        person=self.f.people[0];person.data={'birth_global_day':1,'death_confirmed':True}
        self.assertFalse(main._living_sim(person,self.f.save))
        self.assertFalse(advanced.living(person,self.f.save.global_day))
        self.assertFalse(dynasty.alive(person,self.f.save))

    def test_future_death_not_yet_confirmed_is_living(self):
        person=self.f.people[0];person.data={'birth_global_day':1,'death_global_day':200}
        self.assertTrue(main._living_sim(person,self.f.save));self.assertTrue(advanced.living(person,100))

    def test_historical_census_keeps_someone_who_died_later(self):
        from app.record_state import living
        person=self.f.people[0];person.data={'birth_global_day':1,'death_global_day':80,'death_confirmed':True}
        self.assertFalse(living(person,100));self.assertTrue(living(person,40,historical=True))

    def test_same_origin_form_still_works(self):
        @contextmanager
        def db():
            with self.f.sessions() as s:yield s;s.commit()
        with patch.object(main,'db',db):
            response=TestClient(main.app).post('/api/automation/toggle',data={'enabled':''},headers={'Origin':'http://testserver','Sec-Fetch-Site':'same-origin'},follow_redirects=False)
        self.assertEqual(response.status_code,303)

    def test_token_header_does_not_bypass_browser_guard_on_settings(self):
        response=TestClient(main.app).post('/api/sync/configure-local',headers={'Origin':'https://untrusted.example','Authorization':'Bearer fake'})
        self.assertEqual(response.status_code,403)

    def test_report_endpoint_still_requires_its_real_token(self):
        @contextmanager
        def db():
            with self.f.sessions() as s:yield s;s.commit()
        with patch.object(main,'db',db):
            response=TestClient(main.app).post('/api/sync/push',json={'changes':[]},headers={'Origin':'https://relay.example','Authorization':'Bearer invalid'})
        self.assertEqual(response.status_code,401)

    def test_compressed_history_round_trips_without_wire_format_change(self):
        payload={'id':self.f.people[0].id,'data':{'traits':['Trait name']*5000},'label':'Names'}
        change=Change(save_id=self.f.save.id,device_id='local',record_id=self.f.people[0].id,kind='sim',operation='upsert',new_version=1,payload=payload)
        self.s.add(change);self.s.commit();self.s.expire_all()
        self.assertEqual(change.payload,payload)
        raw=self.s.scalar(select(cast(Change.payload,Text)).where(Change.sequence==change.sequence))
        self.assertIn(MARKER,raw)
        self.assertEqual(sync.pull(self.s,self.f.save.id,0)['changes'][0]['payload'],payload)

    def test_batch_compaction_keeps_all_sequences_and_values(self):
        from sqlalchemy import text
        payload={'data':{'notes':'a'*12000}}
        change=Change(save_id=self.f.save.id,device_id='local',record_id=self.f.people[0].id,kind='sim',operation='upsert',new_version=1,payload={})
        self.s.add(change);self.s.commit()
        self.s.execute(text('UPDATE changes SET payload=:payload WHERE sequence=:seq'),{'payload':json.dumps(payload),'seq':change.sequence});self.s.commit()
        result=compact_batch(self.s,self.f.save.id);self.s.commit();self.s.expire_all()
        self.assertEqual(result['compressed'],1);self.assertEqual(change.payload,payload)
        self.assertEqual(compact_batch(self.s,self.f.save.id)['compressed'],0)

    def test_backup_package_still_restores(self):
        original=len(list(self.s.scalars(select(Record).where(Record.save_id==self.f.save.id))))
        restored=backups.restore_as_copy(self.s,self.f.workspace.id,backups.build_package(self.s,self.f.save))
        self.assertEqual(len(list(self.s.scalars(select(Record).where(Record.save_id==restored.id)))),original)

    def test_retention_bounds_dynasty_copies_but_preserves_original(self):
        from datetime import datetime,timezone,timedelta
        initial=BackupSnapshot(save_id=self.f.save.id,reason='infinite:before-enable',revision=0,sha256='test',size_bytes=1,package=b'x')
        self.s.add(initial)
        for i in range(20):
            self.s.add(BackupSnapshot(save_id=self.f.save.id,reason='infinite:test',revision=0,sha256='test',size_bytes=1,package=b'x',created_at=datetime.now(timezone.utc)-timedelta(days=i+1)))
        self.s.commit();backups.create_snapshot(self.s,self.f.save,'infinite:manual-test',force=True);self.s.commit()
        rows=list(self.s.scalars(select(BackupSnapshot)))
        self.assertEqual(len(rows),15);self.assertIn(initial.id,[row.id for row in rows])


class DynastyRestoreTests(unittest.TestCase):
    def setUp(self):self.f=test_dynasty_history.DynastyHistoryTests();self.f.setUp()
    def tearDown(self):self.f.tearDown()

    def test_pending_transfer_restores_and_receives_independently(self):
        f=self.f;parcel=f.send_person();original=f.f.people[3];before=copy.deepcopy(original.data)
        clone=backups.restore_as_copy(f.s,f.save.workspace_id,backups.build_package(f.s,f.save));f.s.commit()
        copied=history.logs(f.s,clone,'parcel')[0]
        self.assertNotEqual(copied.data['sim_ids'],parcel.data['sim_ids'])
        self.assertEqual(set(dynasty.unpack_snapshot(copied.data['payload'])['member_sim_ids']),set(copied.data['sim_ids']))
        ticket=tools.prepare(f.s,clone,f.f.user.id,'switch',{'branch_id':copied.data['branch_id']});tools.confirm(f.s,clone,ticket);f.s.commit()
        clone.global_day=110;f.s.commit()
        ticket=tools.prepare(f.s,clone,f.f.user.id,'history_receive',{'row_id':copied.id});tools.confirm(f.s,clone,ticket);f.s.commit()
        self.assertEqual(original.data,before)
        self.assertFalse(f.s.get(Record,copied.data['sim_ids'][0]).data.get('infinite_frozen'))

    def test_tampered_transfer_rejected_before_any_change(self):
        f=self.f;parcel=f.send_person();data=copy.deepcopy(parcel.data);data['sim_ids']=['wrong']
        with self.assertRaises(ValueError):history.validated_delivery(f.s,f.save,data)

    def test_heirloom_views_agree_and_rewind_by_date(self):
        f=self.f;item=Record(save_id=f.save.id,kind='heirloom',label='Ring',data={'current_holder_sim_id':f.f.people[0].id});f.s.add(item);f.s.commit()
        f.apply('heirloom',heirloom_id=item.id,branch_id=f.root.id,sim_id=f.f.people[0].id,day=104)
        ledger=history.logs(f.s,f.save,'heirloom_history')[0]
        f.save.global_day=110;f.s.commit();f.apply('heirloom',row_id=ledger.id,branch_id=f.root.id,sim_id=f.f.people[1].id,day=110)
        self.assertEqual(item.data['current_holder_sim_id'],f.f.people[1].id)
        grouped={'heirloom':[item],'sim':f.f.people,'dynasty_tool':[ledger]}
        self.assertEqual(historical_life.heirloom_summary(grouped,f.save)['tracked'][0]['holder'].id,f.f.people[1].id)
        f.save.global_day=104
        self.assertEqual(historical_life.heirloom_summary(grouped,f.save)['tracked'][0]['holder'].id,f.f.people[0].id)


class FormatSafetyTests(unittest.TestCase):
    def test_nested_private_settings_are_removed(self):
        original={'name':'Keep','extension':{'api_key':'private','name':'Nested'},'clock_recovery':{},'list':[{'password':'private','count':2}]}
        expected={'name':'Keep','extension':{'name':'Nested'},'list':[{'count':2}]}
        self.assertEqual(public_settings(original),expected)
        self.assertEqual(backups.public_settings(original),sync._public_settings(original))
        self.assertEqual(main.public_save_settings(original),expected)

    def test_compression_integrity_and_legacy(self):
        value={'data':{'text':'abc'*10000}}
        self.assertEqual(decode(encode(value)),value);self.assertEqual(decode({'data':1}),{'data':1})
        packed=encode(value);packed['sha256']='wrong'
        with self.assertRaises(ValueError):decode(packed)

    def test_unpacked_package_size_limited(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',json.dumps({'format':'decades-save-v4'}));z.writestr('records.json',json.dumps([{'id':'a','data':{'padding':'x'*200000}}]))
        with patch.object(backups,'MAX_UNPACKED_BYTES',10000):
            with self.assertRaises(ValueError):backups.inspect_package(stream.getvalue())

    def test_hosted_startup_requires_private_secret(self):
        with patch.object(main,'settings',SimpleNamespace(local_mode=False,session_secret='development-only-change-me')):
            with self.assertRaises(RuntimeError):main.startup()

    def test_every_existing_page_remains_in_navigation_once(self):
        save=SimpleNamespace(settings={'selected_rule_packs':list(usability.OPTIONAL_PAGES.values())})
        groups=usability.visible_navigation(save);pages=[p for g in groups for p in g['pages']]
        self.assertEqual(set(pages),set(main.FEATURES));self.assertEqual(len(pages),len(set(pages)))
        self.assertLess(sum(len(g['primary_pages']) for g in groups),len(pages))
