import asyncio
import sys
import tempfile
import time
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

import scihub_downloader


class SciHubURLTests(TestCase):
    def test_resolve_resource_url_handles_protocol_relative_urls(self):
        resolved = scihub_downloader.resolve_resource_url(
            'https://sci-hub.se/10.1000/test',
            'https://sci-hub.se',
            '//cdn.example.org/paper.pdf?download=1',
        )
        self.assertEqual(resolved, 'https://cdn.example.org/paper.pdf?download=1')


class SciHubLogTests(TestCase):
    def test_write_session_logs_creates_session_and_history_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir = Path(tmpdir)
            pdf_path = logs_dir / 'paper.pdf'
            pdf_path.write_bytes(b'%PDF-1.4\n' + b'x' * 2048)

            scihub_downloader._write_session_logs(
                [
                    {
                        'doi': '10.1000/test',
                        'title': 'Paper Title',
                        'method': 'scihub',
                        'success': True,
                        'file_path': str(pdf_path),
                    }
                ],
                logs_dir,
            )

            session_files = list(logs_dir.glob('session_*.md'))
            self.assertEqual(len(session_files), 1)
            session_text = session_files[0].read_text(encoding='utf-8')
            self.assertIn('| 10.1000/test | Paper Title | scihub | ✅ SUCCESS | 2KB |', session_text)
            history_text = (logs_dir / 'download_history.md').read_text(encoding='utf-8')
            self.assertIn('# Paper Download History', history_text)
            self.assertIn('10.1000/test', history_text)

    def test_result_manifest_is_written_atomically_as_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / 'worker-1.json'
            scihub_downloader.write_results_manifest(
                target,
                [{'doi': '10.1000/test', 'success': False, 'status': 'needs_coordinator_fallback'}],
            )

            self.assertIn('needs_coordinator_fallback', target.read_text(encoding='utf-8'))
            self.assertEqual(list(Path(tmpdir).glob('*.part')), [])


