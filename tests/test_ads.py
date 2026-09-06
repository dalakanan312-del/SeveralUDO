from pathlib import Path
import unittest

from app.config import Settings


ROOT = Path(__file__).resolve().parents[1]


class AdvertisingConsentTests(unittest.TestCase):
    def test_desktop_tracker_never_exposes_ad_configuration(self):
        config = Settings(
            database_url="sqlite:///./data/test.db",
            advertising_enabled=True,
            google_adsense_client_id="ca-pub-example",
            google_adsense_footer_slot="1234567890",
        )
        self.assertEqual(config.ads_config, {"consent_available": False, "verification_available": False, "verification_client": "", "available": False, "client": "", "footer_slot": ""})

    def test_hosted_ads_require_owner_enablement_and_a_complete_unit(self):
        disabled = Settings(
            database_url="postgresql://example.invalid/decades",
            advertising_enabled=False,
            google_adsense_client_id="ca-pub-example",
            google_adsense_footer_slot="1234567890",
        )
        enabled = Settings(
            database_url="postgresql://example.invalid/decades",
            advertising_enabled=True,
            google_adsense_client_id="ca-pub-example",
            google_adsense_footer_slot="1234567890",
        )
        self.assertFalse(disabled.ads_config["available"])
        self.assertTrue(disabled.ads_config["consent_available"])
        self.assertEqual(enabled.ads_config, {"consent_available": True, "verification_available": True, "verification_client": "ca-pub-example", "available": True, "client": "ca-pub-example", "footer_slot": "1234567890"})

    def test_hosted_default_support_unit_is_ready_after_opt_in(self):
        config = Settings(database_url="postgresql://example.invalid/decades")
        self.assertTrue(config.ads_config["available"])
        self.assertEqual(config.ads_config["client"], "ca-pub-7784501722688975")
        self.assertEqual(config.ads_config["footer_slot"], "2263415805")

    def test_shared_shell_and_client_require_an_explicit_opt_in(self):
        template = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        script = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('/static/ads.css', template)
        self.assertIn('data-ad-preference="accepted"', template)
        self.assertIn('data-ad-preference="declined"', template)
        self.assertIn('help support the creator', template)
        self.assertIn('data-manage-ad-preference', template)
        self.assertIn('ads_config.consent_available', template)
        self.assertIn('google-adsense-account', template)
        self.assertIn('ads_config.verification_client', template)
        self.assertIn("if(preference==='accepted')loadOptInAdvertising()", script)
        self.assertIn("pagead2.googlesyndication.com", script)
        self.assertIn("event.preventDefault()", script)
        self.assertIn(".ad-consent-dialog[hidden]{display:none!important}", (ROOT / 'app' / 'static' / 'ads.css').read_text(encoding='utf-8'))
        self.assertIn("decades-ad-preference", script)


if __name__ == "__main__":
    unittest.main()