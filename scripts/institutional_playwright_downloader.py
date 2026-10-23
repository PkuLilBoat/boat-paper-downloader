"""
Institutional Access PDF Downloader — Playwright browser backend.
For publishers that block httpx: Wiley, APA, Elsevier, T&F, SAGE, Springer,
Oxford Academic, Cambridge Core.

Priority in pipeline:
  1. institutional_downloader.py  (httpx, fast, open-access publishers)
  2. THIS FILE                    (Playwright, Cloudflare/SPA publishers)
  3. scihub_downloader.py         (Sci-Hub mirrors, last resort)
"""
import asyncio, html, json, re, shutil, tempfile, os
from pathlib import Path
from urllib.parse import quote, urlparse, urljoin
from playwright.async_api import async_playwright

try:
    from browser_config import (
        default_browser_config,
        describe_launch_option,
        iter_browser_launch_candidates,
    )
    from site_strategy_registry import build_playwright_handler_patterns
except ImportError:  # pragma: no cover - direct script fallback
    from .browser_config import (  # type: ignore
        default_browser_config,
        describe_launch_option,
        iter_browser_launch_candidates,
    )
    from .site_strategy_registry import build_playwright_handler_patterns  # type: ignore

DEFAULT_HUMAN_REQUIRED_MANIFEST = str(
    (Path(__file__).resolve().parents[1] / "logs" / "human_required_queue.json").resolve()
)


DEFAULT_CONFIG = {
    **default_browser_config(),
    "timeout": 60000,
    "js_render_wait": 6000,   # ms to wait for Angular/React SPAs
    # Optional real browser profile for institutional sessions.
    "user_data_dir": None,
    # Optional dedicated persistent session dir for institution login reuse.
    "session_user_data_dir": None,
    "profile_directory": "Default",
    "institution_login_config": None,
    "institution_login": None,
    "goto_wait_until": "domcontentloaded",
    "download_mode": "implicit",
    "allow_manual_browser": False,
    "human_required_queue": True,
    "human_required_manifest_path": DEFAULT_HUMAN_REQUIRED_MANIFEST,
    "human_required_collection": "paper_downloader_human_required",
}

DEFAULT_INSTITUTION_LOGIN = {
    "enabled": False,
    "login_url": None,
    "login_detection_timeout_ms": 3000,
    "username": None,
    "password": None,
    "username_selectors": [
        'input[name="username"]',
        'input[name="user"]',
        'input[name="email"]',
        'input[id="username"]',
        'input[id="account"]',
        'input[type="email"]',
        'input[autocomplete="username"]',
    ],
    "password_selectors": [
        'input[type="password"]',
        'input[name="password"]',
        'input[id="password"]',
        'input[autocomplete="current-password"]',
    ],
    "submit_selectors": [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("登录")',
        'button:has-text("Log in")',
        'button:has-text("Sign in")',
    ],
    "pre_login_click_selectors": [],
    "success_selectors": [],
    "login_required_selectors": [],
    "otp": {
        "enabled": True,
        "detect_selectors": [
            'input[name*="otp"]',
            'input[name*="token"]',
            'input[name*="code"]',
            'input[id*="otp"]',
            'input[id*="token"]',
            'input[id*="code"]',
            'input[autocomplete="one-time-code"]',
            'text=/令牌|验证码|动态码|OTP|token|verification code/i',
        ],
        "success_selectors": [],
        "prompt": "Detected OTP or token step. Finish it in the browser, then press Enter here to continue.",
        "timeout_ms": 300000,
        "always_prompt": False,
    },
}

DEFAULT_STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0"
)

PUBLISHER_PATTERNS = build_playwright_handler_patterns()

_GENERIC_SELECTORS = [
    ('meta[name="citation_pdf_url"]', 'content'),
    ('a[href$=".pdf"]',               'href'),
    ('a[href*="/pdf/"]',              'href'),
    ('a[href*="pdf"]',                'href'),
    ('a[href*="/full-text/"]',        'href'),
    ('a[href*="download"]',           'href'),
    ('embed[type="application/pdf"]', 'src'),
    ('iframe[src*=".pdf"]',           'src'),
    ('a[title*="PDF"]',               'href'),
    ('a[class*="pdf"]',               'href'),
]


def _is_pdf(content: bytes) -> bool:
    return len(content) > 10_000 and content[:4] == b'%PDF'


