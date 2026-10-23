"""
Institutional Access + Open Access PDF Downloader
Replicates Zotero's "Find Available PDF" logic in pure Python.

Priority order:
  1. Open-access resolvers (Unpaywall first, Zotero OA fallback)
  2. DOI -> publisher page via current IP (institutional/VPN access)
     Three-layer strategy based on Zotero translator source (omni.ja):
       L1: meta[name="citation_pdf_url"] (universal, Wiley /pdfdirect/ fix)
       L2: Publisher-specific handler (Wiley, APA, SAGE, Springer, T&F, Atypon)
       L3: Enhanced generic HTML parsing (all candidates, not just first)
  3. [Caller falls back to browser automation / Sci-Hub if both fail]

Source reference: zotero/chrome/content/zotero/xpcom/attachments.js
                  getFileResolvers() / downloadFirstAvailableFile()
                  translators/Wiley Online Library.js
                  translators/APA PsycNET.js
                  translators/Atypon Journals.js
                  translators/SAGE Journals.js
                  translators/Springer Link.js
                  translators/Taylor and Francis+NEJM.js
"""

import inspect
import re
import threading
from dataclasses import dataclass
import httpx
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote
from bs4 import BeautifulSoup

try:
    from http_client import build_httpx_client
    from oa_stage import resolve_open_access_pdf
    from site_strategy_registry import build_http_handler_patterns
    from zotero_request_policy import ZoteroRequestPolicy
except ImportError:  # pragma: no cover - direct script fallback
    from .http_client import build_httpx_client  # type: ignore
    from .oa_stage import resolve_open_access_pdf  # type: ignore
    from .site_strategy_registry import build_http_handler_patterns  # type: ignore
    from .zotero_request_policy import ZoteroRequestPolicy  # type: ignore

# Zotero's own OA search service (mirrors Unpaywall) - may be blocked in China
ZOTERO_OA_URL = "https://services.zotero.org/oa/search"
# Unpaywall official API - more accessible globally
UNPAYWALL_API = "https://api.unpaywall.org/v2/{doi}?email=zotero@zotero.org"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
}


def _is_pdf(content: bytes) -> bool:
    return content[:4] == b'%PDF'


@dataclass(frozen=True)
class ResolverCandidate:
    stage: str
    method: str
    url: str
    referer: str


# ---------------------------------------------------------------------------
# Publisher-specific PDF URL handlers
# Derived from Zotero translator source (omni.ja translators/)
# ---------------------------------------------------------------------------

def _handle_wiley(html: str, final_url: str, doi: str) -> list:
    """
    Wiley Online Library translator logic:
      meta[name="citation_pdf_url"] -> replace /pdf/ with /pdfdirect/
    """
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.select_one('meta[name="citation_pdf_url"]')
    if meta:
        pdf_url = meta.get("content", "").strip()
        if pdf_url:
            return [pdf_url.replace("/pdf/", "/pdfdirect/"), pdf_url]
    # Fallback: construct directly
    base = "https://onlinelibrary.wiley.com"
    return [f"{base}/doi/pdfdirect/{doi}", f"{base}/doi/pdf/{doi}"]


