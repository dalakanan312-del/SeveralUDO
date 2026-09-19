"""UI refresh contracts against disposable saves; never installed game data."""
import re
import unittest
from tests import test_usability as fixtures


class WorkspaceRefreshTests(unittest.TestCase):
    setUp = fixtures.UsabilityTests.setUp
    tearDown = fixtures.UsabilityTests.tearDown
    add = fixtures.UsabilityTests.add

    def test_today_starts_with_identity_then_connection_then_work(self):
        html = self.client.get('/p/today').text
        self.assertEqual(html.count('id="main-content"'), 1)
        self.assertLess(html.index('<h1>Today</h1>'), html.index('aria-label="Game connection status"'))
        self.assertLess(html.index('aria-label="Game connection status"'), html.index('data-workboard'))
        self.assertIn('href="#main-content" hx-boost="false"', html)

    def test_clock_hooks_are_unique_and_diagnostics_remain_available(self):
        html = self.client.get('/p/today').text
        for hook in ['data-clock-label', 'data-clock-detail', 'data-clock-save',
                     'data-clock-game', 'data-clock-seen', 'data-clock-receipt', 'data-mark-paused']:
            self.assertEqual(html.count(hook), 1, hook)
        self.assertIn('<details class="u-clock workspace-clock"', html)
        self.assertIn('Last successful report', html)
        self.assertIn('Game Connection', html)

    def test_all_play_tools_and_section_anchors_are_retained(self):
        html = self.client.get('/p/today').text
        for href in ['/p/today?view=tools', '/p/planner', '/p/rolls', '/p/automation']:
            self.assertIn('href="' + href + '"', html)
        self.assertIn('action="/api/today/pregnancy-count-rolls"', html)
        self.assertIn('aria-label="Work sections" hx-boost="false"', html)
        for section in ['decisions', 'happening', 'completed']:
            self.assertEqual(html.count('id="work-' + section + '"'), 1)

    def test_counts_and_partial_responses_agree(self):
        self.add('roll', 'A pending check', sim_id=self.f.people[0].id, die='d6')
        html = self.client.get('/p/today').text
        partial = self.client.get('/api/ui/today/decisions').text
        count = re.search(r'data-work-count="decisions">([0-9]+)', html).group(1)
        self.assertIn('data-work-total="' + count + '"', partial)
        self.assertNotIn('<main', partial)
        self.assertIn('data-roll-id=', partial)

    def test_light_theme_and_non_today_pages_keep_context(self):
        self.f.save.settings = {**self.f.save.settings, 'visual_theme': {'preset': 'daylight'}}
        self.f.session.commit()
        html = self.client.get('/p/today').text
        self.assertIn('data-theme-mode="light"', html)
        for url in ['/p/sims', '/p/today?view=tools']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.text.count('id="main-content"'), 1)
            self.assertIn('workspace-clock', response.text)


if __name__ == '__main__':
    unittest.main()
