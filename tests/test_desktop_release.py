"""The release entrypoint must preserve the maintained private-data filter."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DesktopReleaseTests(unittest.TestCase):
    def test_build_uses_maintained_spec(self):
        script = (ROOT / 'build_desktop.ps1').read_text(encoding='utf-8')
        self.assertIn('PyInstaller --noconfirm --clean "Decades Tracker.spec"', script)
        self.assertNotIn('--add-data "clock_bridge;clock_bridge"', script)

    def test_private_clock_files_are_filtered_and_blocked(self):
        spec = (ROOT / 'Decades Tracker.spec').read_text(encoding='utf-8')
        for private in ('config.json', 'install_result.txt'):
            self.assertIn('clock_bridge/' + private, spec)
            for filename in ('build_desktop.ps1', 'build_installer.ps1'):
                self.assertIn('"' + private + '"', (ROOT / filename).read_text(encoding='utf-8'))
        start = spec.index('# Never distribute')
        end = spec.index('pyz =', start)
        context = {'a': type('Analysis', (), {'datas': [
            ('clock_bridge/config.json', 'a', 'DATA'),
            ('clock_bridge\\install_result.txt', 'b', 'DATA'),
            ('clock_bridge/SeveralUDOClockSync.ts4script', 'c', 'DATA'),
        ]})()}
        exec(spec[start:end], context)
        self.assertEqual(context['a'].datas, [('clock_bridge/SeveralUDOClockSync.ts4script', 'c', 'DATA')])
