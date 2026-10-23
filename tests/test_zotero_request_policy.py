import sys
import time
from pathlib import Path
from unittest import TestCase

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

import zotero_request_policy


class ZoteroRequestPolicyTests(TestCase):
    def test_normalize_url_drops_tracking_query_params_but_keeps_download(self):
        normalized = zotero_request_policy.normalize_url_for_retry(
            'https://publisher.test/pdf?utm_source=x&download=1&b=2&a=1'
        )

        self.assertEqual(normalized, 'https://publisher.test/pdf?download=1')

    def test_seen_url_filter_treats_tracking_variants_as_same_url(self):
        policy = zotero_request_policy.ZoteroRequestPolicy()

        self.assertFalse(policy.mark_seen('https://publisher.test/pdf?utm_source=x'))
        self.assertTrue(policy.mark_seen('https://publisher.test/pdf?utm_campaign=y'))

    def test_limit_candidates_caps_each_resolver_to_six_unique_urls(self):
        candidates = [
            {'url': f'https://publisher.test/file-{i}.pdf'}
            for i in range(8)
        ]

        limited = zotero_request_policy.limit_candidate_entries(candidates)

        self.assertEqual(len(limited), 6)
        self.assertEqual(limited[-1]['url'], 'https://publisher.test/file-5.pdf')

    def test_request_error_applies_retry_after_and_eventual_domain_circuit_breaker(self):
        policy = zotero_request_policy.ZoteroRequestPolicy(same_domain_delay_seconds=0)
        retry_after = time.time() + 30

        should_retry = policy.record_request_error(
            'https://publisher.test/one.pdf',
            status_code=429,
            retry_after='30',
            now=retry_after - 30,
        )

        self.assertTrue(should_retry)
        self.assertGreaterEqual(policy.seconds_until_allowed('https://publisher.test/two.pdf'), 0)

        for _ in range(policy.max_consecutive_failures + 1):
            policy.record_request_error('https://publisher.test/fail.pdf', status_code=500, now=retry_after)

        self.assertFalse(policy.can_request('https://publisher.test/another.pdf'))
