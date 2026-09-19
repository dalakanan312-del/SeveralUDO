"""Read-only UI preview backed exclusively by disposable test fixtures."""
import json
import mimetypes
import os
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['DECADES_SKIP_STARTUP_MIGRATIONS'] = '1'
from tests.test_usability import UsabilityTests
from app.models import ClockLink
from datetime import datetime, timezone


def snapshots():
    fixture = UsabilityTests()
    fixture.setUp()
    try:
        fixture.f.save.name = 'The Cooley Chronicle'
        fixture.f.save.settings = {**fixture.f.save.settings, 'automatic_occult_rolls': True}
        fixture.f.session.add(ClockLink(save_id=fixture.f.save.id, enabled=True, token_hash='preview-only',
            last_game_day=31, last_game_hour=9, last_game_minute=24, last_seen_at=datetime.now(timezone.utc)))
        fixture.f.session.commit()
        person = fixture.f.people[0]
        fixture.add('roll', 'Birthday survival', sim_id=person.id, roll_type='Young Adult', die='d20', bad_results='1')
        fixture.add('roll', 'Household harvest', sim_id=person.id, die='d6', bad_results='1', roll_scope='household', household_name='Cooley')
        fixture.add('illness', 'Ben Cooley — recovering from fever', day=98, sim_id=fixture.f.people[1].id, status='Active')
        fixture.add('roll', 'A successful match', sim_id=person.id, die='d6', completed=True,
            actual=5, outcome='A promising match', completed_global_day=100)
        pages = {}
        for mode in ['dark', 'light']:
            fixture.f.save.settings = {**fixture.f.save.settings, 'visual_theme': {'preset': 'daylight' if mode == 'light' else 'heirloom'}}
            fixture.f.session.commit()
            for path in ['/p/today', '/p/today?window=overdue', '/p/today?window=future', '/p/today?view=tools', '/p/sims']:
                response = fixture.client.get(path)
                assert response.status_code == 200
                pages[(path, mode)] = response.text.replace('A little progress. Another chapter.', 'UI preview · sample data only').encode()
        api = {'/api/live-status': fixture.client.get('/api/live-status').content,
               '/api/notifications': b'{"items":[]}'}
        fixture.add('roll', 'Fresh sample task', sim_id=person.id, die='d6', bad_results='1')
        for group in ['decisions', 'happening', 'completed']:
            api['/api/ui/today/' + group] = fixture.client.get('/api/ui/today/' + group).content
        return pages, api
    finally:
        fixture.tearDown()


PAGES, API = snapshots()
STATIC = (ROOT / 'app' / 'static').resolve()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        params = parse_qs(url.query)
        mode = params.get('theme', ['dark'])[0]
        path = url.path
        if path.startswith('/static/'):
            file = (STATIC / path.removeprefix('/static/')).resolve()
            if not file.is_relative_to(STATIC) or not file.is_file():
                self.send_error(404)
                return
            content = file.read_bytes()
            content_type = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
        elif path in API:
            content = API[path]
            content_type = 'text/html' if '/today/' in path else 'application/json'
        elif path == '/favicon.ico':
            self.send_error(404)
            return
        else:
            path = '/p/today' if path == '/' else path
            if params.get('view') == ['tools']:
                path += '?view=tools'
            elif params.get('window') in [['overdue'], ['future']]:
                path += '?window=' + params['window'][0]
            if (path, mode) not in PAGES:
                self.send_error(404, 'Read-only UI preview; no live save is connected')
                return
            content = PAGES[(path, mode)]
            content_type = 'text/html'
        self.send_response(200)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        if urlsplit(self.path).path == '/api/ui/preferences':
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            self.send_error(403, 'Preview only. No game changes are applied.')

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    print('Read-only sample UI: http://127.0.0.1:9893/p/today', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 9893), Handler).serve_forever()
