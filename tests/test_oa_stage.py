import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

import oa_stage


class _FakeResponse:
    def __init__(self, status_code=200, content=b'', headers=None, text=''):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.text = text


class _RecordingClient:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def get(self, url, *args, **kwargs):
        self.urls.append(url)
        return self.responses.get(url, _FakeResponse(status_code=404))


class OAStageUnitTests(TestCase):
    def test_extract_arxiv_id_from_url_supports_abs_and_pdf_urls(self):
        self.assertEqual(
            oa_stage._extract_arxiv_id_from_url('https://arxiv.org/abs/1810.04805'),
            '1810.04805',
        )
        self.assertEqual(
            oa_stage._extract_arxiv_id_from_url('https://arxiv.org/pdf/1810.04805.pdf'),
            '1810.04805',
        )

    def test_normalize_pmcid_accepts_numeric_and_prefixed_forms(self):
        self.assertEqual(oa_stage._normalize_pmcid('8371605'), 'PMC8371605')
        self.assertEqual(oa_stage._normalize_pmcid('PMC8371605'), 'PMC8371605')
        self.assertIsNone(oa_stage._normalize_pmcid('abc'))

    def test_resolve_open_access_pdf_checks_sources_in_legal_priority_order(self):
        call_order = []

        def fake_unpaywall(*args, **kwargs):
            call_order.append('unpaywall')
            return None

        def fake_semantic(*args, **kwargs):
            call_order.append('semantic')
            return None, {'externalIds': {'ArXiv': '1810.04805'}}

        def fake_arxiv(*args, **kwargs):
            call_order.append('arxiv')
            return {'success': True, 'method': 'oa_arxiv', 'file_path': '/tmp/paper.pdf'}

        with patch.object(oa_stage, 'try_unpaywall', side_effect=fake_unpaywall), patch.object(
            oa_stage,
            'try_semantic_scholar',
            side_effect=fake_semantic,
        ), patch.object(oa_stage, 'try_arxiv', side_effect=fake_arxiv), patch.object(
            oa_stage, 'try_pubmed_central'
        ) as pmc_mock, patch.object(oa_stage, 'try_biorxiv_medrxiv') as preprint_mock:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = oa_stage.resolve_open_access_pdf('10.1000/test', Path(tmpdir) / 'paper.pdf')

        self.assertTrue(result['success'])
        self.assertEqual(result['method'], 'oa_arxiv')
        self.assertEqual(call_order, ['unpaywall', 'semantic', 'arxiv'])
        pmc_mock.assert_not_called()
        preprint_mock.assert_not_called()

    def test_download_first_pdf_candidate_caps_each_resolver_to_six_unique_urls(self):
        candidates = [
            {'url': f'https://oa.test/file-{i}.pdf'}
            for i in range(7)
        ]
        responses = {
            'https://oa.test/file-6.pdf': _FakeResponse(
                content=b'%PDF-1.4\n' + b'x' * 20000,
                headers={'content-type': 'application/pdf'},
            )
        }
        client = _RecordingClient(responses)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = oa_stage._download_first_pdf_candidate(
                client,
                candidates,
                Path(tmpdir) / 'paper.pdf',
                'oa_test',
            )

        self.assertIsNone(result)
        self.assertEqual(client.urls, [f'https://oa.test/file-{i}.pdf' for i in range(6)])
