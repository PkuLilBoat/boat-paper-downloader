"""Zotero-inspired request controls for full-text discovery.

The policy keeps publisher/OA probing polite and deterministic:
deduplicate normalized URLs, cap resolver output, honor Retry-After, and stop
hammering a domain after repeated failures.
"""

from __future__ import annotations

import email.utils
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

MAX_URLS_PER_RESOLVER = 6


def normalize_url_for_retry(url: str) -> str:
    parsed = urlparse(url)
    kept_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key == "download"
    ]
    query = urlencode(sorted(kept_query))
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.params,
            query,
            "",
        )
    )


def limit_candidate_entries(
    candidates: list[dict],
    max_urls: int = MAX_URLS_PER_RESOLVER,
) -> list[dict]:
    limited: list[dict] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = candidate.get("url")
        if not url:
            continue
        normalized = normalize_url_for_retry(url)
        if normalized in seen:
            continue
        seen.add(normalized)
        limited.append(candidate)
        if len(limited) >= max_urls:
            break
    return limited


@dataclass
class _DomainState:
    next_request_at: float = 0
    consecutive_failures: int = 0


class ZoteroRequestPolicy:
    def __init__(
        self,
        same_domain_delay_seconds: float = 1.0,
        max_consecutive_failures: int = 5,
        max_retry_after_seconds: int = 3600,
    ) -> None:
        self.same_domain_delay_seconds = same_domain_delay_seconds
        self.max_consecutive_failures = max_consecutive_failures
        self.max_retry_after_seconds = max_retry_after_seconds
        self._seen_urls: set[str] = set()
        self._domains: dict[str, _DomainState] = {}

    def mark_seen(self, url: str) -> bool:
        normalized = normalize_url_for_retry(url)
        if normalized in self._seen_urls:
            return True
        self._seen_urls.add(normalized)
        return False

    def can_request(self, url: str) -> bool:
        state = self._domain_state(url)
        return state.consecutive_failures <= self.max_consecutive_failures

    def seconds_until_allowed(self, url: str, now: float | None = None) -> float:
        now = time.time() if now is None else now
        state = self._domain_state(url)
        return max(0.0, state.next_request_at - now)

    def record_request_start(self, url: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        state = self._domain_state(url)
        state.next_request_at = max(state.next_request_at, now + self.same_domain_delay_seconds)

    def record_request_success(self, url: str) -> None:
        self._domain_state(url).consecutive_failures = 0

    def record_request_error(
        self,
        url: str,
        status_code: int | None = None,
        retry_after: str | None = None,
        now: float | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        state = self._domain_state(url)
        state.consecutive_failures += 1

        if status_code in {429, 503} and retry_after:
            retry_at = self._parse_retry_after(retry_after, now)
            if retry_at is None:
                return False
            if retry_at - now > self.max_retry_after_seconds:
                return False
            state.next_request_at = max(state.next_request_at, retry_at)
            return True

        if status_code == 429 or (status_code is not None and 500 <= status_code <= 599):
            state.next_request_at = max(state.next_request_at, now + 10)
            return True

        return False

    def _domain_state(self, url: str) -> _DomainState:
        domain = urlparse(url).netloc.lower()
        if domain not in self._domains:
            self._domains[domain] = _DomainState()
        return self._domains[domain]

    def _parse_retry_after(self, retry_after: str, now: float) -> float | None:
        value = retry_after.strip()
        if value.isdigit():
            return now + int(value)
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        return parsed.timestamp()
