"""DOI-first paper downloader with institutional, OA, and Sci-Hub fallback."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin

from playwright.async_api import Error, TimeoutError, async_playwright

try:
    from rename_papers import DEFAULT_LIBRARY_DIR, merge_metadata, normalize_finder_tags, normalize_keywords, organize_pdf
except ImportError:  # pragma: no cover - direct script fallback
    from .rename_papers import DEFAULT_LIBRARY_DIR, merge_metadata, normalize_finder_tags, normalize_keywords, organize_pdf  # type: ignore

try:
    from browser_config import (
        default_browser_config,
        detect_browser_executable,
        describe_launch_option,
        iter_browser_launch_candidates,
        load_config_file,
    )
except ImportError:  # pragma: no cover - direct script fallback
    from .browser_config import (  # type: ignore
        default_browser_config,
        detect_browser_executable,
        describe_launch_option,
        iter_browser_launch_candidates,
        load_config_file,
    )

try:
    from paper_registry import record_download_result
except ImportError:  # pragma: no cover - direct script fallback
    from .paper_registry import record_download_result  # type: ignore

try:
    from pdf_to_markdown import MinerUConversionError, convert_pdf_to_markdown
except ImportError:  # pragma: no cover - direct package fallback
    from .pdf_to_markdown import MinerUConversionError, convert_pdf_to_markdown  # type: ignore

try:
    from http_client import environment_proxy_usable, system_proxy_usable
except ImportError:  # pragma: no cover - direct package fallback
    from .http_client import environment_proxy_usable, system_proxy_usable  # type: ignore

try:
    from institutional_downloader import try_institutional_access, try_open_access
    _HAS_OA_AND_INSTITUTIONAL = True
except ImportError:
    _HAS_OA_AND_INSTITUTIONAL = False

try:
    from institutional_playwright_downloader import download_institutional_playwright
    _HAS_PLAYWRIGHT_INSTITUTIONAL = True
except ImportError:
    _HAS_PLAYWRIGHT_INSTITUTIONAL = False

DEFAULT_CONFIG = default_browser_config()

SCIHUB_MIRRORS = ["https://sci-hub.ren"]

BROWSER_PROFILE_DIR = (Path(__file__).resolve().parent / ".." / "browser_profile").resolve()
IVANTI_PREFLIGHT_SCRIPT = Path(__file__).resolve().parent / "ivanti_pku_access.py"


def _build_postprocess_metadata(result: dict[str, Any], postprocess_cfg: dict[str, Any]) -> dict[str, Any]:
    provided = {
        "year": postprocess_cfg.get("year"),
        "first_author": postprocess_cfg.get("first_author"),
        "keywords": normalize_keywords(postprocess_cfg.get("keywords", [])),
        "journal": postprocess_cfg.get("journal"),
        "title": postprocess_cfg.get("title") or result.get("title"),
    }
    if postprocess_cfg.get("auto_metadata"):
        extracted = {}
        try:
            from rename_papers import extract_metadata_from_pdf
        except ImportError:  # pragma: no cover - direct script fallback
            from .rename_papers import extract_metadata_from_pdf  # type: ignore
        extracted = extract_metadata_from_pdf(result["file_path"])
        return merge_metadata(extracted, provided)
    return provided


def postprocess_download_results(results: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply one unified rename/move pass after the full download batch finishes."""
    postprocess_cfg = config.get("postprocess", {})
    if not postprocess_cfg.get("enabled"):
        return results

    processed: list[dict[str, Any]] = []
    destination_dir = postprocess_cfg.get("destination_dir", DEFAULT_LIBRARY_DIR)
    finder_tags = normalize_finder_tags(postprocess_cfg.get("finder_tags", []))

    for result in results:
        if not result.get("success") or not result.get("file_path"):
            processed.append(result)
            continue

        metadata = _build_postprocess_metadata(result, postprocess_cfg)
        organized = organize_pdf(
            result["file_path"],
            metadata,
            destination_dir=destination_dir,
            finder_tags=finder_tags,
            dry_run=False,
            on_conflict=postprocess_cfg.get("on_conflict", "suffix"),
        )
        if organized["success"]:
            processed.append(
                {
                    **result,
                    "file_path": organized["new"],
                    "organized_path": organized["new"],
                    "finder_tags": organized.get("finder_tags", []),
                }
            )
        else:
            processed.append(
                {
                    **result,
                    "success": False,
                    "error": organized.get("error", "Postprocess failed"),
                }
            )

    return processed


