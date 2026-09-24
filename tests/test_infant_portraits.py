"""Infant framing is a non-destructive compositor adjustment, not a source edit."""
import copy
import io
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw
from sqlalchemy import select
from app import decade_album as album
from app.models import ChronicleSave, Portrait
from tests import test_decade_album as fixtures


def infant_image():
    image = Image.new('RGBA', (200, 400))
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 0, 150, 235), fill=(180, 70, 40, 255))
    draw.rectangle((29, 2, 29, 232), fill=(180, 70, 40, 18))
    draw.ellipse((0, 313, 199, 399), fill=(0, 100, 250, 255))
    return image


def encoded(image):
    raw = io.BytesIO(); image.save(raw, 'PNG'); return raw.getvalue()


class InfantFramingTests(unittest.TestCase):
    def test_detached_pillow_is_removed_without_losing_baby_or_soft_edges(self):
        source = infant_image(); before = source.tobytes()
        body = album._infant_body(source)
        expected = source.crop((29, 0, 151, 236))
        self.assertEqual(body.size, expected.size)
        self.assertEqual(body.tobytes(), expected.tobytes())
        self.assertEqual(source.tobytes(), before)

    def test_infant_is_enlarged_after_pillow_crop(self):
        raw = encoded(infant_image())
        actor = album._actor({'photo_age_stage': 'Infant'}, raw)
        self.assertEqual(actor.height, 220)
        self.assertGreater(actor.height, 160)
        # Resampling translucent edges may round channels slightly; the blue
        # pillow (B=250) must be gone, including from antialiased edge pixels.
        self.assertLess(actor.getchannel('B').getextrema()[1], 100)

    def test_other_stages_keep_their_full_photo_and_existing_size(self):
        raw = encoded(infant_image())
        for stage, height in [('newborn', 140), ('toddler', 205), ('child', 295), ('adult', 440)]:
            actor = album._actor({'photo_age_stage': stage}, raw)
            self.assertEqual(actor.height, height)
            self.assertGreater(actor.getchannel('B').getextrema()[1], 200)

    def test_crawling_and_seated_poses_without_a_prop_are_not_cropped(self):
        for size in [(240, 181), (153, 214)]:
            image = Image.new('RGBA', size)
            ImageDraw.Draw(image).ellipse((0, 0, size[0]-1, size[1]-1), fill='red')
            result = album._infant_body(image)
            self.assertEqual(result.size, image.size)
            self.assertEqual(result.tobytes(), image.tobytes())

    def test_opaque_upload_or_attached_pillow_is_not_blindly_sliced(self):
        image = infant_image(); draw = ImageDraw.Draw(image)
        draw.rectangle((40, 200, 140, 345), fill='red')
        for source in [image, infant_image().convert('RGB').convert('RGBA')]:
            self.assertEqual(album._infant_body(source).tobytes(), source.tobytes())

    def test_small_gap_or_narrow_lower_body_part_is_not_a_pillow(self):
        for gap_end, lower_width in [(238, 200), (313, 20)]:
            image = Image.new('RGBA', (200, 400))
            draw = ImageDraw.Draw(image)
            draw.rectangle((10, 0, 190, 235), fill='red')
            draw.rectangle((0, gap_end, lower_width-1, 399), fill='blue')
            self.assertEqual(album._infant_body(image).tobytes(), image.tobytes())