def _copy_profile_to_temp(user_data_dir: str, profile_dir: str) -> str:
    """Copy only auth-critical files from Edge profile to a temp dir.

    Copies individual files instead of the entire profile directory tree.
    This is fast (seconds vs 20-30s) and avoids locked-file crashes when
    Edge is already running — locked files are silently skipped via _safe_copy2.
    """
    tmp_dir = tempfile.mkdtemp(prefix="edge_pw_")
    src_profile = os.path.join(user_data_dir, profile_dir)
    dst_profile = os.path.join(tmp_dir, profile_dir)

    if not os.path.isdir(src_profile):
        return tmp_dir

    os.makedirs(dst_profile, exist_ok=True)

    # Files that carry authentication state and browser fingerprint
    auth_files = [
        "Cookies",
        "Login Data",
        "Local State",
        "Preferences",
        "Web Data",
        "Extension Cookies",
    ]
    for fname in auth_files:
        src_file = os.path.join(src_profile, fname)
        dst_file = os.path.join(dst_profile, fname)
        if os.path.isfile(src_file):
            _safe_copy2(src_file, dst_file)

    # Also copy Local State from user_data_dir root (browser-level fingerprint)
    local_state_src = os.path.join(user_data_dir, "Local State")
    local_state_dst = os.path.join(tmp_dir, "Local State")
    if os.path.isfile(local_state_src):
        _safe_copy2(local_state_src, local_state_dst)

    # Network/ subdirectory (contains Network/Cookies)
    src_network = os.path.join(src_profile, "Network")
    dst_network = os.path.join(dst_profile, "Network")
    if os.path.isdir(src_network):
        os.makedirs(dst_network, exist_ok=True)
        for fname in os.listdir(src_network):
            src_file = os.path.join(src_network, fname)
            dst_file = os.path.join(dst_network, fname)
            if os.path.isfile(src_file):
                _safe_copy2(src_file, dst_file)

    return tmp_dir


def _safe_copy2(src, dst):
    """Copy file, silently skipping files locked by another process."""
    try:
        shutil.copy2(src, dst)
    except (PermissionError, OSError):
        pass


def resolve_annualreviews_pdf_url(final_url: str, action: str | None) -> str | None:
    if not action:
        return None
    return urljoin(final_url, html.unescape(action))


