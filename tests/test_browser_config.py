import importlib
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

import browser_config


class BrowserConfigTests(TestCase):
    def test_default_browser_config_uses_headless_for_implicit_downloads(self):
        self.assertTrue(browser_config.default_browser_config()["headless"])

    def test_launch_candidates_prefer_explicit_channel_then_executable_then_bundled(self):
        config = {
            'headless': True,
            'browser_channel': 'msedge',
            'browser_executable': '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
        }
        candidates = browser_config.iter_browser_launch_candidates(config)

        self.assertEqual(
            candidates,
            [
                {'headless': True, 'channel': 'msedge'},
                {
                    'headless': True,
                    'executable_path': '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
                },
                {'headless': True},
            ],
        )

    def test_launch_candidates_keep_bundled_first_when_no_explicit_preference(self):
        config = {
            'headless': True,
            'browser_channel': None,
            'browser_executable': None,
        }
        with patch.object(browser_config, 'detect_browser_channel', return_value='msedge'), patch.object(
            browser_config, 'detect_browser_executable', return_value='/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
        ):
            candidates = browser_config.iter_browser_launch_candidates(config)

        self.assertEqual(
            candidates,
            [
                {'headless': True},
                {'headless': True, 'channel': 'msedge'},
                {
                    'headless': True,
                    'executable_path': '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
                },
            ],
        )
