#!/usr/bin/env python3
"""Update Obsidian-friendly paper properties on canonical paper Markdown files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_LIBRARY_DIR = (Path.home() / "Documents" / "PAPER" / "Papers")
ALLOWED_IMPORT_SOURCES = {"local", "paper-downloader"}
MANAGED_KEYS = {
    "paper_key",
    "p0_local_pdf",
    "p0_local_md",
    "p0_local_assets_dir",
    "p0_import_source",
    "paper_stem",
    "paper_pdf",
    "paper_pdf_path",
    "translation_zh_cn",
    "translation_zh_cn_path",
    "translation_status",
    "paper_folder",
    "source_paper",
    "source_paper_path",
}


def yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def split_frontmatter(text: str) -> tuple[list[str], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return [], text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return [line.rstrip("\n") for line in lines[1:index]], "".join(lines[index + 1 :]).lstrip("\n")
    return [], text


def render_frontmatter(existing: list[str], updates: dict[str, str]) -> str:
    rendered: list[str] = []
    seen: set[str] = set()
    for line in existing:
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key in MANAGED_KEYS and key in updates:
            if key not in seen:
                rendered.append(f"{key}: {yaml_quote(updates[key])}")
                seen.add(key)
            continue
        rendered.append(line)
    for key, value in updates.items():
        if key not in seen:
            rendered.append(f"{key}: {yaml_quote(value)}")
    return "---\n" + "\n".join(rendered).rstrip() + "\n---\n\n"


def wikilink(filename: str, label: str | None = None) -> str:
    return f"[[{filename}|{label}]]" if label else f"[[{filename}]]"


def _vault_relative(path: Path, library_dir: Path) -> str:
    try:
        return path.resolve().relative_to(library_dir.resolve().parent).as_posix()
    except ValueError:
        return path.name


def properties_for(
    path: Path,
    *,
    library_dir: Path = DEFAULT_LIBRARY_DIR,
    import_source: str | None = None,
    translation_path: Path | None = None,
    source_path: Path | None = None,
) -> dict[str, str]:
    if import_source is not None and import_source not in ALLOWED_IMPORT_SOURCES:
        allowed = ", ".join(sorted(ALLOWED_IMPORT_SOURCES))
        raise ValueError(f"Unknown import source {import_source!r}. Allowed: {allowed}")
    folder = path.parent
    stem = folder.name
    pdf_path = folder / f"{stem}.pdf"
    # Canonical paper records use the PAPER P0 schema. Existing legacy keys
    # are intentionally preserved by render_frontmatter, not silently migrated.
    if path.name == f"{stem}.md":
        updates = {
            "paper_key": stem,
            "p0_local_md": _vault_relative(path, library_dir),
        }
        if pdf_path.exists():
            updates["p0_local_pdf"] = _vault_relative(pdf_path, library_dir)
        if (folder / "images").is_dir():
            updates["p0_local_assets_dir"] = _vault_relative(folder / "images", library_dir)
        if import_source is not None:
            updates["p0_import_source"] = import_source
        return updates

    # Translation sidecars are not classified paper records. Keep their
    # existing navigation contract separate from canonical paper properties.
    updates = {"paper_stem": stem, "paper_folder": folder.name}
    if pdf_path.exists():
        updates["paper_pdf"] = wikilink(pdf_path.name, "PDF")
        updates["paper_pdf_path"] = pdf_path.name
    if translation_path and translation_path.exists():
        updates["translation_status"] = "translated"
        updates["translation_zh_cn"] = wikilink(translation_path.stem, "中文译文")
        updates["translation_zh_cn_path"] = translation_path.name
    elif path.name.endswith(".translation.zh-cn.md"):
        updates["translation_status"] = "translated"
    else:
        default_translation = folder / f"{stem}.translation.zh-cn.md"
        updates["translation_status"] = "translated" if default_translation.exists() else "not_translated"
        if default_translation.exists():
            updates["translation_zh_cn"] = wikilink(default_translation.stem, "中文译文")
            updates["translation_zh_cn_path"] = default_translation.name
    if source_path and source_path.exists():
        updates["source_paper"] = wikilink(source_path.stem, "英文原文")
        updates["source_paper_path"] = source_path.name
    return updates


def update_markdown_properties(
    path: Path,
    *,
    library_dir: Path = DEFAULT_LIBRARY_DIR,
    import_source: str | None = None,
    translation_path: Path | None = None,
    source_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    path = path.expanduser().resolve()
    original = path.read_text(encoding="utf-8", errors="replace")
    existing, body = split_frontmatter(original)
    updates = properties_for(
        path,
        library_dir=library_dir,
        import_source=import_source,
        translation_path=translation_path,
        source_path=source_path,
    )
    updated = render_frontmatter(existing, updates) + body
    changed = updated != original
    if changed and not dry_run:
        path.write_text(updated, encoding="utf-8")
    return {"path": str(path), "changed": changed, "properties": updates}


def iter_canonical_markdown(library_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for folder in sorted(library_dir.iterdir(), key=lambda p: p.name.lower()):
        if not folder.is_dir() or folder.name == "inbox" or folder.name.startswith("."):
            continue
        md = folder / f"{folder.name}.md"
        if md.exists():
            paths.append(md)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Update fixed Obsidian paper properties")
    parser.add_argument("paths", nargs="*", help="Markdown files to update")
    parser.add_argument("--library-dir", default=str(DEFAULT_LIBRARY_DIR))
    parser.add_argument("--import-source", choices=sorted(ALLOWED_IMPORT_SOURCES))
    parser.add_argument("--all", action="store_true", help="Update all canonical Markdown files in the library")
    parser.add_argument("--translation-path", help="Translation Markdown path for a single source Markdown file")
    parser.add_argument("--source-path", help="Source paper Markdown path when updating a translation Markdown file")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    library_dir = Path(args.library_dir).expanduser().resolve()
    paths = [Path(item).expanduser().resolve() for item in args.paths]
    if args.all:
        paths.extend(iter_canonical_markdown(library_dir))
    if not paths:
        raise SystemExit("Provide Markdown paths or use --all")
    translation_path = Path(args.translation_path).expanduser().resolve() if args.translation_path else None
    source_path = Path(args.source_path).expanduser().resolve() if args.source_path else None
    rows = [
        update_markdown_properties(
            path,
            library_dir=library_dir,
            import_source=args.import_source,
            translation_path=translation_path,
            source_path=source_path,
            dry_run=args.dry_run,
        )
        for path in paths
    ]
    print(json.dumps({"checked": len(rows), "changed": sum(1 for row in rows if row["changed"]), "dry_run": args.dry_run, "rows": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
