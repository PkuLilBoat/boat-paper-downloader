import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

import institutional_playwright_downloader as ipd


class InstitutionalPlaywrightConfigTests(TestCase):
    def test_resolve_institution_login_config_merges_private_file_and_inline_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            private_path = Path(tmpdir) / 'private-login.json'
            private_path.write_text(
                json.dumps(
                    {
                        'enabled': True,
                        'login_url': 'https://sso.example.edu/login',
                        'username': 'from-file-user',
                        'password': 'from-file-pass',
                        'otp': {'enabled': True, 'prompt': 'file prompt'},
                    }
                ),
                encoding='utf-8',
            )
            cfg = {
                'institution_login_config': str(private_path),
                'institution_login': {
                    'username': 'inline-user',
                    'otp': {'prompt': 'inline prompt'},
                },
            }

            login_cfg = ipd.resolve_institution_login_config(cfg)

        self.assertIsNotNone(login_cfg)
        self.assertEqual(login_cfg['login_url'], 'https://sso.example.edu/login')
        self.assertEqual(login_cfg['username'], 'inline-user')
        self.assertEqual(login_cfg['password'], 'from-file-pass')
        self.assertTrue(login_cfg['otp']['enabled'])
        self.assertEqual(login_cfg['otp']['prompt'], 'inline prompt')
        self.assertIn('input[name="username"]', login_cfg['username_selectors'])
        self.assertIn('input[type="password"]', login_cfg['password_selectors'])

    def test_prepare_profile_runtime_uses_persistent_session_dir_without_cleanup(self):
        cfg = {'session_user_data_dir': '/tmp/paper-downloader-session'}

        with patch.object(ipd, '_copy_profile_to_temp') as copy_mock:
            runtime_dir, cleanup, profile_dir = ipd.prepare_profile_runtime(cfg)

        self.assertEqual(runtime_dir, '/tmp/paper-downloader-session')
        self.assertFalse(cleanup)
        self.assertEqual(profile_dir, 'Default')
        copy_mock.assert_not_called()

    def test_prepare_profile_runtime_copies_user_data_dir_for_temporary_session(self):
        cfg = {
            'user_data_dir': '/Users/example/Library/Application Support/Microsoft Edge',
            'profile_directory': 'Profile 3',
        }

        with patch.object(ipd, '_copy_profile_to_temp', return_value='/tmp/copied-profile') as copy_mock:
            runtime_dir, cleanup, profile_dir = ipd.prepare_profile_runtime(cfg)

        self.assertEqual(runtime_dir, '/tmp/copied-profile')
        self.assertTrue(cleanup)
        self.assertEqual(profile_dir, 'Profile 3')
        copy_mock.assert_called_once_with(cfg['user_data_dir'], 'Profile 3')

    def test_resolve_annualreviews_pdf_url_from_form_action(self):
        final_url = 'https://www.annualreviews.org/content/journals/10.1146/annurev-clinpsy-061324-074037'
        action = (
            '/deliver/fulltext/10.1146/annurev-clinpsy-061324-074037/'
            'annurev-clinpsy-061324-074037.pdf?itemId=%2Fcontent%2Fjournals%2F10.1146%2Fannurev-clinpsy-061324-074037'
            '&amp;mimeType=pdf&amp;containerItemId=content/journals/clinpsy'
        )

        pdf_url = ipd.resolve_annualreviews_pdf_url(final_url, action)

        self.assertEqual(
            pdf_url,
            'https://www.annualreviews.org/deliver/fulltext/10.1146/annurev-clinpsy-061324-074037/'
            'annurev-clinpsy-061324-074037.pdf?itemId=%2Fcontent%2Fjournals%2F10.1146%2Fannurev-clinpsy-061324-074037'
            '&mimeType=pdf&containerItemId=content/journals/clinpsy',
        )

    def test_resolve_institution_login_config_adds_short_detection_timeout_default(self):
        login_cfg = ipd.resolve_institution_login_config(
            {
                'institution_login': {
                    'enabled': True,
                    'username': 'user',
                }
            }
        )

        self.assertEqual(login_cfg['login_detection_timeout_ms'], 3000)

    def test_default_config_prefers_domcontentloaded_for_doi_navigation(self):
        self.assertEqual(ipd.DEFAULT_CONFIG['goto_wait_until'], 'domcontentloaded')
        self.assertEqual(ipd.DEFAULT_CONFIG['download_mode'], 'implicit')
        self.assertFalse(ipd.DEFAULT_CONFIG['allow_manual_browser'])
        self.assertTrue(ipd.DEFAULT_CONFIG['human_required_queue'])

    def test_detect_publisher_handler_covers_supported_site_routes(self):
        cases = {
            'https://www.annualreviews.org/content/journals/10.1146/annurev-clinpsy-061324-074037': 'annualreviews',
            'https://www.sciencedirect.com/science/article/pii/S0191886922004304': 'elsevier',
            'https://link.springer.com/article/10.1007/s10964-014-0177-9': 'springer',
            'https://onlinelibrary.wiley.com/doi/10.1111/pere.12175': 'wiley',
            'https://doi.apa.org/doi/10.1037/0022-3514.79.2.224': 'apa_psycnet',
            'https://psycnet.apa.org/record/2000-13324-001': 'apa_psycnet',
            'https://www.tandfonline.com/doi/abs/10.1080/15374410701653120': 'tandf',
            'https://journals.sagepub.com/doi/10.1177/0146167217736049': 'sage',
            'https://academic.oup.com/scan/article/15/6/642/5864690': 'oxford',
            'https://www.cambridge.org/core/journals/development-and-psychopathology/article/example/S095457941800158X': 'cambridge',
        }

        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(ipd.detect_publisher_handler(url), expected)

    def test_detect_publisher_handler_leaves_cnpereading_sage_mirror_to_generic(self):
        url = 'https://sage.cnpereading.com/paragraph/article/?doi=10.1177/0146167217736049'

        self.assertIsNone(ipd.detect_publisher_handler(url))

    def test_default_context_options_hide_headless_user_agent_marker(self):
        options = ipd.default_context_options()

        self.assertIn('user_agent', options)
        self.assertNotIn('HeadlessChrome', options['user_agent'])
        self.assertEqual(options['locale'], 'en-US')
        self.assertEqual(options['extra_http_headers']['Accept-Language'], 'en-US,en;q=0.9')

    def test_detect_access_barrier_identifies_known_challenge_pages(self):
        self.assertEqual(
            ipd.detect_access_barrier(
                title='Just a moment...',
                body_text='Performing security verification',
                html='',
            ),
            'cloudflare_challenge',
        )
        self.assertEqual(
            ipd.detect_access_barrier(
                title='ScienceDirect',
                body_text='Please confirm you are a human by completing the captcha challenge below.',
                html='',
            ),
            'captcha_challenge',
        )
        self.assertEqual(
            ipd.detect_access_barrier(
                title='',
                body_text='',
                html='Request unsuccessful. Incapsula incident ID: 123',
            ),
            'incapsula_challenge',
        )

    def test_detect_access_barrier_does_not_flag_incapsula_script_on_real_content_page(self):
        self.assertIsNone(
            ipd.detect_access_barrier(
                title='Competence in early adult romantic relationships',
                body_text='APA PsycNET Competence in early adult romantic relationships Get Access',
                html='<html><script src=\"/_Incapsula_Resource\"></script><body>APA PsycNET Get Access</body></html>',
            )
        )

    def test_manual_fallback_requires_visible_browser_session(self):
        self.assertFalse(ipd.should_offer_manual_fallback({'headless': True}, '/tmp/profile'))
        self.assertTrue(
            ipd.should_offer_manual_fallback(
                {'headless': False, 'download_mode': 'human_assisted', 'allow_manual_browser': True},
                '/tmp/profile',
            )
        )
        self.assertFalse(ipd.should_offer_manual_fallback({'headless': False}, None))

    def test_manual_fallback_stays_disabled_in_implicit_mode_even_if_browser_visible(self):
        self.assertFalse(
            ipd.should_offer_manual_fallback(
                {'headless': False, 'download_mode': 'implicit', 'allow_manual_browser': False},
                '/tmp/profile',
            )
        )
        self.assertTrue(
            ipd.should_offer_manual_fallback(
                {'headless': False, 'download_mode': 'human_assisted', 'allow_manual_browser': True},
                '/tmp/profile',
            )
        )

    def test_should_handle_otp_only_after_login_interaction(self):
        self.assertFalse(ipd.should_handle_otp(form_selector=None, interaction_happened=False))
        self.assertTrue(ipd.should_handle_otp(form_selector='#user_name', interaction_happened=False))
        self.assertTrue(ipd.should_handle_otp(form_selector=None, interaction_happened=True))

    def test_detect_access_state_identifies_apa_no_fulltext_access(self):
        self.assertEqual(
            ipd.detect_access_state(
                final_url='https://psycnet.apa.org/doiLanding?doi=10.1037%2F0022-3514.79.2.224',
                title='Competence in early adult romantic relationships',
                body_text='APA PsycNET Competence in early adult romantic relationships Get Access',
                html='<html><body>APA PsycNET Get Access</body></html>',
            ),
            'no_fulltext_access',
        )

    def test_detect_access_state_identifies_pdf_link_available(self):
        self.assertEqual(
            ipd.detect_access_state(
                final_url='https://academic.oup.com/sleepadvances/article/5/1/zpae049/7717581',
                title='Oxford Academic',
                body_text='PDF Split View Cite',
                html='<html><meta name="citation_pdf_url" content="https://academic.oup.com/article.pdf"></html>',
            ),
            'pdf_link_available',
        )

    def test_finalize_access_state_prefers_pdf_downloaded_on_success(self):
        self.assertEqual(ipd.finalize_access_state('no_fulltext_access', success=True), 'pdf_downloaded')
        self.assertEqual(ipd.finalize_access_state('pdf_link_available', success=True), 'pdf_downloaded')
        self.assertEqual(ipd.finalize_access_state('no_fulltext_access', success=False), 'no_fulltext_access')

    def test_detect_access_state_identifies_elsevier_challenge_page(self):
        self.assertEqual(
            ipd.detect_access_state(
                final_url='https://www.sciencedirect.com/science/article/pii/S0191886922002185?via%3Dihub',
                title='ScienceDirect',
                body_text='There was a problem providing the content you requested Reference number: 123 Remote access',
                html='<html><body>There was a problem providing the content you requested</body></html>',
            ),
            'anti_bot_blocked',
        )

    def test_access_requires_human_step_for_challenge_states(self):
        self.assertTrue(ipd.access_requires_human_step('cloudflare_challenge', None))
        self.assertTrue(ipd.access_requires_human_step(None, 'anti_bot_blocked'))
        self.assertFalse(ipd.access_requires_human_step(None, 'pdf_link_available'))

    def test_build_failure_result_uses_requires_human_step_and_reason(self):
        result = ipd.build_failure_result(
            doi='10.1000/test',
            method='playwright_institutional',
            manual_url='https://doi.org/10.1000/test',
            reason='human_step_required',
            requires_human_step=True,
        )

        self.assertEqual(result['doi'], '10.1000/test')
        self.assertEqual(result['reason'], 'human_step_required')
        self.assertTrue(result['requires_human_step'])

    def test_resolve_apa_download_url_joins_relative_fulltext_path(self):
        url = ipd.resolve_apa_download_url(
            'https://psycnet.apa.org/recordAccess/institutional/2000-08654-006?returnUrl=abc',
            '/fulltext/2000-08654-006.pdf?auth_id=12465&returnUrl=abc',
        )

        self.assertEqual(
            url,
            'https://psycnet.apa.org/fulltext/2000-08654-006.pdf?auth_id=12465&returnUrl=abc',
        )
