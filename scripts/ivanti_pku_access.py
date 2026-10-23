#!/usr/bin/env python3
"""Open and sanity-check the local Ivanti Secure Access PKU connection.

This script intentionally does not store or print identity verification secrets.
If Ivanti asks for an ID number, phone number, OTP, or other live verification,
complete that step in the visible Ivanti window or retrieve the value from an
approved private runtime context outside this skill.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import time
from pathlib import Path


APP_CANDIDATES = [
    "/Applications/Ivanti Secure Access.app",
    "/Applications/Pulse Secure.app",
]
PROCESS_PATTERNS = ["Ivanti Secure Access", "Pulse Secure", "dsAccessService"]
CONNECTED_TOKENS = ["connected", "已连接"]
DISCONNECTED_TOKENS = ["disconnected", "not connected", "未连接", "已断开"]


def run(command: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def find_app() -> str | None:
    for candidate in APP_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def open_app(app_path: str | None, url: str | None = None) -> bool:
    if url:
        result = run(["open", url], timeout=15)
        if result.returncode == 0:
            return True
    command = ["open", app_path] if app_path else ["open", "-a", "Ivanti Secure Access"]
    result = run(command, timeout=15)
    return result.returncode == 0


def process_running() -> bool:
    result = run(["pgrep", "-af", "|".join(PROCESS_PATTERNS)], timeout=5)
    if result.returncode == 0 and result.stdout.strip():
        return True
    # pgrep does not support regex alternation consistently across macOS
    # versions, so retry the process names individually.
    for pattern in PROCESS_PATTERNS:
        result = run(["pgrep", "-af", pattern], timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return True
    return False


def scutil_status(profile: str) -> str | None:
    result = run(["scutil", "--nc", "list"], timeout=10)
    if result.returncode != 0:
        return None
    profile_l = profile.lower()
    for line in result.stdout.splitlines():
        line_l = line.lower()
        if profile_l in line_l or "ivanti" in line_l or "pulse" in line_l:
            if "(connected)" in line_l:
                return "connected"
            if "(disconnected)" in line_l:
                return "disconnected"
    return None


def ui_text_status(profile: str) -> str | None:
    if platform.system() != "Darwin":
        return None
    script = r'''
tell application "System Events"
  if exists process "Ivanti Secure Access" then
    tell process "Ivanti Secure Access"
      set frontmost to true
      try
        return value of every static text of entire contents of window 1 as text
      on error
        try
          return name of every UI element of entire contents of window 1 as text
        on error
          return ""
        end try
      end try
    end tell
  end if
end tell
return ""
'''
    result = run(["osascript", "-e", script], timeout=10)
    if result.returncode != 0:
        return None
    text = result.stdout.lower()
    if profile.lower() not in text and "ivanti" not in text and "secure access" not in text:
        return None
    if any(token in text for token in CONNECTED_TOKENS):
        return "connected"
    if any(token in text for token in DISCONNECTED_TOKENS):
        return "disconnected"
    return None


def detect_status(profile: str) -> str:
    for detector in (scutil_status, ui_text_status):
        status = detector(profile)
        if status:
            return status
    if process_running():
        return "unknown_running"
    return "not_running"


def preflight(profile: str, open_if_needed: bool, wait_seconds: int, url: str | None = None) -> str:
    app_path = find_app()
    status = detect_status(profile)
    if status == "connected":
        return status

    if open_if_needed:
        if not open_app(app_path, url=url):
            return "open_failed"
        deadline = time.time() + max(0, wait_seconds)
        while time.time() < deadline:
            status = detect_status(profile)
            if status == "connected":
                return status
            time.sleep(1)
        return detect_status(profile)

    return status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open/check Ivanti Secure Access before PKU institutional paper downloads")
    parser.add_argument("--profile", default="PKU", help="Connection/profile label to look for in Ivanti or macOS VPN status")
    parser.add_argument("--url", default=os.environ.get("PAPER_DOWNLOADER_IVANTI_URL"), help="Optional IVN/Ivanti deep link or portal URL to open before checking status")
    parser.add_argument("--open-if-needed", action="store_true", help="Open Ivanti Secure Access if the connection is not confirmed")
    parser.add_argument("--wait-seconds", type=int, default=8, help="Seconds to wait after opening Ivanti before reporting status")
    parser.add_argument("--require-connected", action="store_true", help="Exit non-zero unless a connected status is confirmed")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if platform.system() != "Darwin":
        print("[IVN] Non-macOS platform; skipping Ivanti preflight.")
        return 0

    status = preflight(args.profile, args.open_if_needed, args.wait_seconds, url=args.url)
    if status == "connected":
        print(f"[IVN] {args.profile}: connected")
        return 0

    if status in {"unknown_running", "disconnected"}:
        print(f"[IVN] {args.profile}: {status}; visible Ivanti verification may be required.")
        print("[IVN] If prompted for ID, phone, OTP, or similar verification, complete it in Ivanti using approved private context. Do not save secrets in this skill.")
    elif status == "not_running":
        print("[IVN] Ivanti Secure Access is not running.")
    elif status == "open_failed":
        print("[IVN] Could not open Ivanti Secure Access.")
    else:
        print(f"[IVN] {args.profile}: {status}")

    return 2 if args.require_connected else 0


if __name__ == "__main__":
    sys.exit(main())