class InfantAlbumRefreshTests(unittest.TestCase):
    setUp = fixtures.DecadeAlbumTests.setUp
    tearDown = fixtures.DecadeAlbumTests.tearDown
    png = fixtures.DecadeAlbumTests.png
    add = fixtures.DecadeAlbumTests.add

    def test_refresh_preserves_members_and_originals_without_scanning_current_tray(self):
        self.sources[self.people[0].id] = (encoded(infant_image()), 'infant', 'Tray Library')
        record = self.add([self.people[0].id, self.people[1].id])['snapshot']
        # Simulate a previously saved layout; retain the archived source bytes.
        old_composite = encoded(Image.new('RGB', (200, 300), 'blue'))
        album._image(self.session, record.id).image = old_composite
        record.data = {**record.data, 'layout_version': 1}
        members = copy.deepcopy(record.data['members'])
        contributions = copy.deepcopy(record.data['contributions'])
        originals = {m['sim_id']: album._image(self.session, record.id, m['portrait_stage']).image for m in members}
        self.save.global_day = 1  # Another Infinite Decades branch may be earlier.
        with patch.object(album, '_source_photos', side_effect=AssertionError('Must not rescan')), patch.object(album, 'discover_portraits', side_effect=AssertionError('Must not scan Tray')):
            refreshed = album.refresh_layout(self.session, self.save, record.id, record.version)
        self.assertEqual(refreshed.id, record.id)
        self.assertEqual(record.data['members'], members)
        self.assertEqual(record.data['contributions'], contributions)
        self.assertEqual(record.data['layout_version'], album.LAYOUT_VERSION)
        self.assertEqual(album._image(self.session, record.id, 'original').image, old_composite)
        self.assertNotEqual(album._image(self.session, record.id).image, old_composite)
        for member in members:
            self.assertEqual(album._image(self.session, record.id, member['portrait_stage']).image, originals[member['sim_id']])
        before = (record.version, self.save.revision)
        album.refresh_layout(self.session, self.save, record.id, record.version)
        self.assertEqual((record.version, self.save.revision), before)
        self.assertEqual(len(album.archives(self.session, self.save)), 1)

    def test_stale_refresh_and_wrong_save_are_rejected(self):
        record = self.add([self.people[0].id])['snapshot']
        image = album._image(self.session, record.id).image
        with self.assertRaisesRegex(ValueError, 'changed'):
            album.refresh_layout(self.session, self.save, record.id, record.version-1)
        other = ChronicleSave(workspace_id=self.save.workspace_id, name='Other')
        self.session.add(other); self.session.flush()
        with self.assertRaisesRegex(ValueError, 'no longer available'):
            album.refresh_layout(self.session, other, record.id, record.version)
        self.assertEqual(album._image(self.session, record.id).image, image)

    def test_missing_source_leaves_existing_composite_unchanged(self):
        record = self.add([self.people[0].id])['snapshot']
        before = (copy.deepcopy(record.data), record.version, album._image(self.session, record.id).image)
        self.session.delete(album._image(self.session, record.id, 'sim-'+self.people[0].id)); self.session.flush()
        with self.assertRaisesRegex(ValueError, 'missing'):
            album.refresh_layout(self.session, self.save, record.id, record.version)
        self.assertEqual((record.data, record.version, album._image(self.session, record.id).image), before)
        self.assertIsNone(album._image(self.session, record.id, 'original'))


class InfantRefreshPageTests(unittest.TestCase):
    setUp = fixtures.DecadeAlbumPageTests.setUp
    tearDown = fixtures.DecadeAlbumPageTests.tearDown

    def test_refresh_button_updates_only_saved_album(self):
        raw = encoded(infant_image())
        sources = {p.id:(raw,'infant','Test') for p in self.f.people}
        with patch.object(album, '_source_photos', return_value=(sources,5,0,0)):
            self.client.post('/api/decade-snapshots', data={'year':1320,'member_ids':self.f.people[0].id})
        self.f.session.expire_all()
        record = album.archives(self.f.session, self.f.save)[0]
        album._image(self.f.session, record.id).image = encoded(Image.new('RGB', (80,80), 'blue'))
        self.f.session.commit()
        path = '/api/decade-snapshots/'+record.id+'/refresh'
        html = self.client.get('/p/decade-snapshots').text
        self.assertIn('Refresh saved portrait layout', html)
        self.assertIn(path, html)
        response = self.client.post(path, data={'version':record.version})
        self.assertEqual(response.status_code, 200)
        self.assertIn('All members and original photos were kept', response.text)
        self.f.session.expire_all()
        self.assertEqual(record.data['member_ids'], [self.f.people[0].id])
        self.assertEqual(len(album.archives(self.f.session, self.f.save)), 1)


if __name__ == '__main__':
    unittest.main()