def _prepare_pdf_conversion_folder(pdf_path: Path) -> Path:
    """Ensure one PDF/Markdown/images namespace per paper before MinerU runs."""
    if pdf_path.parent.name == pdf_path.stem:
        return pdf_path
    paper_dir = pdf_path.parent / pdf_path.stem
    target_pdf = paper_dir / pdf_path.name
    if target_pdf.exists() and target_pdf.resolve() != pdf_path.resolve():
        raise FileExistsError(f"Conversion target already exists: {target_pdf}")
    paper_dir.mkdir(parents=True, exist_ok=True)
    os.replace(pdf_path, target_pdf)
    return target_pdf


def _has_substantive_markdown(path: Path) -> bool:
    try:
        return path.stat().st_size >= 1_000
    except OSError:
        return False


async def convert_download_results(
    results: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Serially convert successful PDFs through local MinerU after the batch download."""
    if not config.get("convert_to_markdown", True):
        return results

    converted: list[dict[str, Any]] = []
    for result in results:
        if not result.get("success") or not result.get("file_path"):
            converted.append(result)
            continue
        prepared_pdf_path: Path | None = None
        try:
            pdf_path = Path(result["file_path"]).expanduser().resolve()
            if not _valid_pdf(pdf_path):
                raise MinerUConversionError(f"Downloaded PDF is missing or invalid: {pdf_path}")
            pdf_path = _prepare_pdf_conversion_folder(pdf_path)
            prepared_pdf_path = pdf_path
            md_path = pdf_path.with_suffix(".md")
            if _has_substantive_markdown(md_path):
                conversion = {
                    "status": "existing",
                    "pdf_path": str(pdf_path),
                    "md_path": str(md_path),
                }
            else:
                conversion = await asyncio.to_thread(
                    convert_pdf_to_markdown,
                    pdf_path,
                    md_path,
                    method=str(config.get("conversion_method", "auto")),
                    model_source=str(config.get("conversion_model_source", "local")),
                    timeout=max(60, int(config.get("conversion_timeout", 2400))),
                    import_source="paper-downloader",
                )
            converted.append(
                {
                    **result,
                    "file_path": str(pdf_path),
                    "markdown_path": str(md_path),
                    "conversion": conversion,
                    "conversion_status": conversion.get("status"),
                }
            )
        except Exception as exc:
            converted.append(
                {
                    **result,
                    "file_path": str(prepared_pdf_path) if prepared_pdf_path else result.get("file_path"),
                    "conversion": {"status": "failed", "error": str(exc)},
                    "conversion_status": "failed",
                }
            )
    return converted


def resolve_resource_url(page_url: str | None, mirror: str, src: str) -> str:
    """Resolve embedded resource URLs against the live page URL when available."""
    return urljoin((page_url or mirror).rstrip("/") + "/", src)


def maybe_run_ivanti_preflight(config: dict[str, Any]) -> None:
    """Check Ivanti only after explicit opt-in; opening it needs a second opt-in."""
    preflight_cfg = config.get("ivanti_preflight")
    if not isinstance(preflight_cfg, dict):
        return
    if not IVANTI_PREFLIGHT_SCRIPT.exists():
        return

    enabled = bool(preflight_cfg.get("enabled", False))
    profile = preflight_cfg.get("profile", "PKU")
    wait_seconds = int(preflight_cfg.get("wait_seconds", 8))
    require_connected = bool(preflight_cfg.get("require_connected", False))
    open_if_needed = bool(preflight_cfg.get("open_if_needed", False))
    url = preflight_cfg.get("url") or os.environ.get("PAPER_DOWNLOADER_IVANTI_URL")
    if not enabled:
        return

    command = [
        sys.executable,
        str(IVANTI_PREFLIGHT_SCRIPT),
        "--profile",
        profile,
        "--wait-seconds",
        str(wait_seconds),
    ]
    if open_if_needed:
        command.append("--open-if-needed")
    if url:
        command.extend(["--url", url])
    if require_connected:
        command.append("--require-connected")

    try:
        result = subprocess.run(command, check=False, text=True, capture_output=True, timeout=wait_seconds + 20)
    except Exception as exc:
        print(f"  [IVN] Preflight skipped after error: {exc}")
        return

    for line in result.stdout.splitlines():
        print(f"  {line}")
    for line in result.stderr.splitlines():
        print(f"  [IVN] {line}")
    if result.returncode != 0 and require_connected:
        raise RuntimeError("Ivanti PKU connection is required but was not confirmed")


def _write_session_logs(results: list[dict[str, Any]], logs_dir: Path) -> None:
    """Write per-session log and append a short history entry."""
    from datetime import datetime

    now = datetime.now()
    session_id = f"{now.strftime('%Y%m%d_%H%M%S_%f')}_{os.getpid()}"
    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
    date_str = now.strftime("%Y-%m-%d")

    logs_dir.mkdir(parents=True, exist_ok=True)

    auto_ok = sum(1 for r in results if r.get("success") and r.get("method") != "manual")
    manual_ok = sum(1 for r in results if r.get("success") and r.get("method") == "manual")
    failed = sum(1 for r in results if not r.get("success"))
    total = len(results)

    rows: list[str] = []
    for r in results:
        doi = r.get("doi", "-")
        method = r.get("method", "-")
        title = r.get("title", "-")
        status = "✅ MANUAL" if r.get("success") and method == "manual" else "✅ SUCCESS"
        if not r.get("success"):
            status = "❌ FAILED"

        file_path = r.get("file_path")
        if file_path and Path(file_path).exists():
            size_kb = Path(file_path).stat().st_size // 1024
            notes = f"{size_kb}KB"
        elif r.get("manual_attempted"):
            notes = "Manual attempted, no file"
        else:
            notes = r.get("error", "-")
        rows.append(f"| {doi} | {title} | {method} | {status} | {notes} |")

    session_file = logs_dir / f"session_{session_id}.md"
    session_file.write_text(
        "\n".join(
            [
                f"# Download Session: {timestamp_str}",
                "",
                "## Results",
                "",
                "| DOI | Title | Method | Status | Notes |",
                "|-----|-------|--------|--------|-------|",
                *rows,
                "",
                "## Summary",
                f"- Attempted: {total}",
                f"- Auto-downloaded: {auto_ok}",
                f"- Manual: {manual_ok}",
                f"- Failed: {failed}",
            ]
        ),
        encoding="utf-8",
    )
    print(f"  [LOG] Session log: {session_file}")

    history_file = logs_dir / "download_history.md"
    history_entry = (
        f"\n## {date_str} - Session: {session_id}\n"
        f"- **Total**: {total} papers attempted, {auto_ok} auto, {manual_ok} manual, {failed} failed\n"
        f"- DOIs: {', '.join(r.get('doi', '?') for r in results)}\n\n---\n"
    )
    if not history_file.exists():
        history_file.write_text("# Paper Download History\n" + history_entry, encoding="utf-8")
    else:
        with history_file.open("a", encoding="utf-8") as handle:
            handle.write(history_entry)
    print(f"  [LOG] History updated: {history_file}")


def write_results_manifest(path: str | Path, results: list[dict[str, Any]]) -> None:
    """Atomically publish a machine-readable worker/coordinator result manifest."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    try:
        temporary.write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def results_exit_code(results: list[dict[str, Any]], *, worker_mode: bool) -> int:
    if not results:
        return 2
    if worker_mode:
        expected = {"downloaded", "needs_coordinator_fallback"}
        return 0 if all(item.get("success") or item.get("status") in expected for item in results) else 1
    return 0 if all(
        item.get("success") and item.get("conversion_status") != "failed"
        for item in results
    ) else 1


async def launch_browser_with_fallbacks(playwright: Any, config: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Launch Chromium by trying bundled, channel, then explicit executable."""
    last_error: Exception | None = None
    for launch_options in iter_browser_launch_candidates(config):
        try:
            browser = await playwright.chromium.launch(**launch_options)
            print(
                f"  Browser: headless={launch_options.get('headless')}, "
                f"{describe_launch_option(launch_options)}"
            )
            return browser, launch_options
        except Error as exc:
            last_error = exc
            print(f"  Browser launch failed with {describe_launch_option(launch_options)}: {exc}")
    raise RuntimeError("Could not launch Chromium with any configured option") from last_error


def build_output_path(output_dir: str, doi: str) -> Path:
    safe_doi = re.sub(r"[/:.]", "_", doi)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    legacy_path = output_path / f"{safe_doi}.pdf"
    nested_path = output_path / safe_doi / f"{safe_doi}.pdf"
    if legacy_path.exists() and not nested_path.exists():
        return legacy_path
    nested_path.parent.mkdir(parents=True, exist_ok=True)
    return nested_path


def normalize_doi(value: str) -> str:
    """Normalize DOI URLs and labels to one stable scheduling key."""
    normalized = unquote(str(value).strip()).lower()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
    normalized = re.sub(r"^doi\s*:\s*", "", normalized)
    return normalized.strip().rstrip("/.,;")


def _valid_pdf(path: Path, minimum_bytes: int = 10_000) -> bool:
    try:
        if path.stat().st_size <= minimum_bytes:
            return False
        with path.open("rb") as handle:
            return handle.read(4) == b"%PDF"
    except OSError:
        return False


def acquire_download_claim(output_dir: str, doi: str, wait_seconds: float = 30) -> Any | None:
    """Acquire a cross-process DOI claim without deleting another run's state."""
    safe_doi = re.sub(r"[^a-z0-9]+", "_", normalize_doi(doi)).strip("_")
    lock_dir = Path(output_dir) / ".paper-downloader-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    handle = (lock_dir / f"{safe_doi}.lock").open("a+", encoding="utf-8")
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} doi={normalize_doi(doi)}\n")
            handle.flush()
            return handle
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                return None
            time.sleep(0.05)


def release_download_claim(handle: Any | None) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


async def run_parallel_http_routes(
    doi: str,
    output_path: Path,
    *,
    timeout: int,
    network_route: str,
) -> dict[str, Any]:
    """Run the two cheap legal HTTP routes concurrently and publish atomically."""
    run_token = uuid.uuid4().hex
    candidates = {
        "oa": output_path.with_name(f".{output_path.name}.{run_token}.oa.part"),
        "institutional": output_path.with_name(f".{output_path.name}.{run_token}.institutional.part"),
    }
    cancel_event = threading.Event()
    tasks = {
        "oa": asyncio.create_task(asyncio.to_thread(
            try_open_access,
            doi,
            candidates["oa"],
            timeout,
            network_route,
            cancel_event,
        )),
        "institutional": asyncio.create_task(asyncio.to_thread(
            try_institutional_access,
            doi,
            candidates["institutional"],
            timeout,
            network_route,
            cancel_event,
        )),
    }
    results: dict[str, dict[str, Any]] = {}
    pending = set(tasks.values())
    winner_name: str | None = None
    while pending and winner_name is None:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        # Preserve OA preference when both tasks complete in the same event-loop turn.
        done_names = sorted(
            (name for name, task in tasks.items() if task in done),
            key=lambda name: 0 if name == "oa" else 1,
        )
        for route_name in done_names:
            try:
                results[route_name] = tasks[route_name].result()
            except Exception as exc:
                results[route_name] = {"success": False, "method": route_name, "error": str(exc)}
            if results[route_name].get("success") and _valid_pdf(candidates[route_name]):
                winner_name = route_name
                cancel_event.set()
                break

    if pending:
        remaining = [(name, task) for name, task in tasks.items() if task in pending]
        remaining_results = await asyncio.gather(*(task for _, task in remaining), return_exceptions=True)
        for (route_name, _), raw_result in zip(remaining, remaining_results):
            if isinstance(raw_result, Exception):
                results[route_name] = {"success": False, "method": route_name, "error": str(raw_result)}
            else:
                results[route_name] = raw_result

    for route_name in candidates:
        results.setdefault(route_name, {"success": False, "method": route_name})

    try:
        if winner_name is not None:
            result = results[winner_name]
            candidate = candidates[winner_name]
            output_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(candidate, output_path)
            return {
                **result,
                "success": True,
                "file_path": str(output_path),
                "network_route": network_route,
            }

        manual_url = (
            results["institutional"].get("manual_url")
            or results["oa"].get("manual_url")
            or f"https://doi.org/{doi}"
        )
        errors = [str(item.get("error")) for item in results.values() if item.get("error")]
        return {
            "success": False,
            "method": "http_fast_lane",
            "manual_url": manual_url,
            "network_route": network_route,
            "error": "; ".join(errors) if errors else None,
        }
    finally:
        for candidate in candidates.values():
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass


async def download_single_paper(doi: str, output_path: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    timeout = cfg.get("timeout", DEFAULT_CONFIG["timeout"])
    max_retries = cfg.get("max_retries", DEFAULT_CONFIG["max_retries"])

    async with async_playwright() as playwright:
        browser = None
        try:
            browser, _ = await launch_browser_with_fallbacks(playwright, cfg)
            context = await browser.new_context(
                accept_downloads=True,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            for mirror in SCIHUB_MIRRORS:
                url = f"{mirror}/{doi}"
                print(f"  Trying {url}...")
                for attempt in range(1, max_retries + 1):
                    try:
                        await page.goto(url, timeout=timeout, wait_until="networkidle")
                        await page.wait_for_timeout(5000)

                        embed = await page.query_selector('embed[type="application/pdf"]')
                        if embed:
                            src = await embed.get_attribute("src")
                            if src:
                                pdf_url = resolve_resource_url(page.url, mirror, src)
                                print(f"    Found embedded PDF: {pdf_url}")
                                pdf_resp = await context.request.get(pdf_url)
                                if pdf_resp.ok:
                                    content = await pdf_resp.body()
                                    if content[:4] == b"%PDF":
                                        output_path.write_bytes(content)
                                        await context.close()
                                        return {"success": True, "file_path": str(output_path), "manual_url": pdf_url}

                        links = await page.query_selector_all("a")
                        for link in links:
                            href = await link.get_attribute("href")
                            if not href:
                                continue
                            if ".pdf" not in href.lower() and "/download/" not in href.lower():
                                continue
                            pdf_url = resolve_resource_url(page.url, mirror, href)
                            print(f"    Found PDF link: {pdf_url}")
                            pdf_resp = await context.request.get(pdf_url)
                            if pdf_resp.ok:
                                content = await pdf_resp.body()
                                if content[:4] == b"%PDF":
                                    output_path.write_bytes(content)
                                    await context.close()
                                    return {"success": True, "file_path": str(output_path), "manual_url": pdf_url}

                        for frame in page.frames:
                            try:
                                embed_in_frame = await frame.query_selector("embed, object")
                                if not embed_in_frame:
                                    continue
                                src = await embed_in_frame.get_attribute("src") or await embed_in_frame.get_attribute("data")
                                if not src or ".pdf" not in src.lower():
                                    continue
                                pdf_url = resolve_resource_url(frame.url or page.url, mirror, src)
                                print(f"    Found PDF in frame: {pdf_url}")
                                pdf_resp = await context.request.get(pdf_url)
                                if pdf_resp.ok:
                                    content = await pdf_resp.body()
                                    if content[:4] == b"%PDF":
                                        output_path.write_bytes(content)
                                        await context.close()
                                        return {"success": True, "file_path": str(output_path), "manual_url": pdf_url}
                            except Exception:
                                continue

                        print(f"    No PDF found on {mirror} (attempt {attempt}/{max_retries})")
                    except (TimeoutError, Error) as exc:
                        print(f"    Error (attempt {attempt}/{max_retries}): {exc}")
                        continue

            await context.close()
            return {"success": False, "error": "No PDF found on any mirror", "manual_url": f"https://doi.org/{doi}"}
        finally:
            if browser:
                await browser.close()


def _open_failed_tabs(results: list[dict[str, Any]], config: dict[str, Any]) -> None:
    if not config.get("persist_failed_tabs", False):
        return

    urls: list[str] = []
    seen: set[str] = set()
    for result in results:
        if result.get("success"):
            continue
        url = result.get("manual_url") or (f"https://doi.org/{result['doi']}" if result.get("doi") else None)
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)

    if not urls:
        return

    executable = config.get("browser_executable") or detect_browser_executable()
    if not executable:
        print("  [MANUAL TAB] No local browser executable detected; skipping failed-tab persistence.")
        return

    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        f"--user-data-dir={BROWSER_PROFILE_DIR}",
        "--new-window",
        *urls,
    ]
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(f"  [MANUAL TAB] Left {len(urls)} failed paper page(s) open in the persistent browser profile.")
        for result in results:
            if not result.get("success") and result.get("manual_url") in seen:
                result["manual_tab_opened"] = True
                result["status"] = "needs_manual_download"
    except Exception as exc:
        print(f"  [MANUAL TAB] Could not persist failed paper tabs: {exc}")


async def download_paper(doi: str, output_dir: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    doi = normalize_doi(doi)
    full_path = build_output_path(output_dir, doi)

    print(f"Downloading: {doi}")

    if full_path.exists():
        if cfg.get("skip_existing", True) and _valid_pdf(full_path):
            print(f"  SKIP [existing]: {full_path}")
            return {"doi": doi, "success": True, "method": "existing", "file_path": str(full_path), "status": "downloaded"}
        preserved_path = full_path.with_name(f".{full_path.name}.preserved.{uuid.uuid4().hex}")
        os.replace(full_path, preserved_path)
        print(f"  PRESERVE [previous or invalid]: {preserved_path}")

    last_manual_url = f"https://doi.org/{doi}"
    http_timeout = max(5, int(cfg.get("http_timeout", 12)))
    if _HAS_OA_AND_INSTITUTIONAL and not cfg.get("fallback_only", False):
        direct_result = await run_parallel_http_routes(
            doi,
            full_path,
            timeout=http_timeout,
            network_route="direct",
        )
        last_manual_url = direct_result.get("manual_url") or last_manual_url
        if direct_result["success"]:
            print(f"  SUCCESS [{direct_result['method']} / direct]: {direct_result['file_path']}")
            return {"doi": doi, "status": "downloaded", **direct_result}

        proxy_routes: list[str] = []
        if environment_proxy_usable():
            proxy_routes.append("environment")
        if system_proxy_usable():
            proxy_routes.append("system")
        for proxy_route in proxy_routes:
            proxy_result = await run_parallel_http_routes(
                doi,
                full_path,
                timeout=http_timeout,
                network_route=proxy_route,
            )
            last_manual_url = proxy_result.get("manual_url") or last_manual_url
            if proxy_result["success"]:
                print(f"  SUCCESS [{proxy_result['method']} / configured proxy]: {proxy_result['file_path']}")
                return {"doi": doi, "status": "downloaded", **proxy_result}
        print("  HTTP routes failed; trying bounded headless browser access...")

    if cfg.get("worker_mode", False):
        return {
            "doi": doi,
            "success": False,
            "method": "http_worker_exhausted",
            "status": "needs_coordinator_fallback",
            "file_path": None,
            "manual_url": last_manual_url,
        }

    if _HAS_PLAYWRIGHT_INSTITUTIONAL and not full_path.exists():
        browser_semaphore = cfg.get("_browser_semaphore")
        if browser_semaphore is None:
            pw_result = await download_institutional_playwright(doi, full_path, cfg)
        else:
            async with browser_semaphore:
                pw_result = await download_institutional_playwright(doi, full_path, cfg)
        last_manual_url = pw_result.get("manual_url") or last_manual_url
        if pw_result["success"]:
            print(f"  SUCCESS [{pw_result['method']}]: {pw_result['file_path']}")
            return {"doi": doi, "status": "downloaded", **pw_result}
        print("  Playwright institutional failed, falling back to Sci-Hub...")

    browser_semaphore = cfg.get("_browser_semaphore")
    if browser_semaphore is None:
        result = await download_single_paper(doi, full_path, cfg)
    else:
        async with browser_semaphore:
            result = await download_single_paper(doi, full_path, cfg)
    if result["success"]:
        result["method"] = "scihub"
        print(f"  SUCCESS [scihub]: {result['file_path']}")
        result["status"] = "downloaded"
    else:
        print(f"  FAILED: {result.get('error', 'Unknown error')}")
        result["status"] = "failed"
    result["manual_url"] = result.get("manual_url") or last_manual_url
    return {"doi": doi, **result}


async def download_papers(dois: list[str], output_dir: str, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    concurrency = max(1, int(cfg.get("concurrency", 1)))
    browser_concurrency = max(1, int(cfg.get("browser_concurrency", 1)))
    cfg["_browser_semaphore"] = asyncio.Semaphore(browser_concurrency)
    semaphore = asyncio.Semaphore(concurrency)

    normalized_dois: list[str] = []
    seen_dois: set[str] = set()
    for raw_doi in dois:
        doi = normalize_doi(raw_doi)
        if not doi or doi in seen_dois:
            continue
        seen_dois.add(doi)
        normalized_dois.append(doi)

    async def _worker(doi: str) -> dict[str, Any]:
        claim = await asyncio.to_thread(
            acquire_download_claim,
            output_dir,
            doi,
            float(cfg.get("claim_wait_seconds", 30)),
        )
        if claim is None:
            return {
                "doi": doi,
                "success": False,
                "method": "claimed_by_another_worker",
                "status": "in_progress_elsewhere",
                "file_path": None,
            }
        try:
            async with semaphore:
                print(f"\n{'=' * 50}")
                return await download_paper(doi, output_dir, cfg)
        finally:
            release_download_claim(claim)

    results = await asyncio.gather(*[_worker(doi) for doi in normalized_dois])
    if not cfg.get("worker_mode", False):
        # Only the coordinator owns shared registry, log, and library side effects.
        results = postprocess_download_results(list(results), cfg)
        results = await convert_download_results(list(results), cfg)
        _open_failed_tabs(results, cfg)

        for item in results:
            if item.get("organized_path"):
                item["status"] = "archived"
            record_download_result(item)

        scripts_dir = Path(__file__).parent
        logs_dir = (scripts_dir / ".." / "logs").resolve()
        _write_session_logs(results, logs_dir)
    return list(results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download papers by DOI with layered fallbacks")
    parser.add_argument("output_dir", help="Directory where PDFs will be written")
    parser.add_argument("dois", nargs="+", help="One or more DOI values")
    parser.add_argument("--config", help="Optional JSON config file")
    parser.add_argument("--concurrency", type=int, help="Override batch concurrency")
    parser.add_argument("--http-timeout", type=int, help="Per-request timeout in seconds for direct/proxy HTTP routes")
    parser.add_argument("--browser-concurrency", type=int, help="Maximum concurrent browser fallbacks")
    execution_mode = parser.add_mutually_exclusive_group()
    execution_mode.add_argument(
        "--worker-mode",
        action="store_true",
        help="Subagent worker mode: HTTP routes only; do not use browsers or write shared registry/logs/library state",
    )
    execution_mode.add_argument(
        "--fallback-only",
        action="store_true",
        help="Coordinator pass: skip HTTP for unresolved DOI values and run bounded browser/final fallbacks",
    )
    parser.add_argument("--result-json", help="Atomically write machine-readable results to this JSON file")
    conversion_mode = parser.add_mutually_exclusive_group()
    conversion_mode.add_argument(
        "--convert",
        dest="convert_to_markdown",
        action="store_true",
        help="Convert successful PDFs to canonical Markdown (default)",
    )
    conversion_mode.add_argument(
        "--no-convert",
        dest="convert_to_markdown",
        action="store_false",
        help="Keep the PDF only for this run",
    )
    parser.add_argument(
        "--organize-after-download",
        action="store_true",
        help=f"Explicitly hand successful downloads to the PAPER intake workflow at {DEFAULT_LIBRARY_DIR}",
    )
    parser.add_argument(
        "--no-organize-after-download",
        dest="organize_after_download",
        action="store_false",
        help="After the full batch finishes, keep successful downloads in the output directory without renaming or moving them",
    )
    parser.add_argument(
        "--finder-tags",
        help="Comma-separated Finder tags to apply when --organize-after-download is enabled",
    )
    parser.add_argument(
        "--persist-failed-tabs",
        dest="persist_failed_tabs",
        action="store_true",
        help="Explicitly leave unresolved paper pages open in the persistent browser profile",
    )
    parser.add_argument(
        "--no-persist-failed-tabs",
        dest="persist_failed_tabs",
        action="store_false",
        help="Do not leave unresolved paper pages open in the persistent browser profile",
    )
    parser.add_argument("--keywords", help="Comma-separated filename keywords for post-download organization")
    parser.add_argument("--year", help="Override year used during post-download organization")
    parser.add_argument("--author", dest="first_author", help="Override first author used during post-download organization")
    parser.add_argument("--journal", help="Override journal used during post-download organization")
    parser.add_argument("--title", help="Override title used during post-download organization")
    parser.add_argument(
        "--on-conflict",
        choices=["suffix", "skip", "overwrite"],
        default="suffix",
        help="How organization handles existing target filenames",
    )
    parser.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        default=None,
        help="Skip DOI files that already exist in the output directory",
    )
    parser.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Re-download even if the DOI file already exists",
    )
    parser.set_defaults(
        organize_after_download=False,
        persist_failed_tabs=False,
        convert_to_markdown=True,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = {**DEFAULT_CONFIG, **load_config_file(args.config)}
    if args.concurrency is not None:
        config["concurrency"] = max(1, args.concurrency)
    if args.http_timeout is not None:
        config["http_timeout"] = max(5, args.http_timeout)
    if args.browser_concurrency is not None:
        config["browser_concurrency"] = max(1, args.browser_concurrency)
    config["worker_mode"] = bool(args.worker_mode)
    config["fallback_only"] = bool(args.fallback_only)
    config["convert_to_markdown"] = bool(args.convert_to_markdown)
    if args.skip_existing is not None:
        config["skip_existing"] = args.skip_existing
    config["persist_failed_tabs"] = getattr(args, "persist_failed_tabs", False)
    if args.organize_after_download:
        config["postprocess"] = {
            "enabled": True,
            "destination_dir": DEFAULT_LIBRARY_DIR,
            "finder_tags": [item.strip() for item in (args.finder_tags or "").split(",") if item.strip()],
            "keywords": [item.strip() for item in (args.keywords or "").split(",") if item.strip()],
            "year": args.year,
            "first_author": args.first_author,
            "journal": args.journal,
            "title": args.title,
            "auto_metadata": True,
            "on_conflict": args.on_conflict,
        }

    results = asyncio.run(download_papers(args.dois, args.output_dir, config))
    if args.result_json:
        write_results_manifest(args.result_json, results)

    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    success_count = sum(1 for item in results if item["success"])
    print(f"Downloaded: {success_count}/{len(results)}")
    for item in results:
        if item.get("conversion_status") == "failed":
            status = "PDF_ONLY"
        elif item["success"]:
            status = "OK"
        elif item.get("status") == "needs_coordinator_fallback":
            status = "DEFERRED"
        else:
            status = "FAILED"
        print(f"  [{status}] {item['doi']} ({item.get('method', '-')})")
    return results_exit_code(results, worker_mode=bool(args.worker_mode))


if __name__ == "__main__":
    raise SystemExit(main())
