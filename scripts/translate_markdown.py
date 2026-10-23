#!/usr/bin/env python3
"""Translate paper Markdown with ordered, interleaved Chinese/English blocks.

The script keeps worker output isolated: Codex child processes only translate
numbered chunks, while this parent process validates and merges by block_id.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from update_paper_properties import update_markdown_properties
except ImportError:  # pragma: no cover
    from .update_paper_properties import update_markdown_properties  # type: ignore


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_CONCURRENCY = 5
DEFAULT_CHUNK_CHARS = 5500
DEFAULT_MAX_RETRIES = 3
BEGIN_RE = re.compile(r"^<!-- paper-downloader-translation:begin\b.*?-->\s*", re.M)
END_RE = re.compile(r"^<!-- paper-downloader-translation:end -->\s*", re.M)
FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
HTML_TABLE_START_RE = re.compile(r"^\s*<table\b", re.I)
HTML_TABLE_END_RE = re.compile(r"</table>\s*$", re.I)
TRANSLATABLE_LINE_RE = re.compile(r"[A-Za-z\u4e00-\u9fff]")
CITATION_RE = re.compile(r"\[[0-9,;\-\s]+\]")
NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?")
URL_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class MarkdownBlock:
    block_id: str
    kind: str
    text: str
    translatable: bool


@dataclass(frozen=True)
class TranslationChunk:
    chunk_id: str
    source_sha256: str
    blocks: list[MarkdownBlock]


@dataclass(frozen=True)
class GlossaryTerm:
    source: str
    target: str
    note: str = ""


class TranslationError(RuntimeError):
    pass


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def has_translation_marker(text: str) -> bool:
    return bool(BEGIN_RE.search(text) or END_RE.search(text))


def strip_existing_translation(text: str) -> str:
    begin = BEGIN_RE.search(text)
    if not begin:
        return text
    end = END_RE.search(text, begin.end())
    if not end:
        raise TranslationError("Existing translation begin marker has no matching end marker")
    return text[end.end() :].lstrip("\n")


def strip_yaml_frontmatter(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[index + 1 :]).lstrip("\n")
    return text


def is_image_or_link_only(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("!["):
        return True
    return bool(re.match(r"^https?://\S+$", stripped))


def is_translatable_text(text: str, kind: str) -> bool:
    if not text.strip():
        return False
    if kind in {"blank", "code", "html_table", "image"}:
        return False
    if is_image_or_link_only(text):
        return False
    return bool(TRANSLATABLE_LINE_RE.search(text))


def _make_block(index: int, kind: str, lines: list[str], translatable: bool | None = None) -> MarkdownBlock:
    text = "\n".join(lines)
    if translatable is None:
        translatable = is_translatable_text(text, kind)
    return MarkdownBlock(f"b{index:04d}", kind, text, translatable)


def parse_markdown_blocks(text: str) -> list[MarkdownBlock]:
    """Parse Markdown into stable ordered blocks suitable for interleaving."""
    lines = text.splitlines()
    blocks: list[MarkdownBlock] = []
    paragraph: list[str] = []
    index = 1
    in_fence = False
    fence_lines: list[str] = []
    in_html_table = False
    table_lines: list[str] = []

    def append(kind: str, lines_: list[str], translatable: bool | None = None) -> None:
        nonlocal index
        if not lines_:
            return
        blocks.append(_make_block(index, kind, lines_, translatable))
        index += 1

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            append("paragraph", paragraph)
            paragraph = []

    for line in lines:
        if in_fence:
            fence_lines.append(line)
            if FENCE_RE.match(line):
                append("code", fence_lines, False)
                fence_lines = []
                in_fence = False
            continue

        if in_html_table:
            table_lines.append(line)
            if HTML_TABLE_END_RE.search(line):
                append("html_table", table_lines, False)
                table_lines = []
                in_html_table = False
            continue

        if FENCE_RE.match(line):
            flush_paragraph()
            in_fence = True
            fence_lines = [line]
            continue

        if HTML_TABLE_START_RE.match(line):
            flush_paragraph()
            in_html_table = True
            table_lines = [line]
            if HTML_TABLE_END_RE.search(line):
                append("html_table", table_lines, False)
                table_lines = []
                in_html_table = False
            continue

        if not line.strip():
            flush_paragraph()
            append("blank", [line], False)
            continue

        if HEADING_RE.match(line):
            flush_paragraph()
            append("heading", [line])
            continue

        if LIST_RE.match(line):
            flush_paragraph()
            append("list_item", [line])
            continue

        if is_image_or_link_only(line):
            flush_paragraph()
            append("image", [line], False)
            continue

        paragraph.append(line)

    flush_paragraph()
    if fence_lines:
        append("code", fence_lines, False)
    if table_lines:
        append("html_table", table_lines, False)
    return blocks


def build_chunks(blocks: list[MarkdownBlock], source_sha256: str, chunk_chars: int = DEFAULT_CHUNK_CHARS) -> list[TranslationChunk]:
    chunks: list[TranslationChunk] = []
    current: list[MarkdownBlock] = []
    current_chars = 0
    for block in [item for item in blocks if item.translatable]:
        size = len(block.text) + 80
        if current and current_chars + size > chunk_chars:
            chunks.append(TranslationChunk(f"chunk_{len(chunks) + 1:04d}", source_sha256, current))
            current = []
            current_chars = 0
        current.append(block)
        current_chars += size
    if current:
        chunks.append(TranslationChunk(f"chunk_{len(chunks) + 1:04d}", source_sha256, current))
    return chunks


def chunk_payload(chunk: TranslationChunk) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "source_sha256": chunk.source_sha256,
        "blocks": [{"block_id": block.block_id, "kind": block.kind, "text": block.text} for block in chunk.blocks],
    }


def glossary_payload(glossary: list[GlossaryTerm]) -> list[dict[str, str]]:
    return [
        {"source": term.source, "target": term.target, "note": term.note}
        for term in glossary
    ]


def build_prompt(chunk: TranslationChunk, glossary: list[GlossaryTerm] | None = None) -> str:
    payload = json.dumps(chunk_payload(chunk), ensure_ascii=False, indent=2)
    glossary = glossary or []
    glossary_text = json.dumps(glossary_payload(glossary), ensure_ascii=False, indent=2) if glossary else "[]"
    return f"""You are a translation-only Codex child agent.

