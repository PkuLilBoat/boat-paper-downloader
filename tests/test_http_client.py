import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

import http_client


class HttpClientTests(TestCase):
    def test_should_bypass_env_proxy_when_socks_proxy_exists_without_socksio(self):
        env = {'ALL_PROXY': 'socks5://127.0.0.1:7890'}

        with patch.object(http_client.importlib.util, 'find_spec', return_value=None):
            self.assertTrue(http_client.should_bypass_env_proxy(env))

    def test_should_not_bypass_env_proxy_for_non_socks_proxy(self):
        env = {'HTTPS_PROXY': 'http://127.0.0.1:7890'}

        with patch.object(http_client.importlib.util, 'find_spec', return_value=None):
            self.assertFalse(http_client.should_bypass_env_proxy(env))

    def test_build_httpx_client_disables_trust_env_by_default(self):
        client = http_client.build_httpx_client(headers={'User-Agent': 'test'})

        self.assertFalse(client._trust_env)
        client.close()

    def test_build_httpx_client_can_opt_into_trusting_env(self):
        with patch.dict(http_client.os.environ, {'PAPER_DOWNLOADER_TRUST_ENV': '1'}, clear=True):
            with patch.object(http_client, 'should_bypass_env_proxy', return_value=False):
                client = http_client.build_httpx_client(headers={'User-Agent': 'test'})

        self.assertTrue(client._trust_env)
        client.close()

    def test_build_httpx_client_still_blocks_unsupported_socks_proxy_when_opted_in(self):
        with patch.dict(http_client.os.environ, {'PAPER_DOWNLOADER_TRUST_ENV': '1'}, clear=True):
            with patch.object(http_client, 'should_bypass_env_proxy', return_value=True):
                client = http_client.build_httpx_client(headers={'User-Agent': 'test'})

        self.assertFalse(client._trust_env)
        client.close()

    def test_environment_proxy_is_available_only_when_configured_and_supported(self):
        with patch.object(http_client, 'should_bypass_env_proxy', return_value=False):
            self.assertTrue(http_client.environment_proxy_usable({'HTTPS_PROXY': 'http://127.0.0.1:7890'}))
        self.assertFalse(http_client.environment_proxy_usable({}))

    def test_environment_route_can_be_selected_without_global_opt_in(self):
        with patch.dict(http_client.os.environ, {'HTTPS_PROXY': 'http://127.0.0.1:7890'}, clear=True):
            with patch.object(http_client, 'should_bypass_env_proxy', return_value=False):
                client = http_client.build_httpx_client(
                    headers={'User-Agent': 'test'},
                    network_route='environment',
                )

        self.assertTrue(client._trust_env)
        client.close()

    def test_reads_an_enabled_macos_https_proxy_without_opening_an_app(self):
        proxy_output = '''<dictionary> {
  HTTPSEnable : 1
  HTTPSPort : 6152
  HTTPSProxy : 127.0.0.1
}'''
        with patch.object(http_client.platform, 'system', return_value='Darwin'), patch.object(
            http_client.subprocess,
            'run',
        ) as run:
            run.return_value.stdout = proxy_output
            self.assertEqual(http_client.get_macos_system_proxy(), 'http://127.0.0.1:6152')

        self.assertEqual(run.call_args.args[0], ['scutil', '--proxy'])
