#!/usr/bin/env python3
"""Turn converted paper Markdown paragraphs into citation-friendly list blocks.

MinerU and similar PDF converters usually emit long academic paragraphs. This
normalizer rewrites prose paragraphs as unordered-list blocks so each paragraph
or citation-bearing sentence is easy to quote as an Obsidian/Logseq block.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

LIST_MARKERS = ("- ", "* ", "+ ")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
HTML_BLOCK_RE = re.compile(r"^\s*</?(?:div|table|tr|td|th|p|figure|img|video|audio|details|summary|blockquote)\b", re.I)
CITATION_RE = re.compile(r"\([^()]*\b\d{4}[a-z]?[^()]*\)|\[[0-9,\-\s]+\]")
ORDERED_LIST_RE = re.compile(r"^\s*\d+[.)]\s+")
UNORDERED_LIST_RE = re.compile(r"^\s*[-*+]\s+")
PASSTHROUGH_SECTION_HEADINGS = {"contents"}


def is_frontmatter_start(lines: list[str]) -> bool:
    return bool(lines) and lines[0].strip() in {"---", "+++"}


def split_frontmatter(lines: list[str]) -> tuple[list[str], list[str]]:
    if not is_frontmatter_start(lines):
        return [], lines
    marker = lines[0].strip()
    for index in range(1, len(lines)):
        if lines[index].strip() == marker:
            return lines[: index + 1], lines[index + 1 :]
    return [], lines


def is_special_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return True
    if is_list_item_start(line):
        return True
    if stripped.startswith((">", "|", "!", "[!", "<!--", "<img")):
        return True
    if HEADING_RE.match(line) or HTML_BLOCK_RE.match(line):
        return True
    return False


def is_list_item_start(line: str) -> bool:
    return bool(UNORDERED_LIST_RE.match(line) or ORDERED_LIST_RE.match(line))


def is_indented_continuation(line: str) -> bool:
    return bool(re.match(r"^\s{2,}\S", line))


def normalized_heading_text(line: str) -> str | None:
    match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
    if not match:
        return None
    return match.group(1).strip().lower()


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    """Split academic prose conservatively, preserving abbreviations like et al."""
    text = normalize_space(text)
    if not text:
        return []
    pieces: list[str] = []
    start = 0
    for match in re.finditer(r"[.!?](?:[\)\]\}\"\u201d\u2019]+)?\s+", text):
        end = match.end()
        candidate = text[start:end].strip()
        if re.search(r"\b(?:et al|e\.g|i\.e|vs|Dr|Mr|Ms|Prof)\.$", candidate):
            continue
        next_char = text[end : end + 1]
        if next_char and not re.match(r"[A-Z0-9\"\u201c\u2018\[]", next_char):
            continue
        pieces.append(candidate)
        start = end
    tail = text[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces or [text]


def split_citation_blocks(paragraph: str) -> list[str]:
    sentences = split_sentences(paragraph)
    citation_count = len(CITATION_RE.findall(paragraph))
    if citation_count <= 1 or len(sentences) <= 1:
        return [normalize_space(paragraph)]

    blocks: list[str] = []
    pending: list[str] = []
    for sentence in sentences:
        pending.append(sentence)
        joined = normalize_space(" ".join(pending))
        if CITATION_RE.search(joined):
            blocks.append(joined)
            pending = []
    if pending:
        tail = normalize_space(" ".join(pending))
        if blocks:
            blocks[-1] = normalize_space(f"{blocks[-1]} {tail}")
        else:
            blocks.append(tail)
    return [block for block in blocks if block]


def paragraph_to_bullets(paragraph_lines: list[str]) -> list[str]:
    paragraph = normalize_space(" ".join(line.strip() for line in paragraph_lines))
    return [f"- {block}" for block in split_citation_blocks(paragraph)]


def blockify_markdown(text: str) -> str:
    lines = text.splitlines()
    frontmatter, body = split_frontmatter(lines)
    output: list[str] = []
    if frontmatter:
        output.extend(frontmatter)
        if body and body[0].strip():
            output.append("")

    paragraph: list[str] = []
    in_fence = False
    in_html_table = False
    in_list_block = False
    in_passthrough_section = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            output.extend(paragraph_to_bullets(paragraph))
            paragraph = []

    for line in body:
        if FENCE_RE.match(line):
            flush_paragraph()
            in_fence = not in_fence
            output.append(line)
            in_list_block = False
            continue
        if in_fence:
            output.append(line)
            continue
        if re.match(r"^\s*<table\b", line, re.I):
            flush_paragraph()
            in_html_table = True
            output.append(line)
            in_list_block = False
            if re.search(r"</table>\s*$", line, re.I):
                in_html_table = False
            continue
        if in_html_table:
            output.append(line)
            if re.search(r"</table>\s*$", line, re.I):
                in_html_table = False
            continue
        if not line.strip():
            flush_paragraph()
            if output and output[-1] != "":
                output.append("")
            in_list_block = False
            continue
        heading = normalized_heading_text(line)
        if heading is not None:
            flush_paragraph()
            output.append(line)
            in_passthrough_section = heading in PASSTHROUGH_SECTION_HEADINGS
            in_list_block = False
            continue
        if in_passthrough_section:
            output.append(line)
            continue
        if in_list_block and is_indented_continuation(line):
            output.append(line)
            continue
        if is_list_item_start(line):
            flush_paragraph()
            output.append(line)
            in_list_block = True
            continue
        if is_special_line(line):
            flush_paragraph()
            output.append(line)
            in_list_block = False
            continue
        in_list_block = False
        paragraph.append(line)

    flush_paragraph()
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output) + ("\n" if text.endswith("\n") or output else "")


def blockify_markdown_file(path: Path, dry_run: bool = False) -> bool:
    original = path.read_text(encoding="utf-8", errors="ignore")
    updated = blockify_markdown(original)
    changed = updated != original
    if changed and not dry_run:
        path.write_text(updated, encoding="utf-8")
    return changed


def should_skip_path(path: Path, include_inbox: bool) -> bool:
    if path.name.lower() == "readme.md":
        return True
    return not include_inbox and "inbox" in path.parts


def iter_markdown_paths(paths: list[str], include_inbox: bool = False) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*.md") if p.is_file() and not should_skip_path(p, include_inbox)))
        elif path.is_file() and path.suffix.lower() == ".md" and not should_skip_path(path, include_inbox):
            found.append(path)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Rewrite paper Markdown prose into citation-friendly bullet blocks")
    parser.add_argument("paths", nargs="+", help="Markdown files or folders to process")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-inbox", action="store_true", help="Also process Markdown under inbox/")
    args = parser.parse_args()

    changed = []
    paths = iter_markdown_paths(args.paths, include_inbox=args.include_inbox)
    for path in paths:
        if blockify_markdown_file(path, dry_run=args.dry_run):
            changed.append(str(path))
    print(f"checked={len(paths)} changed={len(changed)} dry_run={args.dry_run}")
    for path in changed:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