def resolve_apa_download_url(final_url: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(final_url, html.unescape(href))


def detect_publisher_handler(final_url: str) -> str | None:
    for pattern, name in PUBLISHER_PATTERNS:
        if pattern.search(final_url):
            return name
    return None


def default_context_options() -> dict:
    return {
        "user_agent": DEFAULT_STEALTH_USER_AGENT,
        "locale": "en-US",
        "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
        "viewport": {"width": 1440, "height": 900},
    }


def detect_access_barrier(title: str, body_text: str, html: str) -> str | None:
    title_l = (title or "").lower()
    body_l = (body_text or "").lower()
    html_l = (html or "").lower()
    substantive_tokens = [
        "abstract",
        "references",
        "journal article",
        "published:",
        "article contents",
        "get access",
        "pdf",
    ]
    has_substantive_content = any(token in body_l for token in substantive_tokens)
    if "incapsula incident id" in html_l:
        return "incapsula_challenge"
    if "_incapsula_resource" in html_l and not has_substantive_content:
        return "incapsula_challenge"
    if "performing security verification" in body_l or "cloudflare" in body_l or "just a moment" in title_l:
        return "cloudflare_challenge"
    if "confirm you are a human" in body_l or "captcha challenge" in body_l or "are you a robot" in body_l:
        return "captcha_challenge"
    return None


def detect_access_state(final_url: str, title: str, body_text: str, html: str) -> str | None:
    body_l = (body_text or "").lower()
    html_l = (html or "").lower()
    final_url_l = (final_url or "").lower()
    if "sciencedirect.com" in final_url_l:
        if "problem providing the content you requested" in body_l or "reference number:" in body_l:
            return "anti_bot_blocked"
    if "psycnet.apa.org" in final_url_l:
        has_fulltext_signal = "/fulltext" in html_l or "download pdf" in body_l
        if "get access" in body_l and not has_fulltext_signal:
            return "no_fulltext_access"
    if "citation_pdf_url" in html_l or "article-pdf" in html_l:
        return "pdf_link_available"
    return None


def finalize_access_state(access_state: str | None, success: bool) -> str | None:
    if success:
        return "pdf_downloaded"
    return access_state


def access_requires_human_step(access_barrier: str | None, access_state: str | None) -> bool:
    human_barriers = {"cloudflare_challenge", "captcha_challenge", "incapsula_challenge"}
    human_states = {"anti_bot_blocked"}
    return access_barrier in human_barriers or access_state in human_states


def should_offer_manual_fallback(cfg: dict | None, runtime_user_data_dir: str | None) -> bool:
    runtime_cfg = cfg or {}
    manual_mode = runtime_cfg.get("download_mode") == "human_assisted" or runtime_cfg.get("allow_manual_browser", False)
    return bool(runtime_user_data_dir and not runtime_cfg.get("headless", False) and manual_mode)


def build_failure_result(
    *,
    doi: str,
    method: str,
    manual_url: str | None,
    reason: str,
    access_barrier: str | None = None,
    access_state: str | None = None,
    requires_human_step: bool = False,
    error: str | None = None,
    extra: dict | None = None,
) -> dict:
    result = {
        "success": False,
        "doi": doi,
        "method": method,
        "manual_url": manual_url,
        "reason": reason,
        "access_barrier": access_barrier,
        "access_state": access_state,
        "requires_human_step": requires_human_step,
    }
    if error:
        result["error"] = error
    if extra:
        result.update(extra)
    return result


def _append_json_list(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                data = data.get("results") or []
        except Exception:
            data = []
    else:
        data = []
    data.append(row)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_human_required_result(cfg: dict | None, result: dict, output_path: Path) -> None:
    runtime_cfg = cfg or {}
    if not runtime_cfg.get("human_required_queue", True):
        return
    manifest_path = Path(runtime_cfg.get("human_required_manifest_path") or DEFAULT_HUMAN_REQUIRED_MANIFEST).expanduser()
    row = {
        "doi": result.get("doi"),
        "status": "failed",
        "method": result.get("method"),
        "manual_url": result.get("manual_url"),
        "url": result.get("manual_url"),
        "notes": result.get("reason"),
        "error": result.get("error") or result.get("reason"),
        "path": str(output_path),
        "last_attempt_at": _now_iso(),
        "access_barrier": result.get("access_barrier"),
        "access_state": result.get("access_state"),
    }
    _append_json_list(manifest_path, row)
    try:
        try:
            from paper_registry import import_failure_manifest
        except ImportError:  # pragma: no cover - direct script fallback
            from .paper_registry import import_failure_manifest  # type: ignore
        import_failure_manifest(
            str(manifest_path),
            collection_name=runtime_cfg.get("human_required_collection") or "paper_downloader_human_required",
            collection_type="download_retry_queue",
        )
    except Exception as exc:
        print(f"  [PW] Human-required queue update warning: {exc}")


def _now_iso() -> str:
    return __import__("datetime").datetime.now().replace(microsecond=0).isoformat()


def should_handle_otp(form_selector: str | None, interaction_happened: bool) -> bool:
    return bool(form_selector or interaction_happened)


async def apply_stealth_defaults(context) -> None:
    await context.add_init_script(
        """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'language', {get: () => 'en-US'});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = window.chrome || { runtime: {} };
"""
    )


def _deep_merge_dicts(base: dict | None, override: dict | None) -> dict:
    merged = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_json_file(path: str | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def resolve_institution_login_config(cfg: dict | None) -> dict | None:
    runtime_cfg = cfg or {}
    file_cfg = _load_json_file(runtime_cfg.get("institution_login_config"))
    inline_cfg = runtime_cfg.get("institution_login") or {}
    merged = _deep_merge_dicts(DEFAULT_INSTITUTION_LOGIN, file_cfg)
    merged = _deep_merge_dicts(merged, inline_cfg)
    if not merged.get("enabled") and not merged.get("login_url"):
        return None
    return merged


def prepare_profile_runtime(cfg: dict | None) -> tuple[str | None, bool, str]:
    runtime_cfg = cfg or {}
    profile_dir = runtime_cfg.get("profile_directory", "Default")
    session_user_data_dir = runtime_cfg.get("session_user_data_dir")
    if session_user_data_dir:
        return str(Path(session_user_data_dir).expanduser()), False, profile_dir
    user_data_dir = runtime_cfg.get("user_data_dir")
    if user_data_dir:
        return _copy_profile_to_temp(user_data_dir, profile_dir), True, profile_dir
    return None, False, profile_dir


async def _find_first_selector(page, selectors: list[str] | None):
    for selector in selectors or []:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                return locator, selector
        except Exception:
            continue
    return None, None


async def _click_selector_list(page, selectors: list[str] | None, timeout: int):
    for selector in selectors or []:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                await locator.click(timeout=timeout)
                return selector
        except Exception:
            continue
    return None


async def _fill_selector_list(page, selectors: list[str] | None, value: str | None, timeout: int):
    if not value:
        return None
    for selector in selectors or []:
        try:
            locator = page.locator(selector).first
            if await locator.count():
                await locator.fill(value, timeout=timeout)
                return selector
        except Exception:
            continue
    return None


async def _wait_for_login_form(page, login_cfg: dict, timeout: int):
    selectors = login_cfg.get("login_required_selectors") or []
    detect_timeout = login_cfg.get("login_detection_timeout_ms", min(timeout, 3000))
    for selector in selectors:
        try:
            await page.wait_for_selector(selector, timeout=detect_timeout, state="visible")
            return selector
        except Exception:
            continue
    return None


async def _wait_for_manual_confirmation(prompt: str):
    print(prompt)
    try:
        await asyncio.get_event_loop().run_in_executor(None, input, "")
    except (KeyboardInterrupt, EOFError):
        pass


async def _handle_otp_if_needed(page, otp_cfg: dict, timeout: int):
    if not otp_cfg.get("enabled", True):
        return
    should_prompt = otp_cfg.get("always_prompt", False)
    if not should_prompt:
        locator, selector = await _find_first_selector(page, otp_cfg.get("detect_selectors"))
        if locator:
            should_prompt = True
            print(f"  [PW/Login] OTP gate detected via {selector}")
    if not should_prompt:
        return
    await _wait_for_manual_confirmation(f"\n[PW/Login] {otp_cfg.get('prompt')}")
    success_selectors = otp_cfg.get("success_selectors") or []
    for selector in success_selectors:
        try:
            await page.wait_for_selector(selector, timeout=otp_cfg.get("timeout_ms", timeout))
            return
        except Exception:
            continue
    await page.wait_for_timeout(1500)


async def maybe_run_institution_login(page, cfg: dict | None, allow_login_url_navigation: bool = True):
    login_cfg = resolve_institution_login_config(cfg)
    if not login_cfg:
        return False

    timeout = (cfg or {}).get("timeout", DEFAULT_CONFIG["timeout"])
    login_url = login_cfg.get("login_url")
    if login_url and allow_login_url_navigation:
        print(f"  [PW/Login] Opening login page: {login_url}")
        await page.goto(login_url, wait_until="load", timeout=timeout)
    elif not login_url and page.url == "about:blank":
        return False

    form_selector = await _wait_for_login_form(page, login_cfg, timeout)
    if form_selector:
        print(f"  [PW/Login] Login form ready via: {form_selector}")

    clicked = await _click_selector_list(page, login_cfg.get("pre_login_click_selectors"), timeout)
    if clicked:
        print(f"  [PW/Login] Clicked pre-login selector: {clicked}")

    username_sel = await _fill_selector_list(page, login_cfg.get("username_selectors"), login_cfg.get("username"), timeout)
    password_sel = await _fill_selector_list(page, login_cfg.get("password_selectors"), login_cfg.get("password"), timeout)
    if username_sel:
        print(f"  [PW/Login] Filled username using: {username_sel}")
    if password_sel:
        print(f"  [PW/Login] Filled password using: {password_sel}")

    submitted = await _click_selector_list(page, login_cfg.get("submit_selectors"), timeout)
    if submitted:
        print(f"  [PW/Login] Submitted via: {submitted}")
    interaction_happened = bool(clicked or username_sel or password_sel or submitted)
    if should_handle_otp(form_selector, interaction_happened):
        await _handle_otp_if_needed(page, login_cfg.get("otp", {}), timeout)

    success_selectors = login_cfg.get("success_selectors") or []
    for selector in success_selectors:
        try:
            await page.wait_for_selector(selector, timeout=timeout)
            print(f"  [PW/Login] Success confirmed via: {selector}")
            break
        except Exception:
            continue
    return bool(username_sel or password_sel or submitted or clicked)


async def _launch_browser_with_fallbacks(pw, cfg: dict, persistent_dir: str | None = None, profile_dir: str = "Default"):
    """Launch bundled Chromium first, then fall back to detected channels/executables."""
    last_error: Exception | None = None
    context_options = default_context_options()
    for launch_opts in iter_browser_launch_candidates(cfg):
        try:
            if persistent_dir:
                context = await pw.chromium.launch_persistent_context(
                    persistent_dir,
                    accept_downloads=True,
                    args=[f"--profile-directory={profile_dir}"],
                    **context_options,
                    **launch_opts,
                )
                await apply_stealth_defaults(context)
                print(f"  [PW] Browser launch: {describe_launch_option(launch_opts)}")
                return context, launch_opts
            browser = await pw.chromium.launch(**launch_opts)
            print(f"  [PW] Browser launch: {describe_launch_option(launch_opts)}")
            return browser, launch_opts
        except Exception as exc:
            last_error = exc
            print(f"  [PW] Browser launch failed with {describe_launch_option(launch_opts)}: {exc}")
    raise RuntimeError("Could not launch Playwright browser with any configured option") from last_error


async def _fetch_pdf(context, url: str, referer: str) -> bytes | None:
    """Fetch a URL using the browser's authenticated session (cookies included)."""
    try:
        resp = await context.request.get(
            url,
            headers={"Referer": referer, "Accept": "application/pdf,*/*"},
        )
        if resp.ok:
            body = await resp.body()
            if _is_pdf(body):
                return body
    except Exception as e:
        print(f"  [PW] fetch error {url}: {e}")
    return None


def _capture_pdf_response(response, captured: list):
    """Sync callback for page.on('response'). Stores response objects for later body read."""
    ct = response.headers.get("content-type", "")
    if "application/pdf" in ct and not captured:
        captured.append(response)


# --- Publisher handlers ---

async def _handle_wiley(page, context, doi, final_url, cfg):
    base = "https://onlinelibrary.wiley.com"
    # Fast path: try direct URL construction first
    for path in [f"/doi/pdfdirect/{doi}", f"/doi/pdf/{doi}"]:
        data = await _fetch_pdf(context, base + path, final_url)
        if data:
            return data

    # Fallback: find and follow PDF button links on the article page
    # Wait for Wiley's React SPA to render the PDF button
    await page.wait_for_timeout(cfg.get("js_render_wait", 6000))
    print("  [PW/Wiley] Direct URLs failed, scanning page for PDF links")
    pdf_selectors = [
        'a[href*="pdfdirect"]',
        'a[href*="/doi/pdf/"]',
        'a[href*="epdf"]',
        'a.coolBar__ctrl--pdf',
        'a[data-item-name="view-pdf"]',
        'a:has-text("PDF")',
        'a[title*="PDF"]',
    ]
    seen_hrefs = set()
    for sel in pdf_selectors:
        try:
            links = await page.query_selector_all(sel)
            for link in links:
                href = await link.get_attribute("href")
                if not href or href in seen_hrefs:
                    continue
                seen_hrefs.add(href)
                url = urljoin(final_url, href)
                print(f"  [PW/Wiley] Trying PDF link: {url}")
                data = await _fetch_pdf(context, url, final_url)
                if data:
                    return data
                # href may point to an HTML viewer page — navigate and check for embedded PDF
                try:
                    captured = []
                    page.on("response", lambda r: _capture_pdf_response(r, captured))
                    await page.goto(url, wait_until="load", timeout=cfg["timeout"])
                    await page.wait_for_timeout(2000)
                    if captured:
                        body = await captured[0].body()
                        if _is_pdf(body):
                            return body
                    # Check for embedded PDF src
                    for embed_sel in ['embed[type="application/pdf"]', 'iframe[src*=".pdf"]']:
                        el = await page.query_selector(embed_sel)
                        if el:
                            src = await el.get_attribute("src")
                            if src:
                                data = await _fetch_pdf(context, urljoin(url, src), url)
                                if data:
                                    return data
                except Exception as nav_err:
                    print(f"  [PW/Wiley] Navigation error for {url}: {nav_err}")
        except Exception as sel_err:
            print(f"  [PW/Wiley] Selector error ({sel}): {sel_err}")
    return None


async def _handle_apa_psycnet(page, context, doi, final_url, cfg):
    await page.wait_for_timeout(cfg["js_render_wait"])
    # APA often gates the PDF behind an institutional "Get Access" -> "CHECK ACCESS"
    # flow that reveals a temporary authenticated fulltext PDF URL.
    try:
        get_access = page.locator('button:has-text("Get Access")').first
        if await get_access.count():
            await get_access.click(timeout=cfg["timeout"])
            await page.wait_for_timeout(1000)
            check_access = page.locator('button:has-text("CHECK ACCESS")').first
            if await check_access.count():
                await check_access.click(timeout=cfg["timeout"])
                await page.wait_for_timeout(2500)
            download_link = page.locator('a:has-text("DOWNLOAD PDF"), a[href*="/fulltext/"]').first
            if await download_link.count():
                href = await download_link.get_attribute("href")
                pdf_url = resolve_apa_download_url(page.url, href)
                if pdf_url:
                    data = await _fetch_pdf(context, pdf_url, page.url)
                    if data:
                        return data
    except Exception as e:
        print(f"  [PW/APA access] {e}")

    try:
        link = await page.query_selector('a[href*="/fulltext"]')
        if link:
            href = await link.get_attribute("href")
            if href:
                url = urljoin(final_url, href)
                if url.endswith(".pdf"):
                    data = await _fetch_pdf(context, url, final_url)
                    if data:
                        return data
                captured = []
                page.on("response", lambda r: _capture_pdf_response(r, captured))
                await page.goto(url, wait_until="load", timeout=cfg["timeout"])
                if captured:
                    try:
                        body = await captured[0].body()
                        if _is_pdf(body):
                            return body
                    except Exception:
                        pass
    except Exception as e:
        print(f"  [PW/APA] {e}")
    safe_doi = doi.replace("/", "-")
    return await _fetch_pdf(context, f"https://psycnet.apa.org/fulltext/{safe_doi}.pdf", final_url)


async def _handle_atypon(page, context, doi, final_url, cfg):
    repl = re.compile(r'/doi/((?:abs|abstract|full|figure|ref|citedby|book)/)?')
    for pdf_path in ['/doi/pdf/', '/doi/epdf/', '/doi/pdfplus/']:
        url = repl.sub(pdf_path, final_url)
        data = await _fetch_pdf(context, url, final_url)
        if data:
            return data
    return None


async def _handle_elsevier(page, context, doi, final_url, cfg):
    selectors = [
        'a.pdf-download-btn-link',
        'a[href*="pdfft"]',
        'a[data-aa-name*="pdf"]',
        'a:has-text("Download PDF")',
    ]
    for sel in selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                async with page.expect_download(timeout=cfg["timeout"]) as dl_info:
                    await btn.click()
                dl = await dl_info.value
                tmp = Path(await dl.path())
                data = tmp.read_bytes()
                if _is_pdf(data):
                    return data
        except Exception:
            continue
    return None


async def _handle_tandf(page, context, doi, final_url, cfg):
    parsed = urlparse(final_url)
    url = f"{parsed.scheme}://{parsed.netloc}/doi/pdf/{doi}"
    return await _fetch_pdf(context, url, final_url)


async def _handle_sage(page, context, doi, final_url, cfg):
    parsed = urlparse(final_url)
    url = f"{parsed.scheme}://{parsed.netloc}/doi/pdf/{doi}"
    return await _fetch_pdf(context, url, final_url)


async def _handle_springer(page, context, doi, final_url, cfg):
    encoded = quote(doi, safe='')
    url = f"https://link.springer.com/content/pdf/{encoded}.pdf"
    return await _fetch_pdf(context, url, final_url)


async def _handle_oxford(page, context, doi, final_url, cfg):
    for sel in ['a[href*="/pdf/"]', 'a[href*=".pdf"]']:
        link = await page.query_selector(sel)
        if link:
            href = await link.get_attribute("href")
            if href:
                data = await _fetch_pdf(context, urljoin(final_url, href), final_url)
                if data:
                    return data
    return None


async def _handle_cambridge(page, context, doi, final_url, cfg):
    for sel in ['a[href*="/pdf"]', 'a[href*=".pdf"]']:
        link = await page.query_selector(sel)
        if link:
            href = await link.get_attribute("href")
            if href:
                data = await _fetch_pdf(context, urljoin(final_url, href), final_url)
                if data:
                    return data
    return None


async def _handle_annualreviews(page, context, doi, final_url, cfg):
    form = page.locator('form.ft-download-content__form--pdf').first
    if not await form.count():
        return None
    action = await form.get_attribute("action")
    pdf_url = resolve_annualreviews_pdf_url(final_url, action)
    if not pdf_url:
        return None
    try:
        resp = await context.request.post(
            pdf_url,
            headers={"Referer": final_url, "Accept": "application/pdf,*/*"},
            timeout=cfg["timeout"],
        )
        if resp.ok:
            body = await resp.body()
            if _is_pdf(body):
                return body
    except Exception as e:
        print(f"  [PW/AnnualReviews] {e}")
    return None


async def _handle_generic(page, context, doi, final_url, cfg):
    html = await page.content()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    seen, candidates = set(), []
    for selector, attr in _GENERIC_SELECTORS:
        for elem in soup.select(selector):
            val = (elem.get(attr) or "").strip()
            if val and val not in seen:
                seen.add(val)
                candidates.append(urljoin(final_url, val))
    for a in soup.find_all("a", href=True):
        if "PDF" in a.get_text(strip=True).upper():
            val = a["href"].strip()
            if val not in seen:
                seen.add(val)
                candidates.append(urljoin(final_url, val))
    for url in candidates:
        data = await _fetch_pdf(context, url, final_url)
        if data:
            return data
    return None


_HANDLERS = {
    'wiley':       _handle_wiley,
    'apa_psycnet': _handle_apa_psycnet,
    'atypon':      _handle_atypon,
    'elsevier':    _handle_elsevier,
    'tandf':       _handle_tandf,
    'sage':        _handle_sage,
    'springer':    _handle_springer,
    'oxford':      _handle_oxford,
    'cambridge':   _handle_cambridge,
    'annualreviews': _handle_annualreviews,
}


async def download_institutional_playwright(
    doi: str,
    output_path: Path,
    config: dict = None,
    browser=None,
) -> dict:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    own_browser = browser is None

    async def _run(pw):
        nonlocal browser
        user_data_dir = cfg.get("user_data_dir")
        own_context = False

        runtime_user_data_dir, cleanup_runtime_dir, profile_dir = prepare_profile_runtime(cfg)
        tmp_dir = runtime_user_data_dir if cleanup_runtime_dir else None
        if runtime_user_data_dir:
            context, _ = await _launch_browser_with_fallbacks(
                pw,
                cfg,
                persistent_dir=runtime_user_data_dir,
                profile_dir=profile_dir,
            )
            own_context = True
        else:
            # Ephemeral context
            if own_browser:
                browser, _ = await _launch_browser_with_fallbacks(pw, cfg)
            context = await browser.new_context(
                accept_downloads=True,
                **default_context_options(),
            )
            await apply_stealth_defaults(context)
            own_context = False

        try:
            page = await context.new_page()
            if resolve_institution_login_config(cfg):
                try:
                    await maybe_run_institution_login(page, cfg, allow_login_url_navigation=True)
                except Exception as exc:
                    print(f"  [PW/Login] Continuing after login automation warning: {exc}")

            captured = []
            page.on("response", lambda r: _capture_pdf_response(r, captured))

            doi_url = f"https://doi.org/{doi}"
            print(f"  [PW] Navigating to {doi_url}")
            try:
                await page.goto(
                    doi_url,
                    wait_until=cfg.get("goto_wait_until", "domcontentloaded"),
                    timeout=cfg["timeout"],
                )
                # Extra wait for JS-heavy SPAs to settle
                await page.wait_for_timeout(2000)
            except Exception as e:
                # domcontentloaded may still succeed even if load times out
                if not page.url or page.url == doi_url:
                    print(f"  [PW] Navigation error: {e}")
                    return build_failure_result(
                        doi=doi,
                        method="playwright_institutional",
                        manual_url=doi_url,
                        reason="navigation_error",
                        error=str(e),
                    )
                print(f"  [PW] Navigation warning (continuing): {e}")
            if resolve_institution_login_config(cfg):
                try:
                    await maybe_run_institution_login(page, cfg, allow_login_url_navigation=False)
                except Exception as exc:
                    print(f"  [PW/Login] Post-DOI login automation warning: {exc}")

            final_url = page.url
            print(f"  [PW] Landed on: {final_url}")
            try:
                page_title = await page.title()
                page_text = await page.locator("body").inner_text(timeout=5000)
                page_html = await page.content()
                access_barrier = detect_access_barrier(page_title, page_text, page_html)
                access_state = detect_access_state(final_url, page_title, page_text, page_html)
                if access_barrier:
                    print(f"  [PW] Access barrier detected: {access_barrier}")
                if access_state:
                    print(f"  [PW] Access state detected: {access_state}")
            except Exception:
                access_barrier = None
                access_state = None

            # Check if interceptor already captured a PDF (direct PDF response)
            if captured:
                try:
                    body = await captured[0].body()
                    if _is_pdf(body):
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_bytes(body)
                        return {"success": True, "method": "playwright_direct",
                                "file_path": str(output_path), "manual_url": final_url, "access_state": finalize_access_state(access_state, True)}
                except Exception:
                    pass

            # Detect publisher
            handler_name = detect_publisher_handler(final_url)

            if handler_name:
                print(f"  [PW] Publisher: {handler_name}")
                handler = _HANDLERS[handler_name]
            else:
                print(f"  [PW] Unknown publisher, using generic handler")
                handler = _handle_generic

            data = await handler(page, context, doi, final_url, cfg)

            if data:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(data)
                method = f"playwright_{handler_name or 'generic'}"
                print(f"  [PW] Downloaded {len(data)//1024}KB via {method}")
                return {"success": True, "method": method, "file_path": str(output_path), "manual_url": final_url, "access_state": finalize_access_state(access_state, True)}

            # Manual fallback — only when using a real browser profile (not headless/ephemeral)
            if should_offer_manual_fallback(cfg, runtime_user_data_dir):
                print(f"\n[PW] Auto-download failed. Browser window is open — please download the PDF manually.")
                print(f"[PW] Save the file to: {output_path}")
                print(f"[PW] Press Enter here when done (or Ctrl+C to skip)...")
                try:
                    await asyncio.get_event_loop().run_in_executor(None, input, "")
                except (KeyboardInterrupt, EOFError):
                    pass
                if output_path.exists():
                    content = output_path.read_bytes()
                    if _is_pdf(content):
                        return {"success": True, "method": "manual", "file_path": str(output_path), "manual_url": final_url}
                return build_failure_result(
                    doi=doi,
                    method="playwright_institutional",
                    manual_url=final_url,
                    reason="manual_download_not_completed",
                    access_barrier=access_barrier,
                    access_state=access_state,
                    requires_human_step=True,
                    extra={"manual_attempted": True},
                )

            if access_requires_human_step(access_barrier, access_state):
                result = build_failure_result(
                    doi=doi,
                    method="playwright_institutional",
                    manual_url=final_url,
                    reason="human_step_required",
                    access_barrier=access_barrier,
                    access_state=access_state,
                    requires_human_step=True,
                )
                record_human_required_result(cfg, result, output_path)
                return result

            return build_failure_result(
                doi=doi,
                method="playwright_institutional",
                manual_url=final_url,
                reason="download_not_found",
                access_barrier=access_barrier,
                access_state=access_state,
            )

        finally:
            if own_context or (not runtime_user_data_dir and own_browser and browser):
                await context.close()
            if not runtime_user_data_dir and own_browser and browser:
                await browser.close()
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    async with async_playwright() as pw:
        return await _run(pw)


async def download_papers_institutional_playwright(
    dois: list,
    output_dir: str,
    config: dict = None,
) -> list:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    results = []
    if prepare_profile_runtime(cfg)[0]:
        for doi in dois:
            safe = re.sub(r'[/:.]', '_', doi)
            out = Path(output_dir) / f"{safe}.pdf"
            r = await download_institutional_playwright(doi, out, cfg)
            results.append({"doi": doi, **r})
        return results

    async with async_playwright() as pw:
        browser, _ = await _launch_browser_with_fallbacks(pw, cfg)
        for doi in dois:
            safe = re.sub(r'[/:.]', '_', doi)
            out = Path(output_dir) / f"{safe}.pdf"
            r = await download_institutional_playwright(doi, out, cfg, browser=browser)
            results.append({"doi": doi, **r})
        await browser.close()
    return results


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) < 2:
        print("Usage: python institutional_playwright_downloader.py <output_dir> <doi1> [doi2] ... [--config cfg.json]")
        print("       python institutional_playwright_downloader.py --manual <doi> [--config cfg.json]")
        sys.exit(1)
    args = sys.argv[1:]
    config = {}
    if "--config" in args:
        idx = args.index("--config")
        config = _load_json_file(args[idx+1])
        args = args[:idx] + args[idx+2:]

    if "--manual" in args:
        idx = args.index("--manual")
        args.remove("--manual")
        # DOI is the arg that was right after --manual
        doi = args[idx] if idx < len(args) else (args[-1] if args else None)
        if not doi:
            print("Error: --manual requires a DOI argument")
            sys.exit(1)

        async def open_doi():
            cfg2 = {**DEFAULT_CONFIG, **(config or {})}
            cfg2["headless"] = False  # always visible
            cfg2["download_mode"] = "human_assisted"
            cfg2["allow_manual_browser"] = True
            async with async_playwright() as pw:
                runtime_user_data_dir, cleanup_runtime_dir, profile_dir = prepare_profile_runtime(cfg2)
                tmp_dir = runtime_user_data_dir if cleanup_runtime_dir else None
                if runtime_user_data_dir:
                    context, _ = await _launch_browser_with_fallbacks(
                        pw,
                        cfg2,
                        persistent_dir=runtime_user_data_dir,
                        profile_dir=profile_dir,
                    )
                else:
                    browser_inst, _ = await _launch_browser_with_fallbacks(pw, cfg2)
                    context = await browser_inst.new_context(**default_context_options())
                    await apply_stealth_defaults(context)
                page = await context.new_page()
                if resolve_institution_login_config(cfg2):
                    try:
                        await maybe_run_institution_login(page, cfg2, allow_login_url_navigation=True)
                    except Exception as exc:
                        print(f"[MANUAL] Login automation warning (continuing): {exc}")
                try:
                    await page.goto(
                        f"https://doi.org/{doi}",
                        wait_until=cfg2.get("goto_wait_until", "domcontentloaded"),
                        timeout=cfg2["timeout"],
                    )
                except Exception as e:
                    if not page.url or page.url == f"https://doi.org/{doi}":
                        raise
                    print(f"[MANUAL] Navigation warning (continuing): {e}")
                if resolve_institution_login_config(cfg2):
                    try:
                        await maybe_run_institution_login(page, cfg2, allow_login_url_navigation=False)
                    except Exception as exc:
                        print(f"[MANUAL] Post-DOI login automation warning (continuing): {exc}")
                final_url = page.url
                # Detect publisher
                publisher = detect_publisher_handler(final_url) or "unknown"
                safe_doi = re.sub(r'[/:.]', '_', doi)
                suggested_path = Path(".") / f"{safe_doi}.pdf"
                print(f"[MANUAL] Landed on: {final_url}")
                print(f"[MANUAL] Publisher: {publisher}")
                print(f"[MANUAL] Browser open — please download the PDF manually.")
                print(f"[MANUAL] Suggested save path: {suggested_path.resolve()}")
                print("[MANUAL] Close the browser window when done, or press Ctrl+C here.")
                # Keep alive until user closes browser or Ctrl+C
                try:
                    while True:
                        await asyncio.sleep(1)
                        # Exit if all pages are closed
                        if not context.pages:
                            break
                except (KeyboardInterrupt, asyncio.CancelledError):
                    pass
                await context.close()
                if tmp_dir:
                    shutil.rmtree(tmp_dir, ignore_errors=True)

        asyncio.run(open_doi())
        sys.exit(0)

    if len(sys.argv) < 3:
        print("Usage: python institutional_playwright_downloader.py <output_dir> <doi1> [doi2] ... [--config cfg.json]")
        sys.exit(1)
    output_dir, dois = args[0], args[1:]
    results = asyncio.run(download_papers_institutional_playwright(dois, output_dir, config))
    print("\n" + "="*50 + "\nSUMMARY\n" + "="*50)
    for r in results:
        status = f"OK [{r.get('method')}]" if r["success"] else "FAILED"
        print(f"  [{status}] {r['doi']}")
