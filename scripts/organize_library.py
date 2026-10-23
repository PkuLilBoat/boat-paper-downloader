#!/usr/bin/env python3
"""Organize the canonical paper library into one folder per paper.

Layout:
  /tmp/paper-downloader-fixtures/Papers/<paper_stem>/<paper_stem>.pdf

The category slug is metadata only. Cleanup keeps paper-facing artifacts and
moves intermediate files to macOS Trash by default.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_LIBRARY_DIR = (Path.home() / "Documents" / "PAPER" / "Papers")
SKILL_DIR = Path(__file__).resolve().parents[1]
CATEGORY_TABLE = SKILL_DIR / "config" / "paper_categories.json"
TRASH_ROOT = Path.home() / ".Trash" / "paper-downloader-cleanup"

DERIVED_PDF_SUFFIXES = ("_layout.pdf", "_origin.pdf", "_span.pdf", "_spans.pdf")
DERIVED_DIR_NAMES = {
    "mineru_output",
    "magicpdf_output",
    "mineru_local_ocr",
    "auto",
    "ocr",
    "debug",
    "layout",
    "spans",
}
SIDEcar_SUFFIXES = {".json", ".jsonl", ".log", ".txt"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

try:
    from paper_registry import record_archive
except ImportError:  # pragma: no cover
    from .paper_registry import record_archive  # type: ignore


def load_categories(table_path: Path = CATEGORY_TABLE) -> dict[str, Any]:
    data = json.loads(table_path.read_text(encoding="utf-8"))
    categories = data.get("categories", [])
    allowed = {item["slug"]: item for item in categories}
    if not allowed:
        raise ValueError(f"No categories found in {table_path}")
    default = data.get("default_category") or "00_uncategorized"
    if default not in allowed:
        raise ValueError(f"default_category {default!r} is not in category table")
    return {"allowed": allowed, "default": default, "table": str(table_path)}


def validate_category(slug: str | None, category_data: dict[str, Any]) -> str:
    selected = slug or category_data["default"]
    if selected not in category_data["allowed"]:
        allowed = ", ".join(sorted(category_data["allowed"]))
        raise ValueError(f"Unknown category {selected!r}. Allowed categories: {allowed}")
    return selected


def sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]", "", value.strip())
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned[:180] or "paper"


def resolve_conflict(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}_{index}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not resolve folder conflict for {path}")


def is_derived_pdf(path: Path) -> bool:
    return path.name.lower().endswith(DERIVED_PDF_SUFFIXES)


def is_inside_allowed_category(path: Path, library_dir: Path, allowed: set[str]) -> bool:
    try:
        rel = path.resolve().relative_to(library_dir.resolve())
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] in allowed


def iter_primary_pdfs(library_dir: Path, allowed: set[str]) -> list[Path]:
    pdfs: list[Path] = []
    for pdf in library_dir.rglob("*.pdf"):
        if pdf.is_symlink() or is_derived_pdf(pdf):
            continue
        if any(part in DERIVED_DIR_NAMES for part in pdf.parts):
            continue
        pdfs.append(pdf)
    return sorted(set(pdfs), key=lambda p: str(p))


def move_to_trash(path: Path, dry_run: bool) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = TRASH_ROOT / stamp
    target = target_dir / path.name
    if dry_run:
        return str(target)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = resolve_file_conflict(target)
    shutil.move(str(path), str(target))
    return str(target)


def resolve_file_conflict(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not resolve conflict for {path}")


def normalize_images_folder(paper_dir: Path, paper_stem: str, dry_run: bool) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    final_images = paper_dir / "images"
    for candidate in [paper_dir / f"{paper_stem}_images"]:
        if candidate.exists() and candidate.is_dir() and candidate != final_images:
            actions.append({"action": "move_images", "from": str(candidate), "to": str(final_images)})
            if not dry_run:
                final_images.mkdir(parents=True, exist_ok=True)
                for item in candidate.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(candidate)
                        target = resolve_file_conflict(final_images / rel)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(item), str(target))
                shutil.rmtree(candidate, ignore_errors=True)
    return actions


def cleanup_paper_dir(paper_dir: Path, paper_stem: str, dry_run: bool) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    actions.extend(normalize_images_folder(paper_dir, paper_stem, dry_run))
    keep_names = {f"{paper_stem}.pdf", f"{paper_stem}.md", "images", "metadata.json"}

    for item in sorted(paper_dir.iterdir(), key=lambda p: p.name):
        if item.name in keep_names:
            continue
        should_clean = False
        if item.is_dir() and item.name in DERIVED_DIR_NAMES:
            should_clean = True
        elif item.is_file() and item.suffix.lower() == ".pdf" and is_derived_pdf(item):
            should_clean = True
        elif item.is_file() and item.suffix.lower() in SIDEcar_SUFFIXES:
            should_clean = True
        elif item.is_file() and item.suffix.lower() in IMAGE_EXTS and (paper_dir / "images").exists():
            should_clean = True
        if should_clean:
            trashed_to = move_to_trash(item, dry_run)
            actions.append({"action": "trash_intermediate", "from": str(item), "to": trashed_to})
    return actions


def existing_category(pdf: Path, library_dir: Path, allowed: set[str]) -> str | None:
    try:
        rel = pdf.resolve().relative_to(library_dir.resolve())
    except ValueError:
        return None
    if rel.parts and rel.parts[0] in allowed:
        return rel.parts[0]
    return None


def organize_pdf(pdf: Path, library_dir: Path, category_slug: str, dry_run: bool, allowed: set[str] | None = None) -> dict[str, Any]:
    paper_stem = sanitize_name(pdf.stem)
    effective_category = existing_category(pdf, library_dir, allowed or set()) or category_slug
    paper_dir = library_dir / paper_stem
    if pdf.parent == paper_dir:
        target_pdf = pdf
    else:
        paper_dir = resolve_conflict(paper_dir)
        target_pdf = paper_dir / f"{paper_stem}.pdf"

    actions: list[dict[str, str]] = []
    if pdf != target_pdf:
        actions.append({"action": "move_pdf", "from": str(pdf), "to": str(target_pdf)})
        if not dry_run:
            paper_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pdf), str(target_pdf))
            source_md = pdf.with_suffix(".md")
            if source_md.exists():
                shutil.move(str(source_md), str(paper_dir / f"{paper_stem}.md"))
            source_images = pdf.parent / f"{pdf.stem}_images"
            if source_images.exists() and source_images.is_dir():
                shutil.move(str(source_images), str(paper_dir / "images"))
    elif not dry_run:
        paper_dir.mkdir(parents=True, exist_ok=True)

    if paper_dir.exists() or dry_run:
        actions.extend(cleanup_paper_dir(paper_dir, paper_stem, dry_run) if paper_dir.exists() else [])

    if not dry_run:
        record_archive(
            raw_path=str(pdf),
            final_path=str(target_pdf),
            metadata={"title": paper_stem.replace("_", " "), "keywords": [], "category_slug": effective_category},
            finder_tags=[effective_category],
            source_method="organize_library",
            category_slug=effective_category,
        )
    return {"pdf": str(pdf), "category": effective_category, "paper_dir": str(paper_dir), "final_pdf": str(target_pdf), "actions": actions}


def audit_top_level(library_dir: Path, allowed: set[str]) -> list[str]:
    problems: list[str] = []
    for child in sorted(library_dir.iterdir(), key=lambda p: p.name) if library_dir.exists() else []:
        if child.name.startswith(".") or child.name in {"README.md"}:
            continue
        if child.is_dir() and child.name in allowed:
            problems.append(str(child))
        elif child.is_file() and child.suffix.lower() == ".pdf":
            problems.append(str(child))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Organize paper library into one folder per paper")
    parser.add_argument("--library-dir", default=str(DEFAULT_LIBRARY_DIR))
    parser.add_argument("--category", help="Category slug from config/paper_categories.json")
    parser.add_argument("--category-table", default=str(CATEGORY_TABLE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args(argv)

    library_dir = Path(args.library_dir).expanduser().resolve()
    category_data = load_categories(Path(args.category_table).expanduser().resolve())
    allowed = set(category_data["allowed"])
    category_slug = validate_category(args.category, category_data)

    problems = audit_top_level(library_dir, allowed)
    if args.audit_only:
        print(json.dumps({"library_dir": str(library_dir), "invalid_top_level": problems}, ensure_ascii=False, indent=2))
        return 1 if problems else 0

    library_dir.mkdir(parents=True, exist_ok=True)
    rows = [organize_pdf(pdf, library_dir, category_slug, args.dry_run, allowed) for pdf in iter_primary_pdfs(library_dir, allowed)]
    print(json.dumps({"library_dir": str(library_dir), "category": category_slug, "count": len(rows), "items": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
