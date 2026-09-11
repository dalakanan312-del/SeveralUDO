"""Game-thumbnail decoding regressions; all images and databases are disposable."""
import base64
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import clock, decade_portraits, portraits, tray_scanner
from app.db import Base
from app.models import ChronicleSave, Portrait, Record, Workspace
from app.save_scanner import SaveScanError


def image_bytes(image, kind="PNG"):
    output = io.BytesIO()
    image.save(output, format=kind)
    return output.getvalue()


def with_segment(jpeg, payload, marker=b"\xff\xe0"):
    segment = marker + (len(payload) + 2).to_bytes(2, "big") + payload
    # Insert after the existing JFIF segment, like a real Tray JPEG.
    return jpeg[:20] + segment + jpeg[20:]


def thumbnail(size=(640, 640), mask_size=None):
    # Stretched edge colors only belong behind the mask, never on the matte.
    color = Image.new("RGB", size, (220, 160, 120))
    ImageDraw.Draw(color).rectangle((size[0]//3, size[1]//3, size[0]*2//3, size[1]*2//3), fill=(30, 80, 160))
    mask = Image.new("L", mask_size or size, 0)
    ImageDraw.Draw(mask).rectangle((mask.width//3, mask.height//3, mask.width*2//3, mask.height*2//3), fill=255)
    png = image_bytes(mask)
    jpeg = image_bytes(color, "JPEG")
    return with_segment(jpeg, b"ALFA" + len(png).to_bytes(4, "big") + png), mask


def sgi(jpeg):
    key = bytes.fromhex("4125e6cd47bab21a")
    return b"\0" * 24 + bytes(value ^ key[index % 8] for index, value in enumerate(jpeg))


def household_bytes():
    def varint(value):
        parts = []
        while value > 127:
            parts.append((value & 127) | 128)
            value >>= 7
        return bytes(parts + [value])

    def field(number, value):
        return varint(number * 8 + 2) + varint(len(value)) + value

    sim = b"\x09" + (0x1234).to_bytes(8, "little") + field(5, b"Tray") + field(6, b"Sim")
    payload = field(1, field(6, sim))
    return b"\0" * 12 + len(payload).to_bytes(4, "little") + payload


class TrayTransparencyTests(unittest.TestCase):
    def test_sgi_applies_embedded_mask_without_changing_jpeg_colors(self):
        jpeg, mask = thumbnail()
        result = Image.open(io.BytesIO(tray_scanner.decode_sgi(sgi(jpeg))))
        self.assertEqual((result.format, result.mode, result.size), ("PNG", "RGBA", (640, 640)))
        self.assertEqual(result.getchannel("A").tobytes(), mask.tobytes())
        self.assertEqual(result.convert("RGB").tobytes(), Image.open(io.BytesIO(jpeg)).convert("RGB").tobytes())

    def test_ordinary_jpeg_keeps_original_bytes(self):
        jpeg = image_bytes(Image.new("RGB", (48, 48), "red"), "JPEG")
        self.assertEqual(tray_scanner.decode_sgi(sgi(jpeg)), jpeg)

    def test_other_metadata_is_not_mistaken_for_game_alpha(self):
        jpeg = image_bytes(Image.new("RGB", (48, 48), "red"), "JPEG")
        jpeg = with_segment(jpeg, b"ALFA-not-a-mask", marker=b"\xff\xfe")
        self.assertEqual(tray_scanner.decode_sgi(sgi(jpeg)), jpeg)

    def test_invalid_alpha_is_rejected_instead_of_importing_a_blurred_image(self):
        plain = image_bytes(Image.new("RGB", (48, 48), "red"), "JPEG")
        good, _ = thumbnail((48, 48))
        invalid = [
            thumbnail((48, 48), (32, 32))[0],
            with_segment(plain, b"ALFA\0\0\0\x40short"),
            with_segment(plain, b"ALFA\0\0\0\x08notapng!"),
            with_segment(good, b"ALFA\0\0\0\x08notapng!"),
        ]
        for jpeg in invalid:
            with self.subTest(size=len(jpeg)), self.assertRaises(SaveScanError):
                tray_scanner.decode_sgi(sgi(jpeg))

    def test_native_game_jpeg_normalization_also_honors_alpha(self):
        jpeg, mask = thumbnail((48, 48))
        encoded, mime = portraits.normalize_image(jpeg, lossless=True)
        result = Image.open(io.BytesIO(encoded))
        self.assertEqual(mime, "image/webp")
        self.assertEqual(result.getchannel("A").tobytes(), mask.tobytes())

    def test_lossless_storage_preserves_alpha_and_visible_pixels(self):
        jpeg, _ = thumbnail()
        decoded = tray_scanner.decode_sgi(sgi(jpeg))
        original = Image.open(io.BytesIO(decoded))
        encoded, _ = portraits.normalize_image(decoded, lossless=True)
        result = Image.open(io.BytesIO(encoded))
        self.assertEqual(result.size, (640, 640))
        self.assertEqual(result.getchannel("A").tobytes(), original.getchannel("A").tobytes())
        self.assertEqual(result.getpixel((320, 320)), original.getpixel((320, 320)))
        self.assertEqual(portraits.normalize_image(decoded, lossless=True)[0], encoded)

    def test_regular_transparent_uploads_keep_their_transparency(self):
        for mode in ("RGBA", "P"):
            source = Image.new(mode, (48, 48))
            if mode == "P":
                source.info["transparency"] = 0
            encoded, _ = portraits.normalize_image(image_bytes(source))
            result = Image.open(io.BytesIO(encoded))
            self.assertEqual(result.mode, "RGBA")
            self.assertEqual(result.getpixel((0, 0))[3], 0)

    def test_partial_opacity_survives_round_trip(self):
        source = Image.new("RGBA", (48, 48), (100, 80, 60, 128))
        encoded, _ = portraits.normalize_image(image_bytes(source), lossless=True)
        self.assertEqual(Image.open(io.BytesIO(encoded)).getpixel((24, 24)), (100, 80, 60, 128))

    def test_large_portraits_are_bounded_and_small_ones_are_not_upscaled(self):
        for size, expected in (((1800, 1200), (1600, 1067)), ((48, 48), (48, 48))):
            encoded, _ = portraits.normalize_image(image_bytes(Image.new("RGBA", size, (30, 40, 50, 128))), lossless=True)
            self.assertEqual(Image.open(io.BytesIO(encoded)).size, expected)

    def test_decade_tile_uses_the_selected_solid_background_behind_the_sim(self):
        jpeg, _ = thumbnail((100, 100))
        for raw in (jpeg, tray_scanner.decode_sgi(sgi(jpeg))):
            tile = decade_portraits._portrait_tile(raw, (122, 122), "#123456")
            for position in ((0, 0), (11, 11), (26, 61), (61, 26)):
                self.assertEqual(tile.getpixel(position), (18, 52, 86))
            self.assertEqual(tile.getpixel((61, 61)), Image.open(io.BytesIO(jpeg)).getpixel((50, 50)))

    def test_decade_tile_composites_semtransparent_edges(self):
        raw = image_bytes(Image.new("RGBA", (100, 100), (200, 100, 0, 128)))
        tile = decade_portraits._portrait_tile(raw, (122, 122), "#000000")
        self.assertEqual(tile.getpixel((61, 61)), (100, 50, 0))


class TrayStorageTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.workspace = Workspace(name="Portrait tests")
        self.session.add(self.workspace); self.session.flush()
        self.save = ChronicleSave(workspace_id=self.workspace.id, name="Tray test")
        self.session.add(self.save); self.session.flush()
        self.sim = Record(save_id=self.save.id, kind="sim", label="Tray Sim", data={"game_age_stage": "adult"})
        self.session.add(self.sim); self.session.flush()
        self.jpeg, _ = thumbnail()
        self.payload = {
            "portrait_image_base64": base64.b64encode(tray_scanner.decode_sgi(sgi(self.jpeg))).decode(),
            "portrait_source": "tray-library-game", "age_stage": "adult",
        }

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def stored(self):
        return self.session.scalar(select(Portrait).where(Portrait.record_id == self.sim.id))

    def test_storage_retains_tray_resolution_and_is_idempotent(self):
        self.assertTrue(clock._store_game_portrait(self.session, self.save, self.sim, self.payload))
        result = Image.open(io.BytesIO(self.stored().image))
        self.assertEqual((result.size, result.mode), ((640, 640), "RGBA"))
        self.assertFalse(clock._store_game_portrait(self.session, self.save, self.sim, self.payload))

    def test_clock_or_save_cannot_replace_tray_with_a_smaller_thumbnail(self):
        clock._store_game_portrait(self.session, self.save, self.sim, self.payload)
        image = self.stored().image
        for source in ("clock-sync-game", "save-file-game"):
            self.assertFalse(clock._store_game_portrait(self.session, self.save, self.sim, {**self.payload, "portrait_source": source}))
            self.assertEqual((self.stored().image, self.stored().source), (image, "tray-library-game"))

    def test_non_tray_game_thumbnails_remain_bounded_at_512(self):
        clock._store_game_portrait(self.session, self.save, self.sim, {**self.payload, "portrait_source": "save-file-game"})
        self.assertEqual(Image.open(io.BytesIO(self.stored().image)).size, (512, 512))

    def test_manual_stage_portrait_stays_protected(self):
        self.session.add(Portrait(save_id=self.save.id, record_id=self.sim.id, stage="Adult", source="upload",
                                  image=b"players-original-image", mime_type="image/png"))
        self.session.flush()
        self.assertFalse(clock._store_game_portrait(self.session, self.save, self.sim, self.payload))
        self.assertEqual(self.stored().image, b"players-original-image")

    def test_rescan_repairs_existing_opaque_thumbnail_and_does_not_touch_tray(self):
        old = Image.open(io.BytesIO(self.jpeg)).convert("RGB")
        old.thumbnail((512, 512))
        row = Portrait(save_id=self.save.id, record_id=self.sim.id, stage="adult", source="tray-library-game",
                       image=image_bytes(old, "WEBP"), mime_type="image/webp")
        self.session.add(row); self.session.flush()
        old_id = row.id
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {"0x0!0x9999.householdbinary": household_bytes(), "0x13!0x1234.sgi": sgi(self.jpeg)}
            for name, raw in files.items():
                (root / name).write_bytes(raw)
            result = tray_scanner.import_portraits(self.session, self.save, root=root)
            self.assertEqual((result["updated"], result["invalid"]), (1, 0))
            self.assertEqual(self.stored().id, old_id)
            self.assertEqual(Image.open(io.BytesIO(self.stored().image)).mode, "RGBA")
            second = tray_scanner.import_portraits(self.session, self.save, root=root)
            self.assertEqual((second["updated"], second["unchanged"]), (0, 1))
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, files)


if __name__ == "__main__":
    unittest.main()
