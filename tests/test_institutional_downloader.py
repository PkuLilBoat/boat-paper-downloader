import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

import institutional_downloader


class InstitutionalResolverQueueTests(TestCase):
    def test_build_institutional_resolver_queue_preserves_stage_order_and_deduplicates(self):
        html = '''
        <html><head><meta name="citation_pdf_url" content="https://publisher.test/article.pdf" /></head>
        <body>
          <a href="/article.pdf">PDF</a>
          <a href="/supplement.pdf">Supplement</a>
        </body></html>
        '''
        def fake_handler(html, final_url, doi):
            return [
                'https://publisher.test/article.pdf',
                'https://publisher.test/publisher-specific.pdf',
            ]

        with patch.object(
            institutional_downloader,
            'PUBLISHER_HANDLERS',
            [(institutional_downloader.re.compile(r'publisher\.test'), fake_handler)],
        ):
            queue = institutional_downloader.build_institutional_resolver_queue(
                doi='10.1000/test',
                final_url='https://publisher.test/article',
                html=html,
                client=None,
            )

        self.assertEqual(queue[0].stage, 'institutional_meta')
        self.assertIn('publisher_handler', [item.stage for item in queue])
        self.assertIn('generic_html', [item.stage for item in queue])
        self.assertEqual(queue[0].url, 'https://publisher.test/article.pdf')
        self.assertEqual(queue[1].url, 'https://publisher.test/publisher-specific.pdf')
        self.assertEqual(len([item for item in queue if item.url == 'https://publisher.test/article.pdf']), 1)

    def test_execute_resolver_queue_stops_at_first_success(self):
        queue = [
            institutional_downloader.ResolverCandidate(stage='institutional_meta', method='m1', url='https://a.test/one.pdf', referer='https://a.test'),
            institutional_downloader.ResolverCandidate(stage='publisher_handler', method='m2', url='https://a.test/two.pdf', referer='https://a.test'),
        ]

        attempted = []

        def fake_fetch(candidate, client, policy=None):
            attempted.append(candidate.url)
            if candidate.url.endswith('two.pdf'):
                return b'%PDF-1.4\n' + b'x' * 20000
            return None

        with patch.object(institutional_downloader, 'fetch_candidate_pdf', side_effect=fake_fetch):
            result = institutional_downloader.execute_resolver_queue(queue, client=None, output_path=Path('/tmp/fake.pdf'))

        self.assertTrue(result['success'])
        self.assertEqual(result['method'], 'm2')
        self.assertEqual(attempted, ['https://a.test/one.pdf', 'https://a.test/two.pdf'])

    def test_execute_resolver_queue_skips_normalized_duplicate_urls(self):
        queue = [
            institutional_downloader.ResolverCandidate(stage='generic_html', method='m1', url='https://a.test/paper.pdf?utm_source=x', referer='https://a.test'),
            institutional_downloader.ResolverCandidate(stage='generic_html', method='m1', url='https://a.test/paper.pdf?utm_campaign=y', referer='https://a.test'),
            institutional_downloader.ResolverCandidate(stage='generic_html', method='m2', url='https://a.test/final.pdf', referer='https://a.test'),
        ]
        attempted = []

        def fake_fetch(candidate, client, policy=None):
            attempted.append(candidate.url)
            if candidate.url.endswith('final.pdf'):
                return b'%PDF-1.4\n' + b'x' * 20000
            return None

        with patch.object(institutional_downloader, 'fetch_candidate_pdf', side_effect=fake_fetch):
            result = institutional_downloader.execute_resolver_queue(queue, client=None, output_path=Path('/tmp/fake.pdf'))

        self.assertTrue(result['success'])
        self.assertEqual(
            attempted,
            ['https://a.test/paper.pdf?utm_source=x', 'https://a.test/final.pdf'],
        )

    def test_execute_resolver_queue_stops_after_too_many_domain_failures(self):
        queue = [
            institutional_downloader.ResolverCandidate(stage='generic_html', method=f'm{i}', url=f'https://a.test/{i}.pdf', referer='https://a.test')
            for i in range(8)
        ]
        attempted = []

        def fake_fetch(candidate, client, policy=None):
            attempted.append(candidate.url)
            if policy:
                policy.record_request_error(candidate.url, status_code=500)
            return None

        with patch.object(institutional_downloader, 'fetch_candidate_pdf', side_effect=fake_fetch):
            result = institutional_downloader.execute_resolver_queue(queue, client=None, output_path=Path('/tmp/fake.pdf'))

        self.assertFalse(result['success'])
        self.assertLess(len(attempted), len(queue))

    def test_download_paper_zotero_style_prefers_open_access_before_institutional(self):
        with patch.object(
            institutional_downloader,
            'try_open_access',
            return_value={'success': True, 'method': 'oa', 'file_path': '/tmp/oa.pdf'},
        ) as oa_mock, patch.object(
            institutional_downloader,
            'try_institutional_access',
            return_value={'success': True, 'method': 'institutional_direct', 'file_path': '/tmp/inst.pdf'},
        ) as inst_mock:
            result = institutional_downloader.download_paper_zotero_style('10.1000/test', '/tmp')

        self.assertTrue(result['success'])
        self.assertEqual(result['method'], 'oa')
        oa_mock.assert_called_once()
        inst_mock.assert_not_called()
