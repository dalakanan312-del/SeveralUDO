"""Photo navigation contracts with disposable save data only."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from app.models import ChronicleSave, Portrait, Record, Workspace
from tests import test_usability as fixtures
import desktop_launcher


class PhotoNavigationTests(unittest.TestCase):
    setUp = fixtures.UsabilityTests.setUp
    tearDown = fixtures.UsabilityTests.tearDown

    def photo(self):
        person = self.f.people[0]
        self.f.session.add(Portrait(save_id=self.f.save.id, record_id=person.id,
            stage='default', image=b'photo bytes', mime_type='image/png'))
        self.f.session.commit()
        return person

    def test_all_app_pages_have_back_and_today(self):
        for page in ['/p/today', '/p/sims', '/p/decade-snapshots', '/p/portrait-studio']:
            response = self.client.get(page)
            self.assertEqual(response.status_code, 200)
            self.assertIn('data-tracker-back', response.text)
            self.assertIn('navigation.js', response.text)
            self.assertEqual(response.text.count('aria-label="Back and exit controls"'), 1)

    def test_viewer_has_real_exit_links_and_does_not_change_photo(self):
        person = self.photo()
        response = self.client.get(f'/photos/{person.id}/default?v=17')
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'href="/sims/{person.id}" data-tracker-back>Close photo', response.text)
        self.assertIn(f'src="/portraits/{person.id}/default?v=17"', response.text)
        self.assertIn('?download=1" download', response.text)
        self.assertEqual(self.client.get(f'/portraits/{person.id}/default').content, b'photo bytes')

    def test_raw_image_navigation_redirects_but_image_embeds_do_not(self):
        person = self.photo()
        url = f'/portraits/{person.id}/default?v=17'
        response = self.client.get(url, headers={'Sec-Fetch-Dest':'document'}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], f'/photos/{person.id}/default?v=17')
        image = self.client.get(url, headers={'Sec-Fetch-Dest':'image'})
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers['content-type'], 'image/png')
        self.assertEqual(image.content, b'photo bytes')
        self.assertEqual(self.client.get(url, headers={'If-None-Match':image.headers['etag']}).status_code, 304)

    def test_download_is_attachment_even_for_document_or_cached_image(self):
        person = self.photo()
        url = f'/portraits/{person.id}/default'
        etag = self.client.get(url).headers['etag']
        response = self.client.get(url+'?download=1', headers={'Sec-Fetch-Dest':'document','If-None-Match':etag}, follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response.headers['content-disposition'])
        self.assertEqual(response.content, b'photo bytes')

    def test_no_photo_still_has_an_exit_and_missing_record_is_404(self):
        self.assertEqual(self.client.get(f'/photos/{self.f.people[0].id}/default').status_code, 200)
        self.assertEqual(self.client.get(f'/portraits/{self.f.people[0].id}/default').status_code, 404)
        self.assertEqual(self.client.get('/photos/does-not-exist/default').status_code, 404)

    def test_other_users_photos_are_not_exposed(self):
        workspace = Workspace(name='Not ours');self.f.session.add(workspace);self.f.session.flush()
        save = ChronicleSave(workspace_id=workspace.id, name='Private');self.f.session.add(save);self.f.session.flush()
        person = Record(save_id=save.id, kind='sim', label='Private person', data={})
        self.f.session.add(person);self.f.session.commit()
        for path in [f'/photos/{person.id}/default', f'/portraits/{person.id}/default?download=1']:
            self.assertIn(self.client.get(path, follow_redirects=False).status_code, [403,404])


class NativeNavigationTests(unittest.TestCase):
    def menu(self, window):
        fake = SimpleNamespace(MenuAction=lambda title, function: SimpleNamespace(title=title, function=function))
        with patch.dict('sys.modules', {'webview.menu':fake}):
            return desktop_launcher.native_navigation(window)

    def test_native_back_prefers_photo_or_dialog_close(self):
        window = Mock();window.evaluate_js.return_value = True
        menu = self.menu(window);menu[0].function()
        self.assertIn('Back', menu[0].title)
        window.load_url.assert_not_called()

    def test_native_back_escapes_error_pages_without_history(self):
        for value in [False, None]:
            window = Mock();window.evaluate_js.return_value = value
            self.menu(window)[0].function()
            window.load_url.assert_called_once_with(desktop_launcher.URL+'/p/today')
        window = Mock();window.evaluate_js.side_effect = RuntimeError('Page unavailable')
        self.menu(window)[0].function()
        window.load_url.assert_called_once_with(desktop_launcher.URL+'/p/today')

    def test_native_today_does_not_depend_on_page_javascript(self):
        window = Mock();self.menu(window)[1].function()
        window.evaluate_js.assert_not_called()
        window.load_url.assert_called_once_with(desktop_launcher.URL+'/p/today')
