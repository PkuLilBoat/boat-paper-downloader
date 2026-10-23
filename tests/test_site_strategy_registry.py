import sys
from pathlib import Path
from unittest import TestCase

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

import site_strategy_registry as registry


class SiteStrategyRegistryTests(TestCase):
    def test_registry_includes_expected_core_sites(self):
        entries = {entry['key']: entry for entry in registry.site_entries()}

        self.assertIn('annualreviews', entries)
        self.assertIn('elsevier', entries)
        self.assertIn('springer', entries)
        self.assertIn('wiley', entries)
        self.assertIn('apa_psycnet', entries)
        self.assertIn('tandf', entries)
        self.assertIn('sage', entries)
        self.assertIn('cambridge', entries)
        self.assertIn('oxford', entries)
        self.assertIn('cnki', entries)

    def test_build_playwright_handler_patterns_keeps_cnpereading_mirror_generic(self):
        mapping = dict(registry.build_playwright_handler_patterns())
        self.assertNotIn('sage.cnpereading.com', [pattern.pattern for pattern in mapping.keys()])

    def test_detect_site_entry_matches_known_urls(self):
        self.assertEqual(
            registry.detect_site_entry('https://doi.apa.org/doi/10.1037/0022-3514.79.2.224')['key'],
            'apa_psycnet',
        )
        self.assertEqual(
            registry.detect_site_entry('https://www.annualreviews.org/content/journals/10.1146/annurev-clinpsy-061324-074037')['key'],
            'annualreviews',
        )
        self.assertEqual(
            registry.detect_site_entry('https://kns.cnki.net/kcms2/article/abstract?v=test')['key'],
            'cnki',
        )

    def test_markdown_table_contains_expected_columns(self):
        table = registry.markdown_table()
        self.assertIn('| Site family | Hosts | HTTP handler | Playwright handler | Validation | Strategy |', table)
        self.assertIn('Annual Reviews', table)