class SciHubBatchTests(IsolatedAsyncioTestCase):
    def test_build_output_path_uses_one_folder_per_paper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = scihub_downloader.build_output_path(tmpdir, '10.1000/test')
            self.assertEqual(path, Path(tmpdir) / '10_1000_test' / '10_1000_test.pdf')

    def test_download_claim_prevents_a_second_owner_until_release(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            first = scihub_downloader.acquire_download_claim(tmpdir, '10.1000/test', wait_seconds=0)
            second = scihub_downloader.acquire_download_claim(tmpdir, 'https://doi.org/10.1000/TEST', wait_seconds=0)

            self.assertIsNotNone(first)
            self.assertIsNone(second)
            scihub_downloader.release_download_claim(first)
            third = scihub_downloader.acquire_download_claim(tmpdir, '10.1000/test', wait_seconds=0)
            self.assertIsNotNone(third)
            scihub_downloader.release_download_claim(third)

    async def test_download_papers_invokes_worker_for_each_doi_and_writes_logs(self):
        captured = {}

        async def fake_download_paper(doi, output_dir, cfg):
            return {'doi': doi, 'success': True, 'method': 'existing', 'file_path': f'{output_dir}/{doi}.pdf'}

        def fake_write_session_logs(results, logs_dir):
            captured['results'] = results
            captured['logs_dir'] = logs_dir

        with patch.object(scihub_downloader, 'download_paper', side_effect=fake_download_paper), patch.object(
            scihub_downloader, '_write_session_logs', side_effect=fake_write_session_logs
        ):
            results = await scihub_downloader.download_papers(['10.a', '10.b'], '/tmp/papers', {'concurrency': 2})

        self.assertEqual([item['doi'] for item in results], ['10.a', '10.b'])
        self.assertEqual([item['doi'] for item in captured['results']], ['10.a', '10.b'])
        self.assertEqual(captured['logs_dir'].name, 'logs')

    async def test_download_papers_deduplicates_normalized_dois_before_scheduling(self):
        calls = []

        async def fake_download_paper(doi, output_dir, cfg):
            calls.append(doi)
            return {'doi': doi, 'success': True, 'method': 'existing', 'file_path': f'{output_dir}/{doi}.pdf'}

        with patch.object(scihub_downloader, 'download_paper', side_effect=fake_download_paper), patch.object(
            scihub_downloader, '_write_session_logs'
        ), patch.object(scihub_downloader, 'record_download_result'):
            results = await scihub_downloader.download_papers(
                ['10.1000/Test', ' https://doi.org/10.1000/test ', 'doi:10.2000/Other'],
                '/tmp/papers',
                {'concurrency': 3},
            )

        expected = ['10.1000/test', '10.2000/other']
        self.assertCountEqual(calls, expected)
        self.assertEqual([item['doi'] for item in results], expected)

    async def test_worker_mode_has_no_shared_registry_log_or_postprocess_writes(self):
        async def fake_download_paper(doi, output_dir, cfg):
            return {'doi': doi, 'success': True, 'method': 'existing', 'file_path': f'{output_dir}/{doi}.pdf'}

        with patch.object(scihub_downloader, 'download_paper', side_effect=fake_download_paper), patch.object(
            scihub_downloader, 'postprocess_download_results'
        ) as postprocess, patch.object(scihub_downloader, '_write_session_logs') as logs, patch.object(
            scihub_downloader, 'record_download_result'
        ) as registry:
            results = await scihub_downloader.download_papers(
                ['10.1000/test'],
                '/tmp/papers',
                {'worker_mode': True},
            )

        self.assertTrue(results[0]['success'])
        postprocess.assert_not_called()
        logs.assert_not_called()
        registry.assert_not_called()

    async def test_default_coordinator_converts_pdf_once_after_download(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            flat_pdf = Path(tmpdir) / '10_1000_test.pdf'
            flat_pdf.write_bytes(b'%PDF-1.7\n' + b'x' * 12_000)

            async def fake_download_paper(doi, output_dir, cfg):
                return {'doi': doi, 'success': True, 'method': 'test', 'file_path': str(flat_pdf)}

            def fake_convert(pdf_path, md_path, **kwargs):
                md_path.write_text('# converted\n', encoding='utf-8')
                return {'status': 'converted', 'pdf_path': str(pdf_path), 'md_path': str(md_path)}

            with patch.object(scihub_downloader, 'download_paper', side_effect=fake_download_paper), patch.object(
                scihub_downloader, 'convert_pdf_to_markdown', side_effect=fake_convert
            ) as convert, patch.object(scihub_downloader, '_write_session_logs'), patch.object(
                scihub_downloader, 'record_download_result'
            ):
                results = await scihub_downloader.download_papers(['10.1000/test'], tmpdir, {})

            expected_pdf = Path(tmpdir) / '10_1000_test' / '10_1000_test.pdf'
            self.assertEqual(Path(results[0]['file_path']), expected_pdf.resolve())
            self.assertEqual(Path(results[0]['markdown_path']), expected_pdf.with_suffix('.md').resolve())
            self.assertEqual(results[0]['conversion']['status'], 'converted')
            self.assertEqual(convert.call_count, 1)

    async def test_download_papers_can_postprocess_downloaded_files(self):
        organized = []

        async def fake_download_paper(doi, output_dir, cfg):
            return {'doi': doi, 'success': True, 'method': 'existing', 'file_path': f'{output_dir}/{doi}.pdf'}

        def fake_write_session_logs(results, logs_dir):
            return None

        def fake_postprocess(results, cfg):
            organized.append((results, cfg))
            return [
                {**results[0], 'organized_path': '/tmp/paper-downloader-fixtures/Papers/2025_Chen_AI4Research_arXiv.pdf'}
            ]

        with patch.object(scihub_downloader, 'download_paper', side_effect=fake_download_paper), patch.object(
            scihub_downloader, '_write_session_logs', side_effect=fake_write_session_logs
        ), patch.object(scihub_downloader, 'postprocess_download_results', side_effect=fake_postprocess):
            results = await scihub_downloader.download_papers(
                ['10.a'],
                '/tmp/papers',
                {
                    'postprocess': {
                        'enabled': True,
                        'destination_dir': '/tmp/paper-downloader-fixtures/Papers',
                        'finder_tags': ['AI在学术上的应用'],
                    }
                },
            )

        self.assertEqual(results[0]['organized_path'], '/tmp/paper-downloader-fixtures/Papers/2025_Chen_AI4Research_arXiv.pdf')
        self.assertEqual(organized[0][1]['postprocess']['destination_dir'], '/tmp/paper-downloader-fixtures/Papers')


class SciHubCliTests(TestCase):
    def test_parse_args_converts_to_markdown_by_default(self):
        with patch.object(sys, 'argv', ['scihub_downloader.py', '/tmp/papers', '10.1000/test']):
            args = scihub_downloader.parse_args()
        self.assertTrue(args.convert_to_markdown)

    def test_parse_args_supports_explicit_no_convert(self):
        with patch.object(
            sys,
            'argv',
            ['scihub_downloader.py', '/tmp/papers', '10.1000/test', '--no-convert'],
        ):
            args = scihub_downloader.parse_args()
        self.assertFalse(args.convert_to_markdown)
    def test_parse_args_keeps_download_and_library_write_decoupled_by_default(self):
        with patch.object(
            sys,
            'argv',
            ['scihub_downloader.py', '/tmp/papers', '10.1000/test'],
        ):
            args = scihub_downloader.parse_args()

        self.assertFalse(args.organize_after_download)

    def test_parse_args_supports_disabling_default_postprocess(self):
        with patch.object(
            sys,
            'argv',
            ['scihub_downloader.py', '/tmp/papers', '10.1000/test', '--no-organize-after-download'],
        ):
            args = scihub_downloader.parse_args()

        self.assertFalse(args.organize_after_download)

    def test_parse_args_does_not_open_failed_tabs_by_default(self):
        with patch.object(
            sys,
            'argv',
            ['scihub_downloader.py', '/tmp/papers', '10.1000/test'],
        ):
            args = scihub_downloader.parse_args()

        self.assertFalse(args.persist_failed_tabs)

    def test_parse_args_supports_explicit_failed_tab_persistence(self):
        with patch.object(
            sys,
            'argv',
            ['scihub_downloader.py', '/tmp/papers', '10.1000/test', '--persist-failed-tabs'],
        ):
            args = scihub_downloader.parse_args()

        self.assertTrue(args.persist_failed_tabs)

    def test_parse_args_supports_disabling_failed_tab_persistence(self):
        with patch.object(
            sys,
            'argv',
            ['scihub_downloader.py', '/tmp/papers', '10.1000/test', '--no-persist-failed-tabs'],
        ):
            args = scihub_downloader.parse_args()

        self.assertFalse(args.persist_failed_tabs)

    def test_parse_args_supports_isolated_subagent_worker_mode(self):
        with patch.object(
            sys,
            'argv',
            ['scihub_downloader.py', '/tmp/papers', '10.1000/test', '--worker-mode'],
        ):
            args = scihub_downloader.parse_args()

        self.assertTrue(args.worker_mode)

    def test_parse_args_supports_manifest_and_fallback_only_coordinator(self):
        with patch.object(
            sys,
            'argv',
            [
                'scihub_downloader.py', '/tmp/papers', '10.1000/test',
                '--fallback-only', '--result-json', '/tmp/result.json',
            ],
        ):
            args = scihub_downloader.parse_args()

        self.assertTrue(args.fallback_only)
        self.assertEqual(args.result_json, '/tmp/result.json')

    def test_worker_http_exhaustion_is_a_successful_worker_exit(self):
        results = [{'success': False, 'status': 'needs_coordinator_fallback'}]
        self.assertEqual(scihub_downloader.results_exit_code(results, worker_mode=True), 0)
        self.assertEqual(scihub_downloader.results_exit_code(results, worker_mode=False), 1)
        self.assertEqual(scihub_downloader.results_exit_code([], worker_mode=False), 2)


class SciHubPipelineTests(IsolatedAsyncioTestCase):
    def test_ivanti_preflight_is_disabled_by_default(self):
        with patch.object(scihub_downloader.subprocess, 'run') as run:
            scihub_downloader.maybe_run_ivanti_preflight({})
        run.assert_not_called()

    def test_ivanti_check_does_not_open_app_without_explicit_opt_in(self):
        with patch.object(scihub_downloader.subprocess, 'run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = ''
            run.return_value.stderr = ''
            scihub_downloader.maybe_run_ivanti_preflight(
                {'ivanti_preflight': {'enabled': True, 'open_if_needed': False}}
            )
        command = run.call_args.args[0]
        self.assertNotIn('--open-if-needed', command)

    async def test_download_paper_uses_oa_then_institution_then_playwright_then_scihub(self):
        call_order = []

        def fake_oa(*args, **kwargs):
            call_order.append('oa')
            return {'success': False, 'method': 'oa_stage', 'manual_url': 'https://doi.org/10.1000/test'}

        def fake_inst(*args, **kwargs):
            call_order.append('institutional_http')
            return {'success': False, 'method': 'institutional', 'manual_url': 'https://publisher.test/article'}

        async def fake_playwright(*args, **kwargs):
            call_order.append('institutional_playwright')
            return {'success': False, 'method': 'institutional_playwright', 'manual_url': 'https://publisher.test/article'}

        async def fake_scihub(*args, **kwargs):
            call_order.append('scihub')
            return {'success': True, 'file_path': '/tmp/test.pdf', 'manual_url': 'https://sci-hub.ren/10.1000/test'}

        with patch.object(scihub_downloader, '_HAS_OA_AND_INSTITUTIONAL', True), patch.object(
            scihub_downloader, '_HAS_PLAYWRIGHT_INSTITUTIONAL', True
        ), patch.object(scihub_downloader, 'try_open_access', side_effect=fake_oa), patch.object(
            scihub_downloader, 'try_institutional_access', side_effect=fake_inst
        ), patch.object(
            scihub_downloader, 'download_institutional_playwright', side_effect=fake_playwright
        ), patch.object(
            scihub_downloader, 'download_single_paper', side_effect=fake_scihub
        ), patch.object(
            scihub_downloader, 'environment_proxy_usable', return_value=False
        ), patch.object(
            scihub_downloader, 'system_proxy_usable', return_value=False
        ), patch.object(
            scihub_downloader, 'maybe_run_ivanti_preflight'
        ) as ivanti_preflight:
            result = await scihub_downloader.download_paper('10.1000/test', '/tmp/papers', {})

        self.assertTrue(result['success'])
        self.assertEqual(result['method'], 'scihub')
        self.assertEqual(call_order, ['oa', 'institutional_http', 'institutional_playwright', 'scihub'])
        ivanti_preflight.assert_not_called()

    async def test_fast_http_routes_run_in_parallel_and_publish_one_atomic_winner(self):
        started = set()

        def fake_oa(doi, output_path, timeout=30, network_route='direct', cancel_event=None):
            started.add('oa')
            deadline = time.monotonic() + 1
            while 'institutional' not in started and time.monotonic() < deadline:
                time.sleep(0.005)
            output_path.write_bytes(b'%PDF-1.7\n' + b'o' * 12_000)
            return {'success': True, 'method': 'oa_test', 'file_path': str(output_path)}

        def fake_institutional(doi, output_path, timeout=30, network_route='direct', cancel_event=None):
            started.add('institutional')
            deadline = time.monotonic() + 1
            while cancel_event is not None and not cancel_event.is_set() and time.monotonic() < deadline:
                time.sleep(0.005)
            return {'success': False, 'method': 'institutional'}

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            scihub_downloader, 'try_open_access', side_effect=fake_oa
        ), patch.object(scihub_downloader, 'try_institutional_access', side_effect=fake_institutional):
            destination = Path(tmpdir) / 'paper.pdf'
            result = await scihub_downloader.run_parallel_http_routes(
                '10.1000/test', destination, timeout=10, network_route='direct'
            )

            self.assertTrue(result['success'])
            self.assertEqual(result['method'], 'oa_test')
            self.assertEqual(destination.read_bytes()[:4], b'%PDF')
            self.assertEqual(started, {'oa', 'institutional'})
            self.assertEqual(list(Path(tmpdir).glob('*.part')), [])

    async def test_download_paper_tries_direct_then_configured_environment_route_without_ivanti(self):
        routes = []

        async def fake_fast_routes(doi, output_path, timeout, network_route):
            routes.append(network_route)
            if network_route == 'environment':
                output_path.write_bytes(b'%PDF-1.7\n' + b'x' * 12_000)
                return {'success': True, 'method': 'institutional_environment', 'file_path': str(output_path)}
            return {'success': False, 'method': 'http_fast_lane', 'manual_url': f'https://doi.org/{doi}'}

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            scihub_downloader, '_HAS_OA_AND_INSTITUTIONAL', True
        ), patch.object(scihub_downloader, 'run_parallel_http_routes', side_effect=fake_fast_routes), patch.object(
            scihub_downloader, 'environment_proxy_usable', return_value=True
        ), patch.object(scihub_downloader, 'download_institutional_playwright') as playwright, patch.object(
            scihub_downloader, 'maybe_run_ivanti_preflight'
        ) as ivanti_preflight:
            result = await scihub_downloader.download_paper('10.1000/test', tmpdir, {})

        self.assertTrue(result['success'])
        self.assertEqual(routes, ['direct', 'environment'])
        playwright.assert_not_called()
        ivanti_preflight.assert_not_called()

    async def test_worker_mode_stops_before_browser_fallback(self):
        async def failed_fast_routes(*args, **kwargs):
            return {'success': False, 'method': 'http_fast_lane'}

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            scihub_downloader, '_HAS_OA_AND_INSTITUTIONAL', True
        ), patch.object(scihub_downloader, 'run_parallel_http_routes', side_effect=failed_fast_routes), patch.object(
            scihub_downloader, 'environment_proxy_usable', return_value=False
        ), patch.object(scihub_downloader, 'system_proxy_usable', return_value=False), patch.object(
            scihub_downloader, 'download_institutional_playwright'
        ) as playwright, patch.object(scihub_downloader, 'download_single_paper') as scihub:
            result = await scihub_downloader.download_paper(
                '10.1000/test', tmpdir, {'worker_mode': True}
            )

        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'needs_coordinator_fallback')
        playwright.assert_not_called()
        scihub.assert_not_called()

    async def test_fallback_only_skips_http_and_uses_bounded_browser_stage(self):
        async def fake_playwright(*args, **kwargs):
            return {'success': True, 'method': 'institutional_playwright', 'file_path': '/tmp/test.pdf'}

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            scihub_downloader, 'run_parallel_http_routes'
        ) as http_routes, patch.object(
            scihub_downloader, 'download_institutional_playwright', side_effect=fake_playwright
        ):
            result = await scihub_downloader.download_paper(
                '10.1000/test', tmpdir, {'fallback_only': True}
            )

        self.assertTrue(result['success'])
        http_routes.assert_not_called()

    def test_scihub_uses_single_default_mirror(self):
        self.assertEqual(scihub_downloader.SCIHUB_MIRRORS, ['https://sci-hub.ren'])
