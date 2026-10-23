#!/usr/bin/env python3
"""Detect and merge duplicate paper folders by DOI/PDF hash.

The script is intentionally conservative. A shared PDF hash can be a real
duplicate, but it can also be a bad placeholder/download reused across folders.
Automatic merges therefore require DOI agreement or a strong stem-level signal.
Ambiguous groups are reported for review and are not moved unless
--include-review is passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_LIBRARY_DIR = (Path.home() / "Documents" / "PAPER" / "Papers")
DEFAULT_NOTES_ROOT = (Path.home() / "Documents" / "PAPER")
TRASH_ROOT = Path.home() / ".Trash" / "paper-downloader-duplicates"
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)


@dataclass(frozen=True)
class PaperFolder:
    folder: Path
    stem: str
    doi: str
    pdf_hash: str
    has_source_md: bool
    has_translation_sidecar: bool
    has_inplace_translation: bool
    file_count: int


@dataclass(frozen=True)
class DuplicateAssessment:
    confidence: str
    safe_to_apply: bool
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_doi(folder: Path) -> str:
    """Extract the paper's own DOI, not any cited DOI in the references.

    Markdown converted from PDFs often contains many reference-list DOIs. Treating
    the first DOI anywhere in the document as identity caused false duplicate
    groups. We therefore trust explicit frontmatter DOI fields first, then only
    scan the opening metadata/title area.
    """
    for md in sorted(folder.glob("*.md")):
        full_text = md.read_text(encoding="utf-8", errors="replace")
        if full_text.startswith("---"):
            lines = full_text.splitlines()
            end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
            if end:
                frontmatter = "\n".join(lines[1:end])
                for line in frontmatter.splitlines():
                    if re.match(r"\s*(doi|DOI)\s*:", line):
                        match = DOI_RE.search(line)
                        if match:
                            return match.group(0).rstrip(".,;").lower()
        text = full_text[:5000]
        match = DOI_RE.search(text)
        if match:
            return match.group(0).rstrip(".,;").lower()
    return ""


def inspect_folder(folder: Path) -> PaperFolder | None:
    if not folder.is_dir() or folder.name == "inbox" or folder.name.startswith("."):
        return None
    stem = folder.name
    pdf = folder / f"{stem}.pdf"
    pdfs = sorted(folder.glob("*.pdf"))
    main_pdf = pdf if pdf.exists() else (pdfs[0] if pdfs else None)
    if not main_pdf:
        return None
    source_md = folder / f"{stem}.md"
    source_text = source_md.read_text(encoding="utf-8", errors="replace") if source_md.exists() else ""
    return PaperFolder(
        folder=folder,
        stem=stem,
        doi=extract_doi(folder),
        pdf_hash=sha256_file(main_pdf),
        has_source_md=source_md.exists() and "paper-downloader-translation:begin" not in source_text,
        has_translation_sidecar=(folder / f"{stem}.translation.zh-cn.md").exists(),
        has_inplace_translation=source_md.exists() and "paper-downloader-translation:begin" in source_text,
        file_count=sum(1 for p in folder.rglob("*") if p.is_file()),
    )


def stem_base(stem: str) -> str:
    """Strip mechanical duplicate suffixes without erasing semantic tokens."""
    return re.sub(r"(_(?:\d+|md|copy|duplicate|dup))+$", "", stem, flags=re.I).lower()


def stem_parts(stem: str) -> dict[str, Any]:
    match = re.match(r"^(?P<year>\d{4})_(?P<author>[^_]+)_(?P<rest>.+)$", stem)
    if not match:
        return {"year": "", "author": "", "tokens": set()}
    rest = re.sub(r"(_(?:\d+|md|copy|duplicate|dup))+$", "", match.group("rest"), flags=re.I)
    tokens = {
        token.lower()
        for token in re.split(r"[_\W]+", rest)
        if len(token) >= 3 and not token.isdigit()
    }
    return {"year": match.group("year"), "author": match.group("author").lower(), "tokens": tokens}


def token_jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def min_pairwise_token_overlap(group: list[PaperFolder]) -> float:
    parts = [stem_parts(item.stem)["tokens"] for item in group]
    if len(parts) < 2:
        return 1.0
    scores = []
    for i, left in enumerate(parts):
        for right in parts[i + 1 :]:
            scores.append(token_jaccard(left, right))
    return min(scores) if scores else 0.0


def assess_group(group: list[PaperFolder]) -> DuplicateAssessment:
    doi_values = {item.doi for item in group if item.doi}
    pdf_hashes = {item.pdf_hash for item in group if item.pdf_hash}
    bases = {stem_base(item.stem) for item in group}
    parts = [stem_parts(item.stem) for item in group]
    years = {part["year"] for part in parts if part["year"]}
    authors = {part["author"] for part in parts if part["author"]}
    overlap = min_pairwise_token_overlap(group)

    reasons: list[str] = []
    warnings: list[str] = []
    doi_match = len(doi_values) == 1
    doi_conflict = len(doi_values) > 1
    same_pdf = len(pdf_hashes) == 1
    mechanical_suffix = len(bases) == 1
    same_year_author = len(years) == 1 and len(authors) == 1

    if doi_match:
        reasons.append("same_doi")
    if doi_conflict:
        warnings.append("conflicting_doi_values")
    if same_pdf:
        reasons.append("same_pdf_hash")
    if mechanical_suffix:
        reasons.append("mechanical_suffix_variant")
    if same_year_author:
        reasons.append("same_year_first_author")
    if overlap >= 0.50:
        reasons.append(f"stem_token_overlap={overlap:.2f}")
    elif same_pdf and not doi_match:
        warnings.append(f"low_stem_token_overlap={overlap:.2f}")

    if doi_match and not doi_conflict:
        return DuplicateAssessment("high", True, tuple(reasons), tuple(warnings))
    if same_pdf and mechanical_suffix and not doi_conflict:
        return DuplicateAssessment("high", True, tuple(reasons), tuple(warnings))
    if same_pdf and same_year_author and overlap >= 0.50 and not doi_conflict:
        return DuplicateAssessment("medium", True, tuple(reasons), tuple(warnings))
    return DuplicateAssessment("review", False, tuple(reasons), tuple(warnings or ["pdf_hash_only_or_weak_metadata_match"]))


def canonical_score(item: PaperFolder) -> tuple[int, int, int, int]:
    return (
        1 if item.has_source_md else 0,
        1 if item.has_translation_sidecar else 0,
        0 if item.has_inplace_translation else 1,
        -len(item.stem),
    )


def find_duplicate_groups(library_dir: Path) -> list[list[PaperFolder]]:
    items = [item for child in library_dir.iterdir() if (item := inspect_folder(child))]
    parent: dict[str, str] = {str(item.folder): str(item.folder) for item in items}
    by_path = {str(item.folder): item for item in items}

    def find(path: str) -> str:
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    def union(left: str, right: str) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    buckets: dict[str, list[str]] = {}
    for item in items:
        if item.doi:
            buckets.setdefault(f"doi:{item.doi}", []).append(str(item.folder))
        buckets.setdefault(f"pdf:{item.pdf_hash}", []).append(str(item.folder))
    for rows in buckets.values():
        for path in rows[1:]:
            union(rows[0], path)
    components: dict[str, list[PaperFolder]] = {}
    for path, item in by_path.items():
        components.setdefault(find(path), []).append(item)
    groups = [sorted(rows, key=lambda item: item.stem) for rows in components.values() if len(rows) > 1]
    groups.sort(key=lambda rows: rows[0].stem.lower())
    return groups


def trash_path_for(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = TRASH_ROOT / stamp
    target = root / path.name
    if not target.exists():
        return target
    for index in range(2, 1000):
        candidate = root / f"{path.name}_{index}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not resolve Trash conflict for {path}")


def add_alias_property(md_path: Path, alias: str, dry_run: bool) -> bool:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        prefix = f"---\naliases:\n  - \"{alias}\"\n---\n\n"
        if not dry_run:
            md_path.write_text(prefix + text, encoding="utf-8")
        return True
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return False
    fm = lines[1:end]
    if alias in "\n".join(fm):
        return False
    insert_at = end
    fm.append("aliases:")
    fm.append(f"  - \"{alias}\"")
    updated = "\n".join([lines[0], *fm, *lines[end:]]) + ("\n" if text.endswith("\n") else "")
    if not dry_run:
        md_path.write_text(updated, encoding="utf-8")
    return True


def rewrite_note_links(root: Path, replacements: dict[str, str], dry_run: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for md in root.rglob("*.md"):
        if ".Trash" in md.parts:
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        updated = text
        for old, new in replacements.items():
            updated = updated.replace(old, new)
        if updated != text:
            rows.append({"path": str(md), "old_refs": [old for old in replacements if old in text]})
            if not dry_run:
                md.write_text(updated, encoding="utf-8")
    return rows


def apply_group(group: list[PaperFolder], notes_root: Path, dry_run: bool, include_review: bool = False) -> dict[str, Any]:
    assessment = assess_group(group)
    canonical = max(group, key=canonical_score)
    duplicates = [item for item in group if item.folder != canonical.folder]
    if not assessment.safe_to_apply and not include_review:
        return {
            "status": "requires_review",
            "confidence": assessment.confidence,
            "reasons": list(assessment.reasons),
            "warnings": list(assessment.warnings),
            "canonical_candidate": str(canonical.folder),
            "duplicates": [str(item.folder) for item in duplicates],
            "stems": [item.stem for item in group],
        }
    replacements = {dup.stem: canonical.stem for dup in duplicates}
    rewritten = rewrite_note_links(notes_root, replacements, dry_run)
    alias_changed = False
    canonical_md = canonical.folder / f"{canonical.stem}.md"
    if canonical_md.exists():
        for duplicate in duplicates:
            alias_changed = add_alias_property(canonical_md, duplicate.stem, dry_run) or alias_changed
    moved = []
    for duplicate in duplicates:
        target = trash_path_for(duplicate.folder)
        moved.append({"from": str(duplicate.folder), "to": str(target)})
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(duplicate.folder), str(target))
    return {
        "status": "applied" if not dry_run else "would_apply",
        "confidence": assessment.confidence,
        "reasons": list(assessment.reasons),
        "warnings": list(assessment.warnings),
        "canonical": str(canonical.folder),
        "duplicates": [str(item.folder) for item in duplicates],
        "replacements": replacements,
        "rewritten_files": rewritten,
        "alias_changed": alias_changed,
        "moved_to_trash": moved,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Dedupe paper folders by DOI/PDF hash")
    parser.add_argument("--library-dir", default=str(DEFAULT_LIBRARY_DIR))
    parser.add_argument("--notes-root", default=str(DEFAULT_NOTES_ROOT))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-review", action="store_true", help="Also apply groups that are marked requires_review")
    parser.add_argument("--only", help="Only apply groups containing this stem")
    args = parser.parse_args()
    library_dir = Path(args.library_dir).expanduser().resolve()
    notes_root = Path(args.notes_root).expanduser().resolve()
    groups = find_duplicate_groups(library_dir)
    if args.only:
        groups = [group for group in groups if any(args.only in item.stem for item in group)]
    rows = [apply_group(group, notes_root, dry_run=not args.apply, include_review=args.include_review) for group in groups]
    print(json.dumps({
        "groups": len(groups),
        "auto_applicable": sum(1 for group in groups if assess_group(group).safe_to_apply),
        "requires_review": sum(1 for group in groups if not assess_group(group).safe_to_apply),
        "applied": args.apply,
        "include_review": args.include_review,
        "rows": rows,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
