#!/usr/bin/env python3
"""Normalize root-level paper folder names and main PDF/Markdown filenames.

Rules:
- Each paper is a root-level folder under /tmp/paper-downloader-fixtures/Papers.
- Folder name is the canonical paper stem.
- Main PDF and main Markdown must be named <paper_stem>.pdf and <paper_stem>.md.
- Images live in images/.
- inbox/ is skipped.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from markdown_blockify import blockify_markdown_file
except ImportError:  # pragma: no cover
    from .markdown_blockify import blockify_markdown_file  # type: ignore

try:
    from update_paper_properties import update_markdown_properties
except ImportError:  # pragma: no cover
    from .update_paper_properties import update_markdown_properties  # type: ignore

DEFAULT_LIBRARY_DIR = (Path.home() / "Documents" / "PAPER" / "Papers")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
SKIP_ROOT_DIRS = {"inbox"}


def canonical_stem(value: str) -> str:
    stem = value.strip()
    stem = re.sub(r"[<>:\"/\\|?*]", "", stem)
    stem = re.sub(r"\s+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("._")
    return stem[:180] or "paper"


def resolve_conflict(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.name}_{index}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not resolve conflict for {path}")


def choose_main_file(files: list[Path], folder_stem: str, suffix: str) -> Path | None:
    if not files:
        return None
    exact = [p for p in files if p.name == f"{folder_stem}{suffix}"]
    if exact:
        return exact[0]
    non_backup = [p for p in files if not re.search(r"backup|layout|origin|span|supplement|attachment", p.stem, re.I)]
    pool = non_backup or files
    sameish = [p for p in pool if canonical_stem(p.stem) == folder_stem]
    if sameish:
        return sameish[0]
    return max(pool, key=lambda p: p.stat().st_size if p.exists() else 0)


def rewrite_markdown_image_links(md_path: Path, old_folder_name: str) -> None:
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    updated = text.replace(f"{old_folder_name}/", "images/")
    updated = updated.replace(f"./{old_folder_name}/", "images/")
    updated = re.sub(r"(?<![\w./-])[^\s)]+_images/", "images/", updated)
    if updated != text:
        md_path.write_text(updated, encoding="utf-8")


def normalize_markdown_content(md_path: Path) -> bool:
    """Apply reading-note friendly Markdown normalization after file moves."""
    return blockify_markdown_file(md_path)


def normalize_images(folder: Path, dry_run: bool) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    images_dir = folder / "images"
    for child in sorted(folder.iterdir(), key=lambda p: p.name):
        if child.is_dir() and child.name.endswith("_images"):
            actions.append({"action": "rename_images_dir", "from": str(child), "to": str(images_dir)})
            if not dry_run:
                if images_dir.exists():
                    for item in child.rglob("*"):
                        if item.is_file():
                            rel = item.relative_to(child)
                            target = images_dir / rel
                            target.parent.mkdir(parents=True, exist_ok=True)
                            if target.exists():
                                target = target.with_name(f"{target.stem}_dup{target.suffix}")
                            shutil.move(str(item), str(target))
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    shutil.move(str(child), str(images_dir))
    loose_images = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    if loose_images:
        actions.append({"action": "move_loose_images", "from": str(folder), "to": str(images_dir), "count": str(len(loose_images))})
        if not dry_run:
            images_dir.mkdir(exist_ok=True)
            for image in loose_images:
                target = images_dir / image.name
                if target.exists():
                    target = target.with_name(f"{target.stem}_dup{target.suffix}")
                shutil.move(str(image), str(target))
    return actions


def normalize_folder(folder: Path, dry_run: bool) -> dict[str, Any]:
    original_folder = folder
    desired_stem = canonical_stem(folder.name)
    actions: list[dict[str, str]] = []

    if folder.name != desired_stem:
        target_folder = folder.with_name(desired_stem)
        if target_folder.exists():
            actions.append({
                "action": "blocked_duplicate_suffix_creation",
                "from": str(folder),
                "to": str(target_folder),
            })
            return {"folder": str(original_folder), "final_folder": str(folder), "actions": actions, "status": "target_exists_requires_dedupe"}
        actions.append({"action": "rename_folder", "from": str(folder), "to": str(target_folder)})
        if not dry_run:
            shutil.move(str(folder), str(target_folder))
            folder = target_folder
        desired_stem = target_folder.name if dry_run else folder.name

    pdfs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
    mds = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".md"]

    main_pdf = choose_main_file(pdfs, desired_stem, ".pdf")
    if main_pdf and main_pdf.name != f"{desired_stem}.pdf":
        target = folder / f"{desired_stem}.pdf"
        if target.exists():
            target = folder / f"{desired_stem}_main.pdf"
        actions.append({"action": "rename_main_pdf", "from": str(main_pdf), "to": str(target)})
        if not dry_run:
            shutil.move(str(main_pdf), str(target))

    main_md = choose_main_file(mds, desired_stem, ".md")
    if main_md and main_md.name != f"{desired_stem}.md":
        target = folder / f"{desired_stem}.md"
        if target.exists():
            target = folder / f"{desired_stem}_main.md"
        actions.append({"action": "rename_main_md", "from": str(main_md), "to": str(target)})
        if not dry_run:
            shutil.move(str(main_md), str(target))
            main_md = target
    elif main_md:
        main_md = folder / main_md.name

    if not main_md and main_pdf:
        target = folder / f"{desired_stem}.md"
        pdf_name = f"{desired_stem}.pdf" if (folder / f"{desired_stem}.pdf").exists() else main_pdf.name
        actions.append({"action": "create_md_stub", "from": "", "to": str(target)})
        if not dry_run:
            target.write_text(f"# {desired_stem}\n\n[PDF]({pdf_name})\n", encoding="utf-8")
            update_markdown_properties(target, library_dir=library_dir, import_source="local")
            main_md = target

    actions.extend(normalize_images(folder, dry_run))
    if not dry_run:
        for md in folder.glob("*.md"):
            rewrite_markdown_image_links(md, f"{desired_stem}_images")
            if normalize_markdown_content(md):
                actions.append({"action": "blockify_markdown", "from": str(md), "to": str(md)})

    return {"folder": str(original_folder), "final_folder": str(folder), "actions": actions}


def iter_paper_folders(library_dir: Path) -> list[Path]:
    folders = []
    for child in library_dir.iterdir():
        if child.name.startswith(".") or child.name in SKIP_ROOT_DIRS or not child.is_dir():
            continue
        if any(child.glob("*.pdf")) or any(child.glob("*.md")):
            folders.append(child)
    return sorted(folders, key=lambda p: p.name.lower())


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize paper folder and file names")
    parser.add_argument("--library-dir", default=str(DEFAULT_LIBRARY_DIR))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", help="Optional report path")
    args = parser.parse_args()

    library_dir = Path(args.library_dir).expanduser().resolve()
    rows = [normalize_folder(folder, args.dry_run) for folder in iter_paper_folders(library_dir)]
    changed = [row for row in rows if row["actions"]]
    report_path = Path(args.report).expanduser() if args.report else library_dir / "inbox" / f"normalize_paper_names_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    if not args.dry_run:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({"changed": changed}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"checked": len(rows), "changed": len(changed), "dry_run": args.dry_run, "report": str(report_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
