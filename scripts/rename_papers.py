"""Rename downloaded PDFs into a consistent library-friendly format."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_LIBRARY_DIR = str((Path.home() / "Documents" / "PAPER" / "Papers"))

try:
    from update_paper_properties import update_markdown_properties
except ImportError:  # pragma: no cover - direct script fallback
    from .update_paper_properties import update_markdown_properties  # type: ignore

try:
    from paper_registry import record_archive
except ImportError:  # pragma: no cover - direct script fallback
    from .paper_registry import record_archive  # type: ignore


def extract_metadata_from_pdf(pdf_path: str) -> dict[str, Any]:
    """Best-effort metadata extraction from the first few pages of a PDF."""
    try:
        import pdfplumber
    except ImportError:
        print("Warning: pdfplumber not installed; --auto-metadata will be skipped")
        return {}

    metadata: dict[str, Any] = {
        "year": None,
        "first_author": None,
        "keywords": [],
        "title": None,
        "journal": None,
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            text_parts: list[str] = []
            for page in pdf.pages[:3]:
                text_parts.append(page.extract_text() or "")
            text = "\n".join(text_parts)

        years = re.findall(r"\b(19\d{2}|20[0-3]\d)\b", text)
        if years:
            metadata["year"] = years[0]

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines[:10]:
            if 20 <= len(line) <= 200:
                metadata["title"] = line[:150]
                break

        # Very lightweight heuristic: title case line after title often starts author list.
        if len(lines) >= 2:
            candidate = lines[1]
            match = re.match(r"([A-Z][A-Za-z'\-]+)", candidate)
            if match:
                metadata["first_author"] = match.group(1)

    except Exception as exc:
        print(f"Warning: metadata extraction failed for {pdf_path}: {exc}")

    return metadata


def sanitize_segment(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    cleaned = re.sub(r"[<>:\"/\\|?*]", "", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or None


def normalize_keywords(raw_keywords: list[str] | None) -> list[str]:
    values = raw_keywords or []
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        segment = sanitize_segment(value)
        if not segment:
            continue
        lowered = segment.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned.append(segment)
    return cleaned


def normalize_finder_tags(raw_tags: list[str] | None) -> list[str]:
    values = raw_tags or []
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        tag = re.sub(r"\s+", " ", value.strip())
        tag = re.sub(r"[<>:\"/\\|?*]", "", tag).strip(". ")
        if not tag:
            continue
        lowered = tag.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned.append(tag)
    return cleaned


def merge_metadata(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key == "keywords":
            merged[key] = normalize_keywords(value or merged.get(key, []))
        elif value not in (None, "", []):
            merged[key] = value
    return merged


def generate_filename(metadata: dict[str, Any], keywords: list[str] | None = None) -> str | None:
    """Generate a filename in the form year_author_keywords_journal.pdf."""
    parts: list[str] = []

    for field in [metadata.get("year"), metadata.get("first_author")]:
        segment = sanitize_segment(field)
        if segment:
            parts.append(segment)

    keyword_segments = normalize_keywords(keywords or metadata.get("keywords", []))
    if keyword_segments:
        parts.append("_".join(keyword_segments[:3]))

    journal = sanitize_segment(metadata.get("journal"))
    if journal:
        parts.append(journal)

    if not parts and metadata.get("title"):
        title_words = [sanitize_segment(word) for word in metadata["title"].split()[:6]]
        parts.extend([word for word in title_words if word])

    if not parts:
        return None

    return f"{'_'.join(parts)}.pdf"


def resolve_conflict(path: Path, policy: str) -> Path | None:
    if not path.exists():
        return path
    if policy == "overwrite":
        return path
    if policy == "skip":
        return None
    if policy == "suffix":
        for index in range(2, 1000):
            candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
            if not candidate.exists():
                return candidate
    raise ValueError(f"Unsupported conflict policy: {policy}")


def apply_finder_tags(pdf_path: str | Path, finder_tags: list[str]) -> None:
    """Write Finder tags via macOS metadata xattrs and refresh Spotlight."""
    normalized_tags = normalize_finder_tags(finder_tags)
    if not normalized_tags:
        return

    target = Path(pdf_path)
    existing: list[str] = []
    try:
        raw = subprocess.check_output(
            ["xattr", "-px", "com.apple.metadata:_kMDItemUserTags", str(target)],
            text=True,
        ).strip()
        if raw:
            existing = plistlib.loads(bytes.fromhex(raw))
    except subprocess.CalledProcessError:
        existing = []

    merged: list[str] = []
    for tag in [*existing, *normalized_tags]:
        if tag not in merged:
            merged.append(tag)

    payload = plistlib.dumps(merged, fmt=plistlib.FMT_BINARY).hex()
    subprocess.run(
        ["xattr", "-wx", "com.apple.metadata:_kMDItemUserTags", payload, str(target)],
        check=True,
    )
    subprocess.run(["mdimport", str(target)], check=True)


def rename_pdf(
    pdf_path: str,
    new_name: str,
    dry_run: bool = False,
    on_conflict: str = "suffix",
    destination_dir: str | None = None,
) -> dict[str, Any]:
    old_path = Path(pdf_path)
    if not old_path.exists():
        return {"success": False, "error": f"File not found: {pdf_path}"}

    target_dir = Path(destination_dir) if destination_dir else old_path.parent
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / new_name
    resolved = resolve_conflict(target, on_conflict)
    if resolved is None:
        return {
            "success": False,
            "old": str(old_path),
            "new": str(target),
            "error": "Target exists and conflict policy is skip",
        }

    if dry_run:
        print(f"  [DRY RUN] {old_path.name} -> {resolved.name}")
        return {"success": True, "dry_run": True, "old": str(old_path), "new": str(resolved)}

    try:
        if resolved.exists() and resolved != old_path:
            resolved.unlink()
        old_path.rename(resolved)
        return {"success": True, "old": str(old_path), "new": str(resolved)}
    except Exception as exc:
        return {"success": False, "error": str(exc), "old": str(old_path), "new": str(resolved)}


def organize_pdf(
    pdf_path: str,
    metadata: dict[str, Any],
    destination_dir: str = DEFAULT_LIBRARY_DIR,
    finder_tags: list[str] | None = None,
    dry_run: bool = False,
    on_conflict: str = "suffix",
    import_source: str = "paper-downloader",
) -> dict[str, Any]:
    """Rename into the library format, move to the paper folder, and apply Finder tags."""
    new_name = generate_filename(metadata, metadata.get("keywords"))
    if not new_name:
        return {
            "success": False,
            "old": str(pdf_path),
            "error": "Could not generate filename from provided metadata",
        }

    original = Path(pdf_path)
    library = Path(destination_dir)
    paper_stem = Path(new_name).stem
    paper_dir = library / paper_stem
    target_name = new_name
    target_pdf = paper_dir / target_name
    if target_pdf.exists() and original.resolve() != target_pdf.resolve():
        if on_conflict == "skip":
            return {"success": False, "old": str(original), "new": str(target_pdf), "error": "Target exists and conflict policy is skip"}
        if on_conflict == "suffix":
            for index in range(2, 1000):
                candidate_stem = f"{paper_stem}_{index}"
                candidate_dir = library / candidate_stem
                candidate_pdf = candidate_dir / f"{candidate_stem}.pdf"
                if not candidate_dir.exists():
                    paper_stem = candidate_stem
                    paper_dir = candidate_dir
                    target_name = candidate_pdf.name
                    break
            else:
                return {"success": False, "old": str(original), "error": "Could not resolve paper folder conflict"}

    source_md = original.with_suffix(".md")
    source_images = original.parent / f"{original.stem}_images"
    result = rename_pdf(
        pdf_path,
        target_name,
        dry_run=dry_run,
        on_conflict=on_conflict,
        destination_dir=str(paper_dir),
    )
    if result["success"] and not dry_run:
        target_md = paper_dir / f"{paper_stem}.md"
        if source_md.exists() and source_md.resolve() != target_md.resolve() and not target_md.exists():
            source_md.rename(target_md)
        if source_images.is_dir() and not (paper_dir / "images").exists():
            source_images.rename(paper_dir / "images")
        if not target_md.exists():
            title = metadata.get("title") or paper_stem.replace("_", " ")
            target_md.write_text(f"# {title}\n", encoding="utf-8")
        update_markdown_properties(
            target_md,
            library_dir=library,
            import_source=import_source,
        )
    if result["success"] and finder_tags and not dry_run:
        apply_finder_tags(result["new"], finder_tags)
        result["finder_tags"] = normalize_finder_tags(finder_tags)
    if result["success"] and not dry_run:
        record_archive(
            raw_path=str(pdf_path),
            final_path=result["new"],
            metadata={
                **metadata,
                "keywords": normalize_keywords(metadata.get("keywords", [])),
            },
            finder_tags=normalize_finder_tags(finder_tags or []),
            source_method="organize_pdf",
        )
    return result


def rename_papers_from_manifest(
    manifest_path: str,
    dry_run: bool = False,
    on_conflict: str = "suffix",
    auto_metadata: bool = False,
    destination_dir: str = DEFAULT_LIBRARY_DIR,
    finder_tags: list[str] | None = None,
    import_source: str = "paper-downloader",
) -> list[dict[str, Any]]:
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest_dir = manifest_file.parent
    results: list[dict[str, Any]] = []

    for paper in manifest.get("papers", []):
        pdf_path = manifest_dir / paper["file"]
        provided_metadata = {
            "year": paper.get("year"),
            "first_author": paper.get("first_author") or paper.get("author"),
            "keywords": paper.get("keywords", []),
            "journal": paper.get("journal"),
            "title": paper.get("title"),
        }
        extracted_metadata = extract_metadata_from_pdf(str(pdf_path)) if auto_metadata else {}
        paper_metadata = merge_metadata(extracted_metadata, provided_metadata)
        result = organize_pdf(
            str(pdf_path),
            paper_metadata,
            destination_dir=destination_dir,
            finder_tags=finder_tags,
            dry_run=dry_run,
            on_conflict=on_conflict,
            import_source=import_source,
        )
        result["file"] = paper["file"]
        results.append(result)

        if result["success"]:
            print(f"  Renamed: {paper['file']} -> {Path(result['new']).name}")
        else:
            print(f"  Failed: {paper['file']} - {result.get('error')}")

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rename PDFs with metadata-rich filenames")
    parser.add_argument("target", help="PDF file or manifest JSON")
    parser.add_argument("--year")
    parser.add_argument("--author", dest="first_author")
    parser.add_argument("--keywords", help="Comma-separated keywords")
    parser.add_argument("--journal")
    parser.add_argument("--title")
    parser.add_argument(
        "--destination-dir",
        default=DEFAULT_LIBRARY_DIR,
        help=f"Directory for the final organized PDFs (default: {DEFAULT_LIBRARY_DIR})",
    )
    parser.add_argument("--finder-tags", help="Comma-separated Finder tags to apply after renaming")
    parser.add_argument(
        "--import-source",
        choices=["local", "paper-downloader"],
        default="paper-downloader",
        help="Deterministic p0_import_source value for the canonical Markdown",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without renaming")
    parser.add_argument(
        "--auto-metadata",
        action="store_true",
        help="Attempt to fill missing fields from the PDF before generating the filename",
    )
    parser.add_argument(
        "--on-conflict",
        choices=["suffix", "skip", "overwrite"],
        default="suffix",
        help="How to handle an existing target filename",
    )
    return parser.parse_args()


def build_single_file_metadata(args: argparse.Namespace) -> dict[str, Any]:
    provided = {
        "year": args.year,
        "first_author": args.first_author,
        "keywords": [item.strip() for item in (args.keywords or "").split(",") if item.strip()],
        "journal": args.journal,
        "title": args.title,
    }

    if not args.auto_metadata:
        return provided

    extracted = extract_metadata_from_pdf(args.target)
    return merge_metadata(extracted, provided)


def parse_comma_separated(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def main() -> int:
    args = parse_args()

    if args.target.lower().endswith(".json"):
        results = rename_papers_from_manifest(
            args.target,
            dry_run=args.dry_run,
            on_conflict=args.on_conflict,
            auto_metadata=args.auto_metadata,
            destination_dir=args.destination_dir,
            finder_tags=parse_comma_separated(args.finder_tags),
            import_source=args.import_source,
        )
        success_count = sum(1 for item in results if item["success"])
        print(f"\nDone: {success_count}/{len(results)} renamed")
        return 0 if success_count == len(results) else 1

    metadata = build_single_file_metadata(args)
    result = organize_pdf(
        args.target,
        metadata,
        destination_dir=args.destination_dir,
        finder_tags=parse_comma_separated(args.finder_tags),
        dry_run=args.dry_run,
        on_conflict=args.on_conflict,
        import_source=args.import_source,
    )
    if result["success"]:
        print(f"Renamed: {args.target} -> {Path(result['new']).name}")
        return 0

    print(f"Failed: {result.get('error')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
