import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from app.db import Base, ensure_local_query_indexes
from app.models import ChronicleSave, Record, Workspace
from app import automation, main
from desktop_launcher import relay_heartbeat_fresh


class ClockPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        workspace = Workspace(name='Clock performance fixture')
        self.session.add(workspace); self.session.flush()
        self.save = ChronicleSave(workspace_id=workspace.id, name='Main')
        self.other = ChronicleSave(workspace_id=workspace.id, name='Other')
        self.session.add_all([self.save, self.other]); self.session.flush()
        self.parent = Record(save_id=self.save.id, kind='sim', label='Parent', data={'game_sim_id':'10'})
        self.imported = Record(save_id=self.save.id, kind='sim', label='Imported', data={})
        self.foreign = Record(save_id=self.other.id, kind='sim', label='Other save', data={'game_sim_id':'10'})
        self.session.add_all([self.parent, self.imported, self.foreign]); self.session.commit()
        self.link = SimpleNamespace(save_id=self.save.id)

    def tearDown(self):
        self.session.close(); self.engine.dispose()

    def test_repeated_lookups_load_sims_once_and_cache_misses(self):
        queries = []
        def count(conn, cursor, statement, parameters, context, many):
            if statement.startswith('SELECT records.'): queries.append(statement)
        event.listen(self.engine, 'before_cursor_execute', count)
        @automation.with_game_sim_lookup
        def receive(session, link, report):
            for _ in range(100):
                self.assertIs(automation._game_sim(session, self.save, '10'), self.parent)
                self.assertIsNone(automation._game_sim(session, self.save, 'missing'))
        receive(self.session, self.link, {})
        self.assertEqual(len(queries), 1)
        self.assertNotIn('clock_sim_lookup', self.session.info)

    def test_existing_database_gets_missing_index_without_changing_records(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql('DROP INDEX ix_records_save_kind_deleted_day')
            before = connection.exec_driver_sql('SELECT id, data, version FROM records ORDER BY id').all()
        self.assertEqual(ensure_local_query_indexes(self.engine), 1)
        self.assertEqual(ensure_local_query_indexes(self.engine), 0)
        with self.engine.connect() as connection:
            after = connection.exec_driver_sql('SELECT id, data, version FROM records ORDER BY id').all()
            plan = connection.exec_driver_sql(
                'EXPLAIN QUERY PLAN SELECT id FROM records WHERE save_id=? AND kind=? AND deleted=0',
                (self.save.id, 'sim')).all()
        self.assertEqual(before, after)
        self.assertIn('ix_records_save_kind_deleted_day', str(plan))

    def test_local_index_upgrade_does_not_mutate_hosted_database(self):
        bind = MagicMock(); bind.dialect.name = 'postgresql'
        self.assertEqual(ensure_local_query_indexes(bind), 0)
        bind.begin.assert_not_called()

    def test_seeded_lookup_sees_imported_identity_and_deletion(self):
        @automation.with_game_sim_lookup
        def receive(session, link, report):
            automation.cache_tracked_sims(session, self.save, [self.parent, self.imported])
            self.assertIsNone(automation._game_sim(session, self.save, '20'))
            self.imported.data = {'game_sim_id':'20'}
            session.flush()
            self.assertIs(automation._game_sim(session, self.save, '20'), self.imported)
            self.parent.deleted = True
            self.assertIsNone(automation._game_sim(session, self.save, '10'))
        receive(self.session, self.link, {})

    def test_lookup_does_not_leak_into_other_save_or_next_report(self):
        @automation.with_game_sim_lookup
        def receive(session, link, report):
            self.assertIs(automation._game_sim(session, self.save, '10'), self.parent)
            self.assertIs(automation._game_sim(session, self.other, '10'), self.foreign)
        receive(self.session, self.link, {})
        self.assertNotIn('clock_sim_lookup', self.session.info)
        self.parent.data = {'game_sim_id':'11'}; self.session.commit()
        self.assertIsNone(automation._game_sim(self.session, self.save, '10'))
        self.assertIs(automation._game_sim(self.session, self.save, '11'), self.parent)

    def test_lookup_restores_outer_context_after_exception(self):
        prior = {'save_id':self.other.id, 'rows':None, 'by_id':{}}
        self.session.info['clock_sim_lookup'] = prior
        @automation.with_game_sim_lookup
        def receive(session, link, report):
            raise ValueError('fixture failure')
        with self.assertRaises(ValueError): receive(self.session, self.link, {})
        self.assertIs(self.session.info['clock_sim_lookup'], prior)

    def test_sending_heartbeat_allows_request_but_not_permanent_hang(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'relay_health.json'
            for state, age, expected in [('sending',20,True), ('sending',60,False), ('connected',20,False)]:
                path.write_text(json.dumps({'state':state,'checked_at':(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()}))
                self.assertEqual(relay_heartbeat_fresh(Path(folder)), expected)

    def test_report_processing_does_not_block_other_http_requests(self):
        started = threading.Event(); release = threading.Event()
        fake_session = MagicMock()
        @contextmanager
        def database(): yield fake_session
        def slow_receive(*args):
            started.set()
            if not release.wait(4): raise RuntimeError('probe could not run')
            return {'ok':True}
        application = FastAPI()
        application.add_api_route('/report', main.clock_report, methods=['POST'])
        @application.get('/probe')
        async def probe(): return {'responsive':True}
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://test') as client:
                pending = asyncio.create_task(client.post('/report', json={'game_day':1}, headers={'Authorization':'Bearer fixture'}))
                try:
                    for _ in range(100):
                        if started.is_set(): break
                        await asyncio.sleep(.01)
                    self.assertTrue(started.is_set())
                    response = await asyncio.wait_for(client.get('/probe'), timeout=.5)
                    self.assertEqual(response.json(), {'responsive':True})
                finally:
                    release.set()
                    response = await pending
                self.assertEqual(response.status_code, 200)
        with patch.object(main, 'db', database), patch.object(main.clock, 'receive', slow_receive):
            asyncio.run(run())
