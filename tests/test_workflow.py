"""Workflow navigation uses existing routes and never needs a live save."""
import copy
from html.parser import HTMLParser
from urllib.parse import urlsplit
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import main, workflow
from app.models import Record
from tests import test_infinite_decades as fixtures


class PageLinks(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = []
        self.hrefs = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.append(attrs['id'])
        if tag == 'a' and attrs.get('href'):
            self.hrefs.append(attrs['href'])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InfiniteDecadesTests()
        self.fixture.setUp()
        self.fixture.save.settings = {**self.fixture.save.settings, 'automation_enabled': False}
        self.fixture.session.commit()
        self.binding = patch.object(main, 'SessionLocal', self.fixture.sessions)
        self.binding.start()
        self.client = TestClient(main.app)
        self.client.post('/saves/select', data={'save_id': self.fixture.save.id})

    def tearDown(self):
        self.client.close()
        self.binding.stop()
        self.fixture.tearDown()

    def get(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path + ': ' + response.text[:200])
        return response.text

    def test_every_feature_stays_in_exactly_one_workflow_group(self):
        pages = [page for group in main.NAVIGATION_GROUPS for page in group['pages']]
        self.assertCountEqual(pages, main.FEATURES)
        self.assertEqual(len(pages), len(set(pages)))
        self.assertEqual(main.navigation_group_for('planner')['id'], 'play')
        self.assertEqual(main.navigation_group_for('infinite-decades')['id'], 'history')
        self.assertEqual(main.navigation_group_for('health')['id'], 'settings')
        self.assertEqual(main.navigation_group_for('names')['id'], 'people')
        self.assertEqual(main.navigation_group_for('save-a-sims')['id'], 'play')

    def test_home_prioritizes_play_but_keeps_all_existing_controls(self):
        html = self.get('/')
        self.assertIn('Session Home', html)
        self.assertLess(html.index('id="session-heading"'), html.index('UPCOMING CALENDAR'))
        self.assertEqual(html.count('id="resume-last-page"'), 1)
        for fragment in ('01 · PREPARE', '02 · PLAY', '03 · REVIEW', '04 · WRAP UP',
                         'action="/api/rule-packs"', 'action="/saves"',
                         'HOUSEHOLD FOCUS', 'DATA HEALTH', 'Save totals', 'TRACKER MAP'):
            self.assertIn(fragment, html)
        links = PageLinks(html)
        for page in set(main.FEATURES)-set(main.usability.OPTIONAL_PAGES):
            self.assertIn('/p/' + page, links.hrefs)

    def test_all_page_section_links_have_unique_rendered_targets(self):
        for page, sections in workflow.PAGE_SECTIONS.items():
            with self.subTest(page=page):
                html = self.get('/p/' + page + ('?view=tools' if page=='today' else ''))
                links = PageLinks(html)
                self.assertIn('aria-label="On this page"', html)
                for label, anchor in sections:
                    self.assertEqual(links.ids.count(anchor), 1, (page, anchor))
                    self.assertIn('#' + anchor, links.hrefs)

    def test_related_tasks_only_use_existing_routes_and_real_anchors(self):
        cache = {}
        for page, tasks in workflow.RELATED_TASKS.items():
            self.assertIn(page, main.FEATURES)
            for label, href in tasks:
                target = urlsplit(href)
                self.assertEqual(target.scheme, '')
                self.assertTrue(target.path.startswith('/p/'))
                self.assertIn(target.path[3:], main.FEATURES)
                if target.fragment:
                    if target.path not in cache:
                        cache[target.path] = PageLinks(self.get(target.path + ("?view=tools" if target.path=="/p/today" else "")))
                    self.assertIn(target.fragment, cache[target.path].ids, href)

    def test_marriage_dashboard_moves_without_changing_results_or_dates(self):
        f = self.fixture
        records = [
            Record(save_id=f.save.id, kind='roll', label='Ada marriage result', global_day=70,
                   data={'sim_id':f.people[0].id,'roll_type':'Marriage eligibility','completed':True,
                         'actual':2,'outcome':'May marry','suggested_marriage_global_day':102,'die':'d6'}),
            Record(save_id=f.save.id, kind='roll', label='Ben future marriage check', global_day=105,
                   data={'sim_id':f.people[1].id,'roll_type':'Marriage eligibility','completed':False,'die':'d6'}),
        ]
        f.session.add_all(records);f.session.commit()
        before = [(r.id,r.global_day,copy.deepcopy(r.data)) for r in records]
        html = self.get('/p/relationships')
        self.assertIn('Automatic marriage-roll dashboard', html)
        self.assertIn('May marry', html)
        self.assertIn('GD 105', html)
        self.assertIn('GD 102', html)
        self.assertIn('Create a courtship', html)
        self.assertIn('Generated marriage dates', html)
        self.assertIn('/p/life-records#dowries', html)
        self.assertNotIn('/api/rolls/' + records[1].id + '/roll', html)
        for record in records:
            f.session.refresh(record)
        self.assertEqual(before, [(r.id,r.global_day,r.data) for r in records])
        old_page = self.get('/p/challenge')
        self.assertIn('/p/relationships#marriage-rolls', old_page)
        self.assertNotIn('id="marriage-rolls"', old_page)
        self.assertIn('Succession rules', old_page)
        self.assertIn('Create campaign', old_page)

    def test_branch_page_uses_its_new_planning_breadcrumb(self):
        html = self.get('/p/infinite-decades')
        breadcrumb = html.split('aria-label="Breadcrumb">',1)[1].split('</nav>',1)[0]
        self.assertIn('History', breadcrumb)
        self.assertIn('Infinite Decades', breadcrumb)

    def test_branch_sidebar_keeps_status_and_checkpoint_destination(self):
        self.fixture.enable()
        html = self.get('/p/infinite-decades')
        card = html.split('class="panel infinite-sidebar-card"', 1)[1].split('</section>', 1)[0]
        self.assertIn('Infinite Decades · On', card)
        self.assertIn('Main line', card)
        self.assertIn('class="button infinite-sidebar-link"', card)
        self.assertIn('href="/p/infinite-decades"', card)
        self.assertIn('Branches &amp; checkpoints</a>', card)
        self.assertEqual(PageLinks(card).hrefs, ['/p/infinite-decades'])

    def test_quick_navigation_survives_partial_page_requests(self):
        response = self.client.get('/p/relationships', headers={'HX-Request':'true'})
        self.assertEqual(response.status_code, 200)
        html = response.text.split('<main ',1)[1].split('</main>',1)[0]
        self.assertIn('aria-label="Related tasks"', html)
        self.assertIn('aria-label="On this page"', html)
        self.assertIn('id="marriage-dates"', html)

    def test_tutorial_explains_game_and_tracker_sync_separately(self):
        html = self.get('/p/tutorial')
        self.assertIn('id="tracker-layout"', html)
        self.assertIn('These are different connections.', html)
        for group in main.NAVIGATION_GROUPS:
            self.assertIn(group['label'].replace('&', '&amp;'), html)


if __name__ == '__main__':
    unittest.main()
