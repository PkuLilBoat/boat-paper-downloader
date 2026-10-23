"""Scan a browser download folder for recent PDFs and archive them into the paper library."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from rename_papers import (
        DEFAULT_LIBRARY_DIR,
        extract_metadata_from_pdf,
        merge_metadata,
        normalize_finder_tags,
        normalize_keywords,
        organize_pdf,
    )
except ImportError:  # pragma: no cover - direct script fallback
    from .rename_papers import (  # type: ignore
        DEFAULT_LIBRARY_DIR,
        extract_metadata_from_pdf,
        merge_metadata,
        normalize_finder_tags,
        normalize_keywords,
        organize_pdf,
    )

try:
    from paper_registry import load_registry, record_archive
except ImportError:  # pragma: no cover - direct script fallback
    from .paper_registry import load_registry, record_archive  # type: ignore

try:
    from pdf_to_markdown import convert_pdf_to_markdown
except ImportError:  # pragma: no cover - direct script fallback
    from .pdf_to_markdown import convert_pdf_to_markdown  # type: ignore


DEFAULT_DOWNLOADS_DIR = str(Path.home() / "Downloads")


def _known_paths() -> set[str]:
    registry = load_registry()
    known: set[str] = set()
    for entry in registry.get("papers", []):
        for field in ("raw_path", "final_path"):
            value = entry.get(field)
            if value:
                known.add(str(Path(value).expanduser().resolve()))
    return known


def scan_download_folder(
    source_dir: str = DEFAULT_DOWNLOADS_DIR,
    *,
    hours: int = 24,
    destination_dir: str = DEFAULT_LIBRARY_DIR,
    finder_tags: list[str] | None = None,
    keywords: list[str] | None = None,
    convert_to_markdown: bool = True,
) -> list[dict[str, Any]]:
    root = Path(source_dir).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Download folder not found: {root}")

    cutoff = datetime.now() - timedelta(hours=max(1, hours))
    known_paths = _known_paths()
    results: list[dict[str, Any]] = []

    for pdf_path in sorted(root.glob("*.pdf"), key=lambda item: item.stat().st_mtime, reverse=True):
        resolved = str(pdf_path.resolve())
        if resolved in known_paths:
            continue
        if datetime.fromtimestamp(pdf_path.stat().st_mtime) < cutoff:
            continue

        extracted = extract_metadata_from_pdf(str(pdf_path))
        metadata = merge_metadata(
            extracted,
            {
                "keywords": normalize_keywords(keywords or []),
            },
        )
        result = organize_pdf(
            str(pdf_path),
            metadata,
            destination_dir=destination_dir,
            finder_tags=normalize_finder_tags(finder_tags or []),
            dry_run=False,
            on_conflict="suffix",
        )
        if result["success"]:
            if convert_to_markdown:
                try:
                    final_pdf = Path(result["new"])
                    conversion = convert_pdf_to_markdown(
                        final_pdf,
                        final_pdf.with_suffix(".md"),
                        import_source="paper-downloader",
                    )
                    result["conversion"] = conversion
                    result["conversion_status"] = conversion.get("status")
                    result["markdown_path"] = conversion.get("md_path")
                except Exception as exc:
                    result["conversion"] = {"status": "failed", "error": str(exc)}
                    result["conversion_status"] = "failed"
            record_archive(
                raw_path=resolved,
                final_path=result["new"],
                metadata=metadata,
                finder_tags=normalize_finder_tags(finder_tags or []),
                source_method="scan_download_folder",
                status="scanned_from_downloads",
            )
        result["source_path"] = resolved
        results.append(result)

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan the browser download folder for recent PDFs and archive them")
    parser.add_argument("--source-dir", default=DEFAULT_DOWNLOADS_DIR, help=f"Folder to scan (default: {DEFAULT_DOWNLOADS_DIR})")
    parser.add_argument("--hours", type=int, default=24, help="Only consider PDFs modified in the last N hours")
    parser.add_argument("--destination-dir", default=DEFAULT_LIBRARY_DIR, help=f"Archive destination (default: {DEFAULT_LIBRARY_DIR})")
    parser.add_argument("--finder-tags", help="Comma-separated Finder tags to apply")
    parser.add_argument("--keywords", help="Comma-separated filename keywords to inject")
    conversion_mode = parser.add_mutually_exclusive_group()
    conversion_mode.add_argument("--convert", dest="convert_to_markdown", action="store_true", help="Convert archived PDFs to Markdown (default)")
    conversion_mode.add_argument("--no-convert", dest="convert_to_markdown", action="store_false", help="Archive PDF only for this run")
    parser.set_defaults(convert_to_markdown=True)
    return parser.parse_args()


def _parse_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def main() -> int:
    args = parse_args()
    results = scan_download_folder(
        source_dir=args.source_dir,
        hours=args.hours,
        destination_dir=args.destination_dir,
        finder_tags=_parse_csv(args.finder_tags),
        keywords=_parse_csv(args.keywords),
        convert_to_markdown=args.convert_to_markdown,
    )
    success = sum(1 for item in results if item.get("success"))
    print(f"Archived: {success}/{len(results)}")
    for item in results:
        status = "PDF_ONLY" if item.get("conversion_status") == "failed" else ("OK" if item.get("success") else "FAILED")
        print(f"  [{status}] {item.get('source_path')} -> {item.get('new', item.get('error'))}")
    return 0 if all(item.get("success") and item.get("conversion_status") != "failed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
