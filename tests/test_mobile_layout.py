from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MobileLayoutTests(unittest.TestCase):
    def test_shared_shell_has_mobile_navigation_controls(self):
        template = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('name="viewport"', template)
        self.assertIn('/static/mobile.css', template)
        self.assertIn('/static/tutorial.css', template)
        self.assertIn('class="mobile-menu-toggle"', template)
        self.assertIn('aria-controls="mobile-navigation"', template)
        self.assertIn('id="mobile-navigation"', template)
        self.assertIn('id="navigation-filter"', template)
        self.assertIn('class="nav-group"', template)
        self.assertIn('class="page-breadcrumb"', template)

    def test_mobile_styles_cover_small_screens_and_touch_targets(self):
        css = (ROOT / "app" / "static" / "mobile.css").read_text(encoding="utf-8")
        self.assertIn("@media(max-width:800px)", css)
        self.assertIn("@media(max-width:520px)", css)
        self.assertIn("min-height:44px", css)
        self.assertIn("overflow-x:auto", css)
        self.assertIn("safe-area-inset", css)

    def test_tutorial_has_responsive_navigation_and_steps(self):
        template = (ROOT / "app" / "templates" / "tutorial.html").read_text(encoding="utf-8")
        css = (ROOT / "app" / "static" / "tutorial.css").read_text(encoding="utf-8")
        self.assertIn('class="tutorial-toc"', template)
        self.assertIn('id="daily-routine"', template)
        self.assertIn("@media(max-width:520px)", css)
        self.assertIn("overflow-x:auto", css)

    def test_infinite_branch_button_wraps_inside_one_full_width_touch_target(self):
        css = (ROOT / "app" / "static" / "workflow.css").read_text(encoding="utf-8")
        rule = css.split('.app-sidebar .infinite-sidebar-link{', 1)[1].split('}', 1)[0]
        for declaration in ('display:flex', 'box-sizing:border-box', 'width:100%',
                            'min-width:0', 'min-height:44px', 'white-space:normal',
                            'overflow-wrap:anywhere', 'text-align:center'):
            self.assertIn(declaration, rule)
        self.assertIn('.infinite-sidebar-link:focus-visible', css)
        mobile = css.split('@media(max-width:800px){', 1)[1]
        self.assertIn('.app-sidebar .infinite-sidebar-card{display:none}', mobile)
        self.assertIn('.app-sidebar.mobile-menu-open .infinite-sidebar-card{display:grid', mobile)

    def test_mobile_menu_is_keyboard_and_resize_aware(self):
        script = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("setMobileMenu", script)
        self.assertIn("aria-expanded", script)
        self.assertIn("event.key==='Escape'", script)
        self.assertIn("innerWidth>800", script)
        self.assertIn("filterNavigation", script)
        self.assertIn("NAVIGATION_STATE_KEY", script)


if __name__ == "__main__":
    unittest.main()
