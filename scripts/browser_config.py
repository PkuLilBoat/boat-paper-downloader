"""Shared browser configuration helpers for the paper-downloader skill."""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any


WINDOWS_BROWSER_CANDIDATES = [
    ("msedge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ("msedge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
]
MAC_BROWSER_CANDIDATES = [
    ("msedge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium"),
]
LINUX_BROWSER_CANDIDATES = [
    ("msedge", "/usr/bin/microsoft-edge"),
    ("chrome", "/usr/bin/google-chrome"),
    ("chromium", "/usr/bin/chromium"),
    ("chromium", "/usr/bin/chromium-browser"),
]


def get_browser_candidates() -> list[tuple[str, str]]:
    """Return platform-specific browser channel/path candidates in preference order."""
    system = platform.system()
    if system == "Windows":
        return WINDOWS_BROWSER_CANDIDATES
    elif system == "Darwin":
        return MAC_BROWSER_CANDIDATES
    return LINUX_BROWSER_CANDIDATES


def detect_browser_channel() -> str | None:
    """Return the first Playwright channel that appears to be installed locally."""
    for channel, candidate in get_browser_candidates():
        if os.path.isfile(candidate):
            return channel
        if platform.system() == "Linux":
            executable_name = Path(candidate).name
            if shutil.which(executable_name):
                return channel
    return None


def detect_browser_executable() -> str | None:
    """Return a reasonable browser executable if one is installed, else None."""
    for _, candidate in get_browser_candidates():
        if os.path.isfile(candidate):
            return candidate
        if platform.system() == "Linux":
            executable_name = Path(candidate).name
            resolved = shutil.which(executable_name)
            if resolved:
                return resolved
    return None


def iter_browser_launch_candidates(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build launch options, respecting explicit browser preferences before bundled Chromium."""
    headless = config.get("headless", False)
    base = {"headless": headless}
    explicit_channel = config.get("browser_channel")
    explicit_executable = config.get("browser_executable")
    auto_channel = detect_browser_channel() if not explicit_channel else None
    auto_executable = detect_browser_executable() if not explicit_executable else None

    ordered_options: list[dict[str, Any]] = []
    if explicit_channel:
        ordered_options.append({"channel": explicit_channel})
    if explicit_executable:
        ordered_options.append({"executable_path": explicit_executable})
    ordered_options.append({})
    if not explicit_channel and auto_channel:
        ordered_options.append({"channel": auto_channel})
    if not explicit_executable and auto_executable:
        ordered_options.append({"executable_path": auto_executable})

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, Any], ...]] = set()

    for option in ordered_options:
        value = next(iter(option.values())) if option else True
        if option and not value:
            continue
        candidate = {**base, **option}
        key = tuple(sorted(candidate.items()))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)

    return candidates


def describe_launch_option(option: dict[str, Any]) -> str:
    """Human-friendly description for logging which browser launch path is used."""
    if option.get("channel"):
        return f"channel={option['channel']}"
    if option.get("executable_path"):
        return f"executable={option['executable_path']}"
    return "bundled chromium"


def default_browser_config() -> dict[str, Any]:
    """Return cross-platform defaults while keeping Playwright Chromium as first choice."""
    return {
        "browser_executable": None,
        "browser_channel": None,
        "headless": True,
        "timeout": 90000,
        "max_retries": 2,
        "concurrency": 3,
        "http_timeout": 12,
        "browser_concurrency": 1,
        "convert_to_markdown": True,
        "skip_existing": True,
    }


def load_config_file(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))
