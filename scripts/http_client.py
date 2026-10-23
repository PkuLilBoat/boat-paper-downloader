import importlib.util
import os
import platform
import re
import socket
import subprocess
from urllib.parse import urlparse

import httpx


_PROXY_ENV_KEYS = (
    "ALL_PROXY",
    "all_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "SURGE_HTTP_PROXY",
    "SURGE_HTTPS_PROXY",
    "SURGE_SOCKS_PROXY",
)

_HTTPX_PROXY_ENV_KEYS = (
    "ALL_PROXY",
    "all_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
)


def _env_uses_socks_proxy(env: dict[str, str] | None = None) -> bool:
    effective_env = env or os.environ
    for key in _PROXY_ENV_KEYS:
        value = str(effective_env.get(key, "")).strip().lower()
        if value.startswith("socks"):
            return True
    return False


def should_bypass_env_proxy(env: dict[str, str] | None = None) -> bool:
    if not _env_uses_socks_proxy(env):
        return False
    return importlib.util.find_spec("socksio") is None


def should_trust_env_proxy(env: dict[str, str] | None = None) -> bool:
    effective_env = env or os.environ
    value = str(effective_env.get("PAPER_DOWNLOADER_TRUST_ENV", "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def environment_proxy_usable(env: dict[str, str] | None = None) -> bool:
    """Return whether httpx can use an already-configured environment proxy."""
    effective_env = env if env is not None else os.environ
    if not any(str(effective_env.get(key, "")).strip() for key in _HTTPX_PROXY_ENV_KEYS):
        return False
    return not should_bypass_env_proxy(effective_env)


def get_macos_system_proxy() -> str | None:
    """Read an already-enabled macOS HTTPS proxy without starting any app."""
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["scutil", "--proxy"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout
    enabled = re.search(r"^\s*HTTPSEnable\s*:\s*1\s*$", text, re.MULTILINE)
    host = re.search(r"^\s*HTTPSProxy\s*:\s*(\S+)\s*$", text, re.MULTILINE)
    port = re.search(r"^\s*HTTPSPort\s*:\s*(\d+)\s*$", text, re.MULTILINE)
    if not (enabled and host and port):
        return None
    return f"http://{host.group(1)}:{port.group(1)}"


def system_proxy_usable(proxy_url: str | None = None) -> bool:
    """Check that the configured proxy endpoint is listening, without opening UI."""
    effective_url = proxy_url or get_macos_system_proxy()
    if not effective_url:
        return False
    parsed = urlparse(effective_url)
    if not parsed.hostname or not parsed.port:
        return False
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=0.25):
            return True
    except OSError:
        return False


def build_httpx_client(
    *,
    headers: dict,
    follow_redirects: bool = True,
    timeout: int = 30,
    network_route: str | None = None,
) -> httpx.Client:
    kwargs = {
        "headers": headers,
        "follow_redirects": follow_redirects,
        "timeout": timeout,
        "trust_env": False,
    }
    if network_route == "environment":
        if not environment_proxy_usable():
            raise RuntimeError("Configured environment proxy is no longer usable")
        kwargs["trust_env"] = True
    elif network_route == "system":
        proxy_url = get_macos_system_proxy()
        if not proxy_url or not system_proxy_usable(proxy_url):
            raise RuntimeError("Configured macOS system proxy is no longer usable")
        kwargs["proxy"] = proxy_url
    elif network_route not in {None, "direct"}:
        raise ValueError(f"Unsupported network route: {network_route}")
    elif network_route is None and should_trust_env_proxy():
        kwargs["trust_env"] = not should_bypass_env_proxy()
    return httpx.Client(**kwargs)