Translate every block in the JSON payload from English to Chinese.

Hard rules:
- Do not call tools. Do not run shell commands. Do not run Python. Translate directly from the payload.
- Return strict JSON only. Do not use markdown fences.
- Keep chunk_id exactly: {chunk.chunk_id}
- Keep source_sha256 exactly: {chunk.source_sha256}
- Return translations as an array of objects with block_id and translation.
- Translate the full text literally. Do not summarize, explain, polish, smooth, reorganize, omit, or rewrite.
- Preserve Markdown markers such as heading #, list markers, citation text, DOI, URL, file paths, image paths, and HTML tags.
- Do not translate DOI, URL, file paths, image paths, or HTML tag names.
- Apply the glossary exactly when a source term appears. Keep source acronyms unchanged when the glossary target is identical.
- A translation may be awkward. Accuracy and completeness are more important than fluency.

Glossary:
{glossary_text}

Required output shape:
{{"chunk_id":"{chunk.chunk_id}","source_sha256":"{chunk.source_sha256}","translations":[{{"block_id":"b0001","translation":"..."}}]}}

Payload:
{payload}
"""


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])


def load_glossary(path: str | None) -> list[GlossaryTerm]:
    if not path:
        return []
    glossary_path = Path(path).expanduser().resolve()
    if not glossary_path.exists():
        raise TranslationError(f"glossary not found: {glossary_path}")
    if glossary_path.suffix.lower() == ".json":
        return parse_glossary_json(glossary_path)
    return parse_glossary_tsv(glossary_path)


def parse_glossary_json(path: Path) -> list[GlossaryTerm]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("terms", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise TranslationError("glossary JSON must be a list or an object with a terms list")
    terms: list[GlossaryTerm] = []
    for row in rows:
        if not isinstance(row, dict):
            raise TranslationError("glossary JSON rows must be objects")
        source = str(row.get("source", "")).strip()
        target = str(row.get("target", "")).strip()
        note = str(row.get("note", "")).strip()
        if source and target:
            terms.append(GlossaryTerm(source, target, note))
    return terms


def parse_glossary_tsv(path: Path) -> list[GlossaryTerm]:
    terms: list[GlossaryTerm] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            raise TranslationError(f"glossary TSV line {line_number} must contain source and target")
        source = parts[0].strip()
        target = parts[1].strip()
        note = parts[2].strip() if len(parts) > 2 else ""
        if source and target:
            terms.append(GlossaryTerm(source, target, note))
    return terms


def validate_chunk_result(chunk: TranslationChunk, data: dict[str, Any]) -> dict[str, str]:
    if data.get("chunk_id") != chunk.chunk_id:
        raise TranslationError(f"{chunk.chunk_id}: chunk_id mismatch")
    if data.get("source_sha256") != chunk.source_sha256:
        raise TranslationError(f"{chunk.chunk_id}: source_sha256 mismatch")
    rows = data.get("translations")
    if not isinstance(rows, list):
        raise TranslationError(f"{chunk.chunk_id}: translations must be a list")
    expected = [block.block_id for block in chunk.blocks]
    expected_set = set(expected)
    seen: set[str] = set()
    translated: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TranslationError(f"{chunk.chunk_id}: translation row must be an object")
        block_id = row.get("block_id")
        value = row.get("translation")
        if block_id not in expected_set:
            raise TranslationError(f"{chunk.chunk_id}: unexpected block_id {block_id!r}")
        if block_id in seen:
            raise TranslationError(f"{chunk.chunk_id}: duplicate block_id {block_id}")
        if not isinstance(value, str) or not value.strip():
            raise TranslationError(f"{chunk.chunk_id}: empty translation for {block_id}")
        seen.add(block_id)
        translated[block_id] = value.strip()
    missing = [block_id for block_id in expected if block_id not in seen]
    if missing:
        raise TranslationError(f"{chunk.chunk_id}: missing translations {', '.join(missing[:5])}")
    return translated


async def run_codex_translation(
    chunk: TranslationChunk,
    *,
    run_dir: Path,
    model: str,
    max_retries: int,
    glossary: list[GlossaryTerm],
) -> dict[str, str]:
    translated_path = run_dir / "chunks" / "translated" / f"{chunk.chunk_id}.json"
    source_path = run_dir / "chunks" / "source" / f"{chunk.chunk_id}.json"
    prompt_path = run_dir / "chunks" / "prompts" / f"{chunk.chunk_id}.txt"
    raw_path = run_dir / "chunks" / "raw" / f"{chunk.chunk_id}.json"
    for directory in (translated_path.parent, source_path.parent, prompt_path.parent, raw_path.parent):
        directory.mkdir(parents=True, exist_ok=True)
    source_path.write_text(json.dumps(chunk_payload(chunk), ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = build_prompt(chunk, glossary)
    prompt_path.write_text(prompt, encoding="utf-8")

    if translated_path.exists():
        data = json.loads(translated_path.read_text(encoding="utf-8"))
        return validate_chunk_result(chunk, data)

    last_error: str | None = None
    for attempt in range(1, max_retries + 1):
        last_message = run_dir / "chunks" / "last_messages" / f"{chunk.chunk_id}_attempt_{attempt}.txt"
        last_message.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "codex",
            "--ask-for-approval",
            "never",
            "exec",
            "-m",
            model,
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--output-last-message",
            str(last_message),
            "-",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(prompt.encode("utf-8"))
        output = last_message.read_text(encoding="utf-8", errors="replace") if last_message.exists() else stdout.decode("utf-8", errors="replace")
        raw_path.write_text(
            json.dumps(
                {
                    "attempt": attempt,
                    "returncode": proc.returncode,
                    "stdout": stdout.decode("utf-8", errors="replace"),
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "last_message": output,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if proc.returncode != 0:
            last_error = f"codex exited {proc.returncode}"
            continue
        try:
            data = extract_json_object(output)
            translated = validate_chunk_result(chunk, data)
        except Exception as exc:  # noqa: BLE001 - preserve retry reason
            last_error = str(exc)
            continue
        translated_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return translated
    raise TranslationError(f"{chunk.chunk_id}: failed after {max_retries} attempts: {last_error}")


async def translate_chunks(
    chunks: list[TranslationChunk],
    *,
    run_dir: Path,
    model: str,
    concurrency: int,
    max_retries: int,
    resume: bool,
    glossary: list[GlossaryTerm],
) -> dict[str, str]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    all_translations: dict[str, str] = {}
    failures: list[dict[str, str]] = []

    async def one(chunk: TranslationChunk) -> None:
        translated_path = run_dir / "chunks" / "translated" / f"{chunk.chunk_id}.json"
        if not resume and translated_path.exists():
            translated_path.unlink()
        async with semaphore:
            try:
                result = await run_codex_translation(chunk, run_dir=run_dir, model=model, max_retries=max_retries, glossary=glossary)
                all_translations.update(result)
            except Exception as exc:  # noqa: BLE001 - collect all failures
                failures.append({"chunk_id": chunk.chunk_id, "error": str(exc)})

    await asyncio.gather(*(one(chunk) for chunk in chunks))
    if failures:
        failure_path = run_dir / "failures.json"
        failure_path.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
        raise TranslationError(f"{len(failures)} chunk(s) failed; see {failure_path}")
    return all_translations


def validate_all_translations(blocks: list[MarkdownBlock], translations: dict[str, str]) -> None:
    expected = [block.block_id for block in blocks if block.translatable]
    if set(translations) != set(expected):
        missing = sorted(set(expected) - set(translations))
        extra = sorted(set(translations) - set(expected))
        raise TranslationError(f"translation block mismatch missing={missing[:5]} extra={extra[:5]}")
    for block_id in expected:
        if not translations[block_id].strip():
            raise TranslationError(f"empty translation for {block_id}")


def collect_markers(pattern: re.Pattern[str], text: str) -> list[str]:
    return sorted(set(match.group(0).rstrip(".,;:)") for match in pattern.finditer(text)))


def glossary_hits(blocks: list[MarkdownBlock], glossary: list[GlossaryTerm]) -> dict[str, list[GlossaryTerm]]:
    hits: dict[str, list[GlossaryTerm]] = {}
    for block in blocks:
        if not block.translatable:
            continue
        lowered = block.text.lower()
        matched = [term for term in glossary if term.source.lower() in lowered]
        if matched:
            hits[block.block_id] = matched
    return hits


def run_static_qa(
    blocks: list[MarkdownBlock],
    translations: dict[str, str],
    glossary: list[GlossaryTerm],
    chunks: list[TranslationChunk],
) -> dict[str, Any]:
    validate_all_translations(blocks, translations)
    by_id = {block.block_id: block for block in blocks}
    issues: list[dict[str, Any]] = []
    hits = glossary_hits(blocks, glossary)
    for block_id, terms in hits.items():
        translated = translations.get(block_id, "")
        for term in terms:
            if term.target not in translated:
                issues.append(
                    {
                        "severity": "warning",
                        "type": "glossary_missing",
                        "block_id": block_id,
                        "source": term.source,
                        "expected_target": term.target,
                    }
                )
    for block_id, translated in translations.items():
        source = by_id[block_id].text
        for citation in collect_markers(CITATION_RE, source):
            if citation not in translated:
                issues.append({"severity": "warning", "type": "citation_missing", "block_id": block_id, "marker": citation})
        source_numbers = collect_markers(NUMBER_RE, source)
        translated_numbers = set(collect_markers(NUMBER_RE, translated))
        for number in source_numbers:
            if number not in translated_numbers:
                issues.append({"severity": "warning", "type": "number_missing", "block_id": block_id, "marker": number})
        for url in collect_markers(URL_RE, source):
            if url not in translated:
                issues.append({"severity": "warning", "type": "url_missing", "block_id": block_id, "marker": url})
    boundaries: list[dict[str, Any]] = []
    for previous, current in zip(chunks, chunks[1:]):
        boundaries.append(
            {
                "previous_chunk_id": previous.chunk_id,
                "current_chunk_id": current.chunk_id,
                "previous_block_id": previous.blocks[-1].block_id if previous.blocks else None,
                "current_block_id": current.blocks[0].block_id if current.blocks else None,
            }
        )
    return {
        "schema": "paper-downloader.markdown_translation_qa.v1",
        "status": "passed" if not issues else "issues_found",
        "issue_count": len(issues),
        "issues": issues,
        "glossary_term_count": len(glossary),
        "glossary_hit_block_count": len(hits),
        "boundary_count": len(boundaries),
        "boundaries": boundaries,
    }


def write_qa_reports(run_dir: Path, qa: dict[str, Any]) -> tuple[Path, Path]:
    json_path = run_dir / "qa_report.json"
    md_path = run_dir / "qa_report.md"
    json_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Translation QA Report",
        "",
        f"- Status: {qa['status']}",
        f"- Issues: {qa['issue_count']}",
        f"- Glossary terms: {qa['glossary_term_count']}",
        f"- Glossary hit blocks: {qa['glossary_hit_block_count']}",
        f"- Chunk boundaries: {qa['boundary_count']}",
        "",
        "## Issues",
        "",
    ]
    if qa["issues"]:
        for issue in qa["issues"]:
            lines.append(f"- [{issue['severity']}] {issue['type']} at {issue.get('block_id', 'n/a')}: {issue}")
    else:
        lines.append("- No static QA issues found.")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def render_interleaved(blocks: list[MarkdownBlock], translations: dict[str, str], source_sha256: str) -> str:
    validate_all_translations(blocks, translations)
    output = [
        f"<!-- paper-downloader-translation:begin zh-CN mode=interleaved source_sha256={source_sha256} -->",
        "",
    ]
    for block in blocks:
        if block.translatable:
            output.append(f"<!-- zh:{block.block_id} -->")
            output.append(translations[block.block_id])
            output.append("")
            output.append(f"<!-- en:{block.block_id} -->")
            output.append(block.text)
        else:
            output.append(block.text)
        output.append("")
    while output and output[-1] == "":
        output.pop()
    output.extend(["", "<!-- paper-downloader-translation:end -->", ""])
    return "\n".join(output)


def write_atomic(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def make_run_dir(path: Path) -> Path:
    root = (Path(__file__).resolve().parent / ".." / "logs" / "translation_runs").resolve()
    run_dir = root / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{path.stem}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def write_manifest(run_dir: Path, data: dict[str, Any]) -> None:
    (run_dir / "manifest.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def default_sidecar_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.translation.zh-cn{path.suffix}")


def resolve_output_path(path: Path, output_mode: str, output_path: str | None) -> Path:
    if output_path:
        return Path(output_path).expanduser().resolve()
    if output_mode == "sidecar":
        return default_sidecar_path(path)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate paper Markdown into interleaved Chinese/English blocks")
    parser.add_argument("path", help="Markdown file to translate")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    parser.add_argument("--mode", choices=["interleaved"], default="interleaved")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    parser.add_argument("--resume", action="store_true", help="Reuse translated chunk JSON files in the run directory")
    parser.add_argument("--run-dir", help="Existing or desired translation run directory")
    parser.add_argument("--apply", action="store_true", help="Write the interleaved translation into the Markdown file")
    parser.add_argument("--dry-run", action="store_true", help="Only print chunk plan; do not invoke Codex or write the Markdown file")
    parser.add_argument("--replace-existing-translation", action="store_true", help="Replace an existing translation marker block")
    parser.add_argument("--output-mode", choices=["sidecar", "in-place"], default="sidecar", help="Write to a sidecar file by default, or explicitly overwrite the source Markdown")
    parser.add_argument("--output-path", help="Custom sidecar output Markdown path")
    parser.add_argument("--glossary", help="JSON or TSV glossary with source, target, optional note columns")
    parser.add_argument("--qa", action="store_true", help="Write a static QA report after successful translation")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    path = Path(args.path).expanduser().resolve()
    if not path.exists() or path.suffix.lower() != ".md":
        raise SystemExit(f"Markdown file not found: {path}")
    glossary = load_glossary(args.glossary)
    output_path = resolve_output_path(path, args.output_mode, args.output_path)
    original = path.read_text(encoding="utf-8", errors="replace")
    if args.output_mode == "in-place" and has_translation_marker(original):
        if not args.replace_existing_translation:
            raise SystemExit("Existing paper-downloader translation marker found; use --replace-existing-translation to replace it")
        source = strip_existing_translation(original)
    else:
        source = original
    source = strip_yaml_frontmatter(source)
    source_sha256 = sha256_text(source)
    blocks = parse_markdown_blocks(source)
    chunks = build_chunks(blocks, source_sha256, chunk_chars=args.chunk_chars)
    translatable_count = sum(1 for block in blocks if block.translatable)
    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else make_run_dir(path)
    run_dir.mkdir(parents=True, exist_ok=True)
    glossary_snapshot = None
    if glossary:
        glossary_snapshot = run_dir / "glossary.json"
        glossary_snapshot.write_text(json.dumps({"terms": glossary_payload(glossary)}, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "schema": "paper-downloader.markdown_translation.v1",
        "path": str(path),
        "output_mode": args.output_mode,
        "output_path": str(output_path),
        "run_dir": str(run_dir),
        "model": args.model,
        "mode": args.mode,
        "concurrency": args.concurrency,
        "chunk_chars": args.chunk_chars,
        "glossary_path": str(Path(args.glossary).expanduser().resolve()) if args.glossary else None,
        "glossary_snapshot": str(glossary_snapshot) if glossary_snapshot else None,
        "glossary_term_count": len(glossary),
        "qa_enabled": bool(args.qa),
        "source_sha256": source_sha256,
        "block_count": len(blocks),
        "translatable_block_count": translatable_count,
        "chunk_count": len(chunks),
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "block_ids": [block.block_id for block in chunk.blocks],
                "char_count": sum(len(block.text) for block in chunk.blocks),
            }
            for chunk in chunks
        ],
    }
    write_manifest(run_dir, manifest)
    if args.dry_run or not args.apply:
        print(json.dumps({**manifest, "dry_run": True, "apply": args.apply}, ensure_ascii=False, indent=2))
        return 0
    translations = await translate_chunks(
        chunks,
        run_dir=run_dir,
        model=args.model,
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        resume=args.resume,
        glossary=glossary,
    )
    # Re-read before writing so a concurrent edit cannot be silently overwritten.
    current = path.read_text(encoding="utf-8", errors="replace")
    current_source = strip_existing_translation(current) if args.output_mode == "in-place" and args.replace_existing_translation and has_translation_marker(current) else current
    current_source = strip_yaml_frontmatter(current_source)
    if sha256_text(current_source) != source_sha256:
        raise TranslationError("source changed during translation")
    rendered = render_interleaved(blocks, translations, source_sha256)
    backup = None
    if args.output_mode == "in-place":
        backup = path.with_name(f"{path.stem}.translation-backup-{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}")
        shutil.copy2(path, backup)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(output_path, rendered)
    property_updates = []
    if args.output_mode == "sidecar":
        property_updates.append(update_markdown_properties(path, translation_path=output_path))
        property_updates.append(update_markdown_properties(output_path, source_path=path))
    else:
        property_updates.append(update_markdown_properties(path))
    qa_json_path = None
    qa_md_path = None
    qa_status = None
    if args.qa:
        qa = run_static_qa(blocks, translations, glossary, chunks)
        qa_json_path, qa_md_path = write_qa_reports(run_dir, qa)
        qa_status = qa["status"]
    manifest["status"] = "success"
    manifest["backup"] = str(backup) if backup else None
    manifest["translated_block_count"] = len(translations)
    manifest["property_updates"] = property_updates
    manifest["qa_status"] = qa_status
    manifest["qa_report_json"] = str(qa_json_path) if qa_json_path else None
    manifest["qa_report_md"] = str(qa_md_path) if qa_md_path else None
    write_manifest(run_dir, manifest)
    print(
        json.dumps(
            {
                "status": "success",
                "path": str(path),
                "output_path": str(output_path),
                "output_mode": args.output_mode,
                "backup": str(backup) if backup else None,
                "run_dir": str(run_dir),
                "qa_status": qa_status,
                "qa_report_md": str(qa_md_path) if qa_md_path else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except TranslationError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
