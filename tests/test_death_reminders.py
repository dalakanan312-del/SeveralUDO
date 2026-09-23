"""Death reminders are a read-only projection, independent of Today filters."""
import unittest
from app import death_reminders as reminders, infinite_decades
from app.models import ChronicleSave
from tests import test_usability as fixtures


class DeathReminderTests(unittest.TestCase):
    setUp = fixtures.UsabilityTests.setUp
    tearDown = fixtures.UsabilityTests.tearDown
    add = fixtures.UsabilityTests.add

    def pending(self, **kwargs):
        return reminders.pending(self.f.session, self.f.save, self.f.user.id, **kwargs)

    def test_only_unconfirmed_due_deaths_from_this_save(self):
        today = self.add('sim', 'Today', death_global_day=100, cause_of_death='Childbirth')
        old = self.add('sim', 'Overdue', death_global_day=98)
        self.add('sim', 'Future', death_global_day=101)
        self.add('sim', 'Confirmed', death_global_day=100, death_confirmed=True)
        self.add('sim', 'Game deceased', death_global_day=100, game_was_dead=True)
        self.add('sim', 'Paused branch', death_global_day=100, infinite_frozen=True)
        self.add('sim', 'No death date')
        deleted = self.add('sim', 'Deleted', death_global_day=100)
        deleted.deleted = True
        self.add('roll', 'Not a Sim', death_global_day=100)
        foreign = ChronicleSave(workspace_id=self.f.workspace.id, name='Other save')
        self.f.session.add(foreign); self.f.session.flush()
        outsider = self.add('sim', 'Other save death', death_global_day=100)
        outsider.save_id = foreign.id; self.f.session.commit()
        data = self.pending()
        self.assertEqual((data['today_count'], data['overdue_count']), (1, 1))
        self.assertEqual([r['id'] for r in data['items']], [today.id, old.id])
        self.assertEqual(data['items'][0]['cause'], 'Childbirth')
        self.assertEqual(data['items'][1]['cause'], 'Cause not recorded')

    def test_advance_and_rewind_follow_tracker_day(self):
        self.add('sim', 'Scheduled tomorrow', death_global_day=101)
        self.assertEqual(self.pending()['items'], [])
        self.f.save.global_day = 101
        self.assertEqual(self.pending()['today_count'], 1)
        self.f.save.global_day = 102
        self.assertEqual(self.pending()['overdue_count'], 1)
        self.f.save.global_day = 100
        self.assertEqual(self.pending()['items'], [])

    def test_bounded_payload_preserves_total_and_prioritizes_today(self):
        for number in range(52):
            self.add('sim', 'Old ' + str(number), death_global_day=90)
        today = self.add('sim', 'Today', death_global_day=100)
        data = self.pending()
        self.assertEqual(len(data['items']), reminders.LIMIT)
        self.assertEqual((data['today_count'], data['overdue_count']), (1, 52))
        self.assertEqual(data['items'][0]['id'], today.id)

    def test_recovery_and_paused_dynasty_suppress_popups(self):
        self.add('sim', 'Scheduled death', death_global_day=100)
        data = self.pending(clock_status={'recovery_required': True})
        self.assertTrue(data['suppressed']); self.assertEqual(data['items'], [])
        self.f.enable()
        state = infinite_decades.state(self.f.save)
        self.f.save.settings = {**self.f.save.settings, infinite_decades.KEY: {**state, 'status': 'paused'}}
        self.assertTrue(self.pending()['suppressed'])

    def test_poll_and_review_do_not_confirm_or_modify_death(self):
        sim = self.add('sim', '<script>Test</script>', death_global_day=100,
                       cause_of_death='A failed roll', death_source_roll_id='source-1')
        before = (dict(sim.data), sim.version)
        for _ in range(2):
            response = self.client.get('/api/live-status')
            self.assertEqual(response.status_code, 200)
            data = response.json()['death_reminders']
            self.assertEqual(data['user_id'], self.f.user.id)
            self.assertEqual(data['save_id'], self.f.save.id)
            self.assertEqual(data['items'][0]['source_roll_id'], 'source-1')
        review = self.client.get(data['items'][0]['review_url'])
        self.assertEqual(review.status_code, 200)
        self.assertIn('id="death-' + sim.id + '"', review.text)
        self.assertIn('&lt;script&gt;Test&lt;/script&gt;', review.text)
        self.f.session.refresh(sim)
        self.assertEqual((sim.data, sim.version), before)

    def test_banner_available_with_today_filters_and_on_profiles(self):
        for path in ['/p/today?window=future&household=all', '/sims/' + self.f.people[0].id, '/p/sims']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('data-death-reminders', response.text)
            self.assertIn('/static/death_reminders.js', response.text)


if __name__ == '__main__':
    unittest.main()
