"""Legal open-access resolution stage for DOI-based paper downloads."""

from __future__ import annotations

import re
import threading
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

try:
    from http_client import build_httpx_client
    from zotero_request_policy import ZoteroRequestPolicy, limit_candidate_entries
except ImportError:  # pragma: no cover - direct package fallback
    from .http_client import build_httpx_client  # type: ignore
    from .zotero_request_policy import ZoteroRequestPolicy, limit_candidate_entries  # type: ignore

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
}

UNPAYWALL_API = "https://api.unpaywall.org/v2/{doi}?email=zotero@zotero.org"
SEMANTIC_SCHOLAR_API = (
    "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    "?fields=openAccessPdf,externalIds,title,year"
)
PMC_ID_CONVERTER_API = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
DOI_RESOLVER_URL = "https://doi.org/{doi}"
_CANCEL_STATE = threading.local()


def _cancelled() -> bool:
    event = getattr(_CANCEL_STATE, "event", None)
    return bool(event and event.is_set())


def _is_pdf(content: bytes) -> bool:
    return content[:4] == b"%PDF"


def _collect_pdf_candidates(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    selectors = [
        ('meta[name="citation_pdf_url"]', 'content'),
        ('a[href$=".pdf"]', 'href'),
        ('a[href*="/pdf/"]', 'href'),
        ('a[href*="download"]', 'href'),
        ('embed[type="application/pdf"]', 'src'),
        ('iframe[src*=".pdf"]', 'src'),
    ]
    seen: set[str] = set()
    results: list[str] = []
    for selector, attr in selectors:
        try:
            for elem in soup.select(selector):
                value = (elem.get(attr) or "").strip()
                if not value:
                    continue
                resolved = urljoin(base_url, value)
                if resolved in seen:
                    continue
                seen.add(resolved)
                results.append(resolved)
        except Exception:
            continue
    return results


def _download_first_pdf_candidate(
    client: httpx.Client,
    candidates: list[dict[str, Any]],
    output_path,
    source: str,
) -> dict[str, Any] | None:
    policy = ZoteroRequestPolicy(same_domain_delay_seconds=0)
    for entry in limit_candidate_entries(candidates):
        if _cancelled():
            return None
        pdf_url = entry.get("url")
        if not pdf_url:
            continue
        if policy.mark_seen(pdf_url) or not policy.can_request(pdf_url):
            continue
        try:
            policy.record_request_start(pdf_url)
            resp = client.get(pdf_url)
            if resp.status_code != 200:
                policy.record_request_error(
                    pdf_url,
                    status_code=resp.status_code,
                    retry_after=resp.headers.get("retry-after"),
                )
                continue
            policy.record_request_success(pdf_url)
            content_type = resp.headers.get("content-type", "")
            if "application/pdf" in content_type or _is_pdf(resp.content):
                output_path.write_bytes(resp.content)
                return {
                    "success": True,
                    "method": source,
                    "source_stage": "oa",
                    "file_path": str(output_path),
                    "manual_url": pdf_url,
                    "license": entry.get("license"),
                    "metadata": entry.get("metadata", {}),
                }
            if "text/html" in content_type:
                nested_candidates = [
                    {"url": nested, "metadata": entry.get("metadata", {})}
                    for nested in _collect_pdf_candidates(resp.text, pdf_url)
                ]
                for nested_entry in limit_candidate_entries(nested_candidates, max_urls=3):
                    if _cancelled():
                        return None
                    nested = nested_entry["url"]
                    if policy.mark_seen(nested) or not policy.can_request(nested):
                        continue
                    policy.record_request_start(nested)
                    nested_resp = client.get(nested)
                    if nested_resp.status_code != 200:
                        policy.record_request_error(
                            nested,
                            status_code=nested_resp.status_code,
                            retry_after=nested_resp.headers.get("retry-after"),
                        )
                        continue
                    policy.record_request_success(nested)
                    if _is_pdf(nested_resp.content):
                        output_path.write_bytes(nested_resp.content)
                        return {
                            "success": True,
                            "method": f"{source}_page",
                            "source_stage": "oa",
                            "file_path": str(output_path),
                            "manual_url": pdf_url,
                            "license": entry.get("license"),
                            "metadata": entry.get("metadata", {}),
                        }
        except Exception:
            policy.record_request_error(pdf_url)
            continue
    return None


def try_unpaywall(doi: str, client: httpx.Client, output_path) -> dict[str, Any] | None:
    resp = client.get(UNPAYWALL_API.format(doi=quote(doi, safe="")))
    if resp.status_code != 200:
        return None
    data = resp.json()
    locations = data.get("oa_locations", [])
    candidates = []
    for loc in locations:
        url = loc.get("url_for_pdf") or loc.get("url")
        if not url:
            continue
        candidates.append(
            {
                "url": url,
                "license": loc.get("license"),
                "metadata": {
                    "version": loc.get("version"),
                    "host_type": loc.get("host_type"),
                    "evidence": loc.get("evidence"),
                },
            }
        )
    return _download_first_pdf_candidate(client, candidates, output_path, "oa_unpaywall")


def try_semantic_scholar(doi: str, client: httpx.Client, output_path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    resp = client.get(SEMANTIC_SCHOLAR_API.format(doi=quote(doi, safe="")))
    if resp.status_code != 200:
        return None, {}
    data = resp.json()
    oa_pdf = data.get("openAccessPdf") or {}
    candidates = []
    if oa_pdf.get("url"):
        candidates.append(
            {
                "url": oa_pdf["url"],
                "license": oa_pdf.get("license"),
                "metadata": {
                    "status": oa_pdf.get("status"),
                    "title": data.get("title"),
                    "year": data.get("year"),
                    "externalIds": data.get("externalIds", {}),
                },
            }
        )
    result = _download_first_pdf_candidate(client, candidates, output_path, "oa_semantic_scholar")
    return result, data


_ARXIV_PAT = re.compile(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)")


def _extract_arxiv_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = _ARXIV_PAT.search(url)
    if not match:
        return None
    arxiv_id = match.group(1)
    if arxiv_id.endswith('.pdf'):
        arxiv_id = arxiv_id[:-4]
    return arxiv_id


def try_arxiv(doi: str, client: httpx.Client, output_path, semantic_data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    arxiv_id = None
    if semantic_data:
        external_ids = semantic_data.get("externalIds") or {}
        arxiv_id = external_ids.get("ArXiv") or external_ids.get("Arxiv")
    if not arxiv_id:
        try:
            resp = client.get(DOI_RESOLVER_URL.format(doi=doi), follow_redirects=True)
            arxiv_id = _extract_arxiv_id_from_url(str(resp.url))
        except Exception:
            arxiv_id = None
    if not arxiv_id:
        return None
    candidates = [{"url": f"https://arxiv.org/pdf/{arxiv_id}.pdf", "metadata": {"arxiv_id": arxiv_id}}]
    return _download_first_pdf_candidate(client, candidates, output_path, "oa_arxiv")


def _normalize_pmcid(value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    if value.upper().startswith("PMC"):
        return value.upper()
    if value.isdigit():
        return f"PMC{value}"
    return None


def try_pubmed_central(doi: str, client: httpx.Client, output_path, semantic_data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    pmcid = None
    if semantic_data:
        external_ids = semantic_data.get("externalIds") or {}
        pmcid = _normalize_pmcid(external_ids.get("PubMedCentral"))
    if not pmcid:
        try:
            resp = client.get(PMC_ID_CONVERTER_API, params={"ids": doi, "format": "json"})
            if resp.status_code == 200:
                records = (resp.json() or {}).get("records") or []
                if records:
                    pmcid = _normalize_pmcid(records[0].get("pmcid"))
        except Exception:
            pmcid = None
    if not pmcid:
        return None
    candidates = [
        {"url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/", "metadata": {"pmcid": pmcid}},
        {"url": f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf", "metadata": {"pmcid": pmcid}},
    ]
    return _download_first_pdf_candidate(client, candidates, output_path, "oa_pmc")


def try_biorxiv_medrxiv(doi: str, client: httpx.Client, output_path) -> dict[str, Any] | None:
    try:
        resp = client.get(DOI_RESOLVER_URL.format(doi=doi), follow_redirects=True)
    except Exception:
        return None
    final_url = str(resp.url)
    host = (urlparse(final_url).netloc or "").lower()
    if "biorxiv.org" not in host and "medrxiv.org" not in host:
        return None
    base = final_url.split("?")[0].rstrip("/")
    candidates = [
        {"url": f"{base}.full.pdf", "metadata": {"landing_url": final_url}},
        {"url": f"{base}.full.pdf?download=1", "metadata": {"landing_url": final_url}},
    ]
    return _download_first_pdf_candidate(client, candidates, output_path, "oa_preprint")


def resolve_open_access_pdf(
    doi: str,
    output_path,
    timeout: int = 30,
    network_route: str | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Run the legal OA stage before publisher / institutional / Sci-Hub fallbacks."""
    _CANCEL_STATE.event = cancel_event
    try:
        with build_httpx_client(
            headers=HEADERS,
            follow_redirects=True,
            timeout=timeout,
            network_route=network_route,
        ) as client:
            result = try_unpaywall(doi, client, output_path)
            if result:
                return result
            if _cancelled():
                return {"success": False, "method": "oa_cancelled"}

            semantic_result, semantic_data = try_semantic_scholar(doi, client, output_path)
            if semantic_result:
                return semantic_result
            if _cancelled():
                return {"success": False, "method": "oa_cancelled"}

            result = try_arxiv(doi, client, output_path, semantic_data=semantic_data)
            if result:
                return result
            if _cancelled():
                return {"success": False, "method": "oa_cancelled"}

            result = try_pubmed_central(doi, client, output_path, semantic_data=semantic_data)
            if result:
                return result
            if _cancelled():
                return {"success": False, "method": "oa_cancelled"}

            result = try_biorxiv_medrxiv(doi, client, output_path)
            if result:
                return result
    except Exception as exc:
        return {
            "success": False,
            "method": "oa_stage",
            "manual_url": f"https://doi.org/{doi}",
            "error": str(exc),
        }
    finally:
        _CANCEL_STATE.event = None

    return {
        "success": False,
        "method": "oa_stage",
        "manual_url": f"https://doi.org/{doi}",
    }