def _handle_apa_psycnet(html: str, final_url: str, doi: str, client=None) -> list:
    """
    APA PsycNET translator logic:
      1. Find a[href*="/fulltext"] on page
      2. POST institution API to get ringGoldId, construct fulltext URL
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    # Step 1: direct fulltext link on page
    link = soup.select_one('a[href*="/fulltext"]')
    if link:
        href = link.get("href", "").strip()
        if href:
            candidates.append(urljoin(final_url, href))

    # Step 2: institution API (replicates Zotero APA PsycNET translator)
    uid_match = re.search(r'/(?:record|fulltext)/([^/.?]+)', final_url)
    if uid_match and client:
        uid = uid_match.group(1)
        try:
            resp = client.post(
                "https://psycnet.apa.org/api/request/institution.getInstitutionByIpAddress",
                json={"api": "institution.getInstitutionByIpAddress", "params": {"uid": uid}},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                institution = data.get("institution")
                if institution and institution.get("ringGoldId"):
                    ring_id = institution["ringGoldId"]
                    pdf_url = (
                        f"https://psycnet.apa.org/fulltext/{uid}.pdf"
                        f"?auth_id={ring_id}&returnUrl={quote(final_url)}"
                    )
                    candidates.append(pdf_url)
        except Exception as e:
            print(f"  [Institutional/APA] Institution API failed: {e}")

    # Step 3: direct URL guess
    candidates.append(f"https://psycnet.apa.org/fulltext/{doi.replace('/', '-')}.pdf")
    return candidates


def _handle_atypon(html: str, final_url: str, doi: str) -> list:
    """
    Atypon Journals translator logic (covers pubs.apa.org, many others):
      Replace /doi/(abs|abstract|full|...)/ with /doi/pdf/, /doi/epdf/, /doi/pdfplus/
    """
    repl_re = re.compile(r'/doi/((?:abs|abstract|full|figure|ref|citedby|book)/)?')
    if not repl_re.search(final_url):
        return []
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for pdf_path in ['/doi/pdf/', '/doi/epdf/', '/doi/pdfplus/']:
        if soup.find('a', href=lambda h: h and pdf_path in h):
            candidate = repl_re.sub(pdf_path, final_url)
            candidates.append(candidate)
    # Always add /doi/pdf/ as last-resort even without page link
    fallback = repl_re.sub('/doi/pdf/', final_url)
    if fallback not in candidates:
        candidates.append(fallback)
    return candidates


def _handle_sage(html: str, final_url: str, doi: str) -> list:
    """SAGE Journals: construct //journals.sagepub.com/doi/pdf/{doi}"""
    parsed = urlparse(final_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return [f"{base}/doi/pdf/{doi}"]


def _handle_tandf(html: str, final_url: str, doi: str) -> list:
    """Taylor & Francis: {base}/doi/pdf/{doi}"""
    parsed = urlparse(final_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return [f"{base}/doi/pdf/{doi}"]


def _handle_springer(html: str, final_url: str, doi: str) -> list:
    """Springer Link: /content/pdf/{encoded_doi}.pdf"""
    encoded = quote(doi, safe='')
    return [f"https://link.springer.com/content/pdf/{encoded}.pdf"]


_HANDLER_NAME_MAP = {
    "wiley": _handle_wiley,
    "apa_psycnet": _handle_apa_psycnet,
    "atypon": _handle_atypon,
    "sage": _handle_sage,
    "tandf": _handle_tandf,
    "springer": _handle_springer,
}
_CANCEL_STATE = threading.local()


def _cancelled() -> bool:
    event = getattr(_CANCEL_STATE, "event", None)
    return bool(event and event.is_set())

# Domain pattern -> handler function
# Checked against the final URL after doi.org redirect
PUBLISHER_HANDLERS = [
    (pattern, _HANDLER_NAME_MAP[name])
    for pattern, name in build_http_handler_patterns()
    if name in _HANDLER_NAME_MAP
]

# Enhanced CSS selectors for generic fallback (multi-candidate)
_GENERIC_SELECTORS = [
    ('meta[name="citation_pdf_url"]',  'content'),
    ('a[href$=".pdf"]',                'href'),
    ('a[href*="/pdf/"]',               'href'),
    ('a[href*="pdf"]',                 'href'),
    ('a[href*="/full-text/"]',         'href'),
    ('a[href*="download"]',            'href'),
    ('embed[type="application/pdf"]',  'src'),
    ('iframe[src*=".pdf"]',            'src'),
    ('a[title*="PDF"]',                'href'),
    ('a[class*="pdf"]',                'href'),
]


def _collect_pdf_candidates(html: str, base_url: str) -> list:
    """
    Collect ALL PDF link candidates from an HTML page (not just first match).
    Returns deduplicated list preserving order.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    candidates = []

    def _add(url: str):
        url = url.strip()
        if url and url not in seen:
            seen.add(url)
            candidates.append(urljoin(base_url, url))

    for selector, attr in _GENERIC_SELECTORS:
        try:
            for elem in soup.select(selector):
                val = elem.get(attr, "")
                if val:
                    _add(val)
        except Exception:
            continue

    # Text-based fallback: any <a> whose text contains "PDF"
    for a in soup.find_all("a", href=True):
        if "PDF" in a.get_text(strip=True).upper():
            _add(a["href"])

    return candidates


def build_institutional_resolver_queue(
    doi: str,
    final_url: str,
    html: str,
    client: httpx.Client | None = None,
) -> list[ResolverCandidate]:
    """Create a Zotero-style ordered queue of institutional PDF candidates."""
    queue: list[ResolverCandidate] = []
    seen: set[str] = set()

    def add(stage: str, method: str, url: str | None) -> None:
        if not url:
            return
        normalized = urljoin(final_url, url.strip())
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        queue.append(
            ResolverCandidate(
                stage=stage,
                method=method,
                url=normalized,
                referer=final_url,
            )
        )

    soup_quick = BeautifulSoup(html, "html.parser")
    meta = soup_quick.select_one('meta[name="citation_pdf_url"]')
    if meta:
        raw = meta.get("content", "").strip()
        if raw:
            add("institutional_meta", "institutional_meta", raw.replace("/pdf/", "/pdfdirect/"))
            add("institutional_meta", "institutional_meta_orig", raw)

    for pattern, handler in PUBLISHER_HANDLERS:
        if pattern.search(final_url):
            label = handler.__name__
            sig = inspect.signature(handler)
            if "client" in sig.parameters:
                candidates = handler(html, final_url, doi, client=client)
            else:
                candidates = handler(html, final_url, doi)
            for pdf_url in candidates:
                add("publisher_handler", f"institutional_{label}", pdf_url)
            break

    for pdf_url in _collect_pdf_candidates(html, final_url):
        add("generic_html", "institutional_generic", pdf_url)

    return queue


def fetch_candidate_pdf(
    candidate: ResolverCandidate,
    client: httpx.Client,
    policy: ZoteroRequestPolicy | None = None,
) -> bytes | None:
    """Fetch one queued candidate URL and return bytes when it is a valid PDF."""
    try:
        if policy:
            policy.record_request_start(candidate.url)
        response = client.get(candidate.url, headers={**HEADERS, "Referer": candidate.referer})
        if response.status_code == 200:
            content = response.content
            if _is_pdf(content) and len(content) > 10_000:
                if policy:
                    policy.record_request_success(candidate.url)
                return content
            if policy:
                policy.record_request_error(candidate.url, status_code=response.status_code)
        elif policy:
            policy.record_request_error(
                candidate.url,
                status_code=response.status_code,
                retry_after=response.headers.get("retry-after"),
            )
    except Exception as exc:
        if policy:
            policy.record_request_error(candidate.url)
        print(f"  [Resolver/{candidate.stage}] Error fetching {candidate.url}: {exc}")
    return None


def execute_resolver_queue(
    queue: list[ResolverCandidate],
    client: httpx.Client,
    output_path: Path,
) -> dict:
    """Execute queued candidates until one returns a valid PDF."""
    policy = ZoteroRequestPolicy(same_domain_delay_seconds=0)
    for candidate in queue:
        if _cancelled():
            return {"success": False, "method": "institutional_cancelled"}
        if policy.mark_seen(candidate.url):
            print(f"  [Resolver/{candidate.stage}] Skipping duplicate: {candidate.url}")
            continue
        if not policy.can_request(candidate.url):
            print(f"  [Resolver/{candidate.stage}] Skipping domain after repeated failures: {candidate.url}")
            continue
        print(f"  [Resolver/{candidate.stage}] Trying: {candidate.url}")
        content = fetch_candidate_pdf(candidate, client, policy=policy)
        if content:
            output_path.write_bytes(content)
            return {
                "success": True,
                "method": candidate.method,
                "file_path": str(output_path),
            }
    return {"success": False, "method": "institutional"}


def try_institutional_access(
    doi: str,
    output_path: Path,
    timeout: int = 30,
    network_route: str | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """
    Try to download PDF via current machine's IP (institutional / VPN access).

    Three-layer strategy (mirrors Zotero translator logic):
      L1: Universal meta[name="citation_pdf_url"] + Wiley /pdfdirect/ fix
      L2: Publisher-specific handler (Wiley, APA, SAGE, Springer, T&F, Atypon)
      L3: Enhanced generic HTML parsing (all candidates, not just first)

    Each candidate validated: %PDF header + size > 10KB.
    """
    doi_url = f"https://doi.org/{doi}"
    print(f"  [Institutional] Trying {doi_url}")

    _CANCEL_STATE.event = cancel_event
    try:
        with build_httpx_client(
            headers=HEADERS,
            follow_redirects=True,
            timeout=timeout,
            network_route=network_route,
        ) as client:
            resp = client.get(doi_url)
            if _cancelled():
                return {"success": False, "method": "institutional_cancelled"}
            final_url = str(resp.url)
            content_type = resp.headers.get("content-type", "")
            print(f"  [Institutional] Landed on: {final_url}")

            # Publisher served PDF directly
            if "application/pdf" in content_type:
                if _is_pdf(resp.content) and len(resp.content) > 10_000:
                    output_path.write_bytes(resp.content)
                    return {"success": True, "method": "institutional_direct",
                            "file_path": str(output_path), "manual_url": final_url}

            if "text/html" not in content_type:
                return {"success": False, "method": "institutional", "manual_url": final_url}

            html = resp.text
            queue = build_institutional_resolver_queue(doi, final_url, html, client=client)
            print(f"  [Institutional] Built resolver queue with {len(queue)} candidate(s)")
            result = execute_resolver_queue(queue, client, output_path)
            if result["success"]:
                result["manual_url"] = final_url
                return result

    except Exception as e:
        print(f"  [Institutional] Failed: {e}")
    finally:
        _CANCEL_STATE.event = None

    return {"success": False, "method": "institutional", "manual_url": f"https://doi.org/{doi}"}


def try_open_access(
    doi: str,
    output_path: Path,
    timeout: int = 30,
    network_route: str | None = None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Run the dedicated legal open-access stage before institutional fallback."""
    print(f"  [OA] Starting legal OA stage for {doi}")
    result = resolve_open_access_pdf(
        doi,
        output_path,
        timeout=timeout,
        network_route=network_route,
        cancel_event=cancel_event,
    )
    if result.get("success"):
        print(f"  [OA] Downloaded via {result.get('method')}: {result.get('manual_url')}")
    return result


def download_paper_zotero_style(doi: str, output_dir: str) -> dict:
    """
    Main entry point. Tries OA first, then institutional access.
    Caller should fall back to Sci-Hub on failure.

    Returns:
        {
          "doi": str,
          "success": bool,
          "method": str | None,
          "file_path": str | None,
        }
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    safe_doi = re.sub(r'[/:.]', '_', doi)
    dest = output_path / f"{safe_doi}.pdf"

    # 1. Open access first: lowest friction, succeeds cleanly when available.
    result = try_open_access(doi, dest)
    if result["success"]:
        return {"doi": doi, **result}

    # 2. Institutional / VPN access as the next best automatic path.
    result = try_institutional_access(doi, dest)
    if result["success"]:
        return {"doi": doi, **result}

    return {"doi": doi, "success": False, "method": None, "file_path": None}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python institutional_downloader.py <output_dir> <doi1> [doi2] ...")
        print("Example: python institutional_downloader.py ./papers 10.1037/0022-3514.73.2.345")
        sys.exit(1)

    output_dir = sys.argv[1]
    dois = sys.argv[2:]

    results = []
    for doi in dois:
        print(f"\n{'='*50}")
        print(f"DOI: {doi}")
        r = download_paper_zotero_style(doi, output_dir)
        results.append(r)

    print(f"\n{'='*50}")
    print("SUMMARY")
    print("="*50)
    success = sum(1 for r in results if r["success"])
    print(f"Downloaded: {success}/{len(results)}")
    for r in results:
        status = f"OK [{r.get('method')}]" if r["success"] else "FAILED"
        print(f"  [{status}] {r['doi']}")
