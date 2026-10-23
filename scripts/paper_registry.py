"""Persistent SQLite-backed registry for papers downloaded or archived by the skill."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


LOGS_DIR = (Path(__file__).resolve().parent / ".." / "logs").resolve()
REGISTRY_DB = LOGS_DIR / "paper_registry.sqlite"
REGISTRY_JSON = LOGS_DIR / "paper_registry.json"
REGISTRY_MD = LOGS_DIR / "paper_registry.md"
RUN_ARCHIVES_DIR = LOGS_DIR / "download_runs"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_path(value: str | None) -> str | None:
    if not value:
        return None
    return str(Path(value).expanduser().resolve())


def _normalize_list(value: list[str] | None) -> list[str]:
    return value or []


DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\]|)>,;]+", re.IGNORECASE)


def _slug_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value.strip().lower())
    cleaned = re.sub(r"[^0-9a-z\u4e00-\u9fff ]+", "", cleaned)
    return cleaned.replace(" ", "_")[:120]


def _make_key(
    doi: str | None = None,
    final_path: str | None = None,
    raw_path: str | None = None,
    title: str | None = None,
    year: str | None = None,
    first_author: str | None = None,
) -> str:
    if doi:
        return f"doi:{doi.lower()}"
    if final_path:
        return f"final:{_normalize_path(final_path)}"
    if raw_path:
        return f"raw:{_normalize_path(raw_path)}"
    if title:
        suffix = "_".join(_slug_text(value) for value in [first_author or "", year or ""] if value)
        return f"title:{_slug_text(title)}" + (f":{suffix}" if suffix else "")
    raise ValueError("Registry entry requires doi, final_path, raw_path, or title")


def _connect() -> sqlite3.Connection:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(REGISTRY_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS papers (
            key TEXT PRIMARY KEY,
            doi TEXT,
            title TEXT,
            year TEXT,
            first_author TEXT,
            journal TEXT,
            keywords_json TEXT NOT NULL DEFAULT '[]',
            finder_tags_json TEXT NOT NULL DEFAULT '[]',
            raw_path TEXT,
            final_path TEXT,
            category_slug TEXT,
            source_method TEXT,
            status TEXT,
            manual_url TEXT,
            error TEXT,
            last_attempt_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registry_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL DEFAULT 'project_library',
            source_path TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_items (
            collection_id INTEGER NOT NULL,
            paper_key TEXT NOT NULL,
            role TEXT,
            status TEXT,
            notes TEXT,
            order_index INTEGER NOT NULL DEFAULT 0,
            raw_citation TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (collection_id, paper_key),
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
            FOREIGN KEY (paper_key) REFERENCES papers(key) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_updated_at ON papers(updated_at DESC)")
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
    if "category_slug" not in existing_columns:
        conn.execute("ALTER TABLE papers ADD COLUMN category_slug TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_final_path ON papers(final_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_raw_path ON papers(raw_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_category_slug ON papers(category_slug)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collection_items_paper_key ON collection_items(paper_key)")
    _import_json_snapshot_if_needed(conn)
    return conn


def _import_json_snapshot_if_needed(conn: sqlite3.Connection) -> None:
    migrated = conn.execute(
        "SELECT value FROM registry_settings WHERE key = 'json_snapshot_migrated'"
    ).fetchone()
    if migrated is not None:
        return
    if REGISTRY_JSON.exists():
        try:
            snapshot = json.loads(REGISTRY_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            snapshot = {"papers": []}
        for paper in snapshot.get("papers", []):
            _upsert_entry(
                conn,
                {
                    "doi": paper.get("doi"),
                    "title": paper.get("title"),
                    "year": paper.get("year"),
                    "first_author": paper.get("first_author"),
                    "journal": paper.get("journal"),
                    "keywords": paper.get("keywords", []),
                    "finder_tags": paper.get("finder_tags", []),
                    "raw_path": paper.get("raw_path"),
                    "final_path": paper.get("final_path"),
                    "category_slug": paper.get("category_slug"),
                    "source_method": paper.get("source_method"),
                    "status": paper.get("status"),
                    "manual_url": paper.get("manual_url"),
                    "error": paper.get("error"),
                    "last_attempt_at": paper.get("last_attempt_at"),
                },
            )
    conn.execute(
        "INSERT OR REPLACE INTO registry_settings(key, value) VALUES (?, ?)",
        ("json_snapshot_migrated", _now_iso()),
    )
    conn.commit()


def _row_to_entry(row: sqlite3.Row) -> dict[str, Any]:
    entry = dict(row)
    entry["keywords"] = json.loads(entry.pop("keywords_json") or "[]")
    entry["finder_tags"] = json.loads(entry.pop("finder_tags_json") or "[]")
    return entry


def _all_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT key, doi, title, year, first_author, journal, keywords_json, finder_tags_json,
               raw_path, final_path, category_slug, source_method, status, manual_url, error,
               last_attempt_at, created_at, updated_at
        FROM papers
        ORDER BY updated_at DESC
        """
    ).fetchall()
    return [_row_to_entry(row) for row in rows]


def load_registry() -> dict[str, Any]:
    with _connect() as conn:
        return {"papers": _all_entries(conn)}


def save_registry(registry: dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_JSON.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    REGISTRY_MD.write_text(render_registry_markdown(registry), encoding="utf-8")


def _export_registry(conn: sqlite3.Connection) -> None:
    save_registry({"papers": _all_entries(conn)})


def _merge_payload(existing: sqlite3.Row | None, payload: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        merged = {
            "doi": None,
            "title": None,
            "year": None,
            "first_author": None,
            "journal": None,
            "keywords": [],
            "finder_tags": [],
            "raw_path": None,
            "final_path": None,
            "category_slug": None,
            "source_method": None,
            "status": None,
            "manual_url": None,
            "error": None,
            "last_attempt_at": None,
        }
        created_at = _now_iso()
    else:
        merged = {
            "doi": existing["doi"],
            "title": existing["title"],
            "year": existing["year"],
            "first_author": existing["first_author"],
            "journal": existing["journal"],
            "keywords": json.loads(existing["keywords_json"] or "[]"),
            "finder_tags": json.loads(existing["finder_tags_json"] or "[]"),
            "raw_path": existing["raw_path"],
            "final_path": existing["final_path"],
            "category_slug": existing["category_slug"] if "category_slug" in existing.keys() else None,
            "source_method": existing["source_method"],
            "status": existing["status"],
            "manual_url": existing["manual_url"],
            "error": existing["error"],
            "last_attempt_at": existing["last_attempt_at"],
        }
        created_at = existing["created_at"]

    for field, value in payload.items():
        if field in {"keywords", "finder_tags"}:
            if value not in (None, [], ""):
                merged[field] = _normalize_list(value)
        elif value not in (None, "", []):
            merged[field] = value

    return {
        **merged,
        "created_at": created_at,
        "updated_at": _now_iso(),
    }


def _upsert_entry(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    key = _make_key(
        doi=payload.get("doi"),
        final_path=payload.get("final_path"),
        raw_path=payload.get("raw_path"),
        title=payload.get("title"),
        year=payload.get("year"),
        first_author=payload.get("first_author"),
    )
    existing = conn.execute("SELECT * FROM papers WHERE key = ?", (key,)).fetchone()
    merged = _merge_payload(existing, payload)
    conn.execute(
        """
        INSERT INTO papers (
            key, doi, title, year, first_author, journal, keywords_json, finder_tags_json,
            raw_path, final_path, category_slug, source_method, status, manual_url, error,
            last_attempt_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            doi = excluded.doi,
            title = excluded.title,
            year = excluded.year,
            first_author = excluded.first_author,
            journal = excluded.journal,
            keywords_json = excluded.keywords_json,
            finder_tags_json = excluded.finder_tags_json,
            raw_path = excluded.raw_path,
            final_path = excluded.final_path,
            category_slug = excluded.category_slug,
            source_method = excluded.source_method,
            status = excluded.status,
            manual_url = excluded.manual_url,
            error = excluded.error,
            last_attempt_at = excluded.last_attempt_at,
            updated_at = excluded.updated_at
        """,
        (
            key,
            merged.get("doi"),
            merged.get("title"),
            merged.get("year"),
            merged.get("first_author"),
            merged.get("journal"),
            json.dumps(_normalize_list(merged.get("keywords")), ensure_ascii=False),
            json.dumps(_normalize_list(merged.get("finder_tags")), ensure_ascii=False),
            _normalize_path(merged.get("raw_path")),
            _normalize_path(merged.get("final_path")),
            merged.get("category_slug"),
            merged.get("source_method"),
            merged.get("status"),
            merged.get("manual_url"),
            merged.get("error"),
            merged.get("last_attempt_at"),
            merged.get("created_at"),
            merged.get("updated_at"),
        ),
    )
    row = conn.execute("SELECT * FROM papers WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to persist registry entry for key {key}")
    return _row_to_entry(row)


def record_download_result(result: dict[str, Any]) -> dict[str, Any]:
    with _connect() as conn:
        entry = _upsert_entry(
            conn,
            {
                "doi": result.get("doi"),
                "title": result.get("title"),
                "status": result.get("status") or ("downloaded" if result.get("success") else "failed"),
                "source_method": result.get("method"),
                "raw_path": _normalize_path(result.get("file_path")),
                "manual_url": result.get("manual_url"),
                "error": result.get("error"),
                "last_attempt_at": _now_iso(),
            },
        )
        conn.commit()
        _export_registry(conn)
        return entry


def record_archive(
    *,
    doi: str | None = None,
    raw_path: str | None = None,
    final_path: str,
    metadata: dict[str, Any] | None = None,
    finder_tags: list[str] | None = None,
    source_method: str | None = None,
    status: str = "archived",
    category_slug: str | None = None,
    manual_url: str | None = None,
) -> dict[str, Any]:
    metadata = metadata or {}
    with _connect() as conn:
        entry = _upsert_entry(
            conn,
            {
                "doi": doi,
                "title": metadata.get("title"),
                "year": metadata.get("year"),
                "first_author": metadata.get("first_author"),
                "journal": metadata.get("journal"),
                "keywords": metadata.get("keywords", []),
                "finder_tags": finder_tags or [],
                "raw_path": _normalize_path(raw_path),
                "final_path": _normalize_path(final_path),
                "category_slug": category_slug or metadata.get("category_slug"),
                "source_method": source_method,
                "status": status,
                "manual_url": manual_url,
                "last_attempt_at": _now_iso(),
            },
        )
        conn.commit()
        _export_registry(conn)
        return entry


def find_registry_entries(
    *,
    query: str | None = None,
    doi: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if doi:
        clauses.append("doi = ?")
        params.append(doi)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if query:
        wildcard = f"%{query}%"
        clauses.append("(title LIKE ? OR first_author LIKE ? OR journal LIKE ? OR doi LIKE ?)")
        params.extend([wildcard, wildcard, wildcard, wildcard])

    sql = """
        SELECT key, doi, title, year, first_author, journal, keywords_json, finder_tags_json,
               raw_path, final_path, category_slug, source_method, status, manual_url, error,
               last_attempt_at, created_at, updated_at
        FROM papers
    """
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(max(1, limit))

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_entry(row) for row in rows]


def _collection_name_from_md(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.lstrip("#").strip()
    return path.stem


def _split_table_row(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def _header_index(headers: list[str], *names: str) -> int | None:
    lowered = [header.lower().strip() for header in headers]
    for name in names:
        name_lower = name.lower()
        for index, header in enumerate(lowered):
            if name_lower in header:
                return index
    return None


def _cell(cells: list[str], index: int | None) -> str | None:
    if index is None or index >= len(cells):
        return None
    value = re.sub(r"<br\s*/?>", " ", cells[index], flags=re.IGNORECASE).strip()
    return value or None


def _extract_title_from_line(line: str) -> str | None:
    cleaned = re.sub(r"^\s*[-*+]\s+", "", line).strip()
    cleaned = re.sub(r"\b10\.\d{4,9}/[^\s\]|)>,;]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[[ xX]\]", "", cleaned).strip(" -|")
    return cleaned[:240] or None


def _metadata_from_failed_filename(path_value: str | None) -> dict[str, Any]:
    if not path_value:
        return {}
    stem = Path(path_value).stem
    match = re.match(r"^(?P<year>\d{4})_(?P<author>[^_]+)_(?P<rest>.+)$", stem)
    if not match:
        return {"title": stem.replace("_", " ")[:240]}

    year = match.group("year")
    first_author = match.group("author")
    rest = match.group("rest")
    parts = [part for part in rest.split("_") if part]
    journal = parts[-1] if parts else None
    keywords = parts[:-1] if len(parts) > 1 else parts
    title = " ".join(keywords).replace("-", " ")
    return {
        "year": year,
        "first_author": first_author,
        "journal": journal,
        "keywords": keywords,
        "title": title[:240] if title else stem.replace("_", " ")[:240],
    }


def _normalize_manifest_status(value: str | None) -> str:
    if not value:
        return "failed"
    lowered = value.strip().lower()
    if lowered in {"downloaded", "archived", "existing", "linked"}:
        return lowered
    if "fail" in lowered or "error" in lowered:
        return "failed"
    if "manual" in lowered:
        return "manual_download"
    return lowered


def import_failure_manifest(
    manifest_path: str,
    *,
    collection_name: str | None = None,
    collection_type: str = "download_retry_queue",
) -> dict[str, Any]:
    path = Path(manifest_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Failure manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        rows = data.get("papers") or data.get("items") or data.get("results") or []
    elif isinstance(data, list):
        rows = data
    else:
        raise ValueError("Failure manifest must be a JSON list or an object containing papers/items/results")
    if not isinstance(rows, list):
        raise ValueError("Failure manifest paper rows must be a list")

    inferred_collection_name = collection_name or path.stem
    imported = 0
    skipped = 0
    imported_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    imported_at = _now_iso()
    with _connect() as conn:
        collection = _upsert_collection(
            conn,
            name=inferred_collection_name,
            collection_type=collection_type,
            source_path=str(path),
            metadata={"import_format": "failure_manifest"},
        )
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                skipped += 1
                skipped_rows.append({"index": index, "reason": "row_not_object", "row": row})
                continue
            file_path = (
                row.get("file")
                or row.get("archive_path")
                or row.get("final_path")
                or row.get("raw_path")
                or row.get("path")
            )
            inferred = _metadata_from_failed_filename(file_path)
            status = _normalize_manifest_status(row.get("status"))
            manual_url = row.get("url") or row.get("pdf_url") or row.get("manual_url")
            doi = row.get("doi") or row.get("DOI")
            title = row.get("title") or inferred.get("title")
            if not any([doi, title, file_path, manual_url]):
                skipped += 1
                skipped_rows.append({"index": index, "reason": "no_identifier", "row": row})
                continue
            paper = _upsert_entry(
                conn,
                {
                    "doi": doi,
                    "title": title,
                    "year": str(row.get("year") or inferred.get("year") or ""),
                    "first_author": row.get("first_author") or row.get("author") or inferred.get("first_author"),
                    "journal": row.get("journal") or row.get("venue") or inferred.get("journal"),
                    "keywords": row.get("keywords") or inferred.get("keywords") or [],
                    "finder_tags": row.get("finder_tags") or row.get("tags") or [],
                    "raw_path": _normalize_path(file_path),
                    "source_method": row.get("method") or row.get("source_method") or "failure_manifest_import",
                    "status": status,
                    "manual_url": manual_url,
                    "error": row.get("error") or row.get("notes"),
                    "last_attempt_at": row.get("last_attempt_at") or _now_iso(),
                },
            )
            now = _now_iso()
            conn.execute(
                """
                INSERT INTO collection_items (
                    collection_id, paper_key, role, status, notes, order_index,
                    raw_citation, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_id, paper_key) DO UPDATE SET
                    role = excluded.role,
                    status = excluded.status,
                    notes = excluded.notes,
                    order_index = excluded.order_index,
                    raw_citation = excluded.raw_citation,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    collection["id"],
                    paper["key"],
                    row.get("role") or "retry_candidate",
                    status,
                    row.get("error") or row.get("notes"),
                    index,
                    row.get("raw_citation") or title or manual_url,
                    json.dumps({"source_manifest": str(path), "manual_url": manual_url}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            imported += 1
            imported_rows.append(
                {
                    "index": index,
                    "paper_key": paper["key"],
                    "status": status,
                    "doi": paper.get("doi"),
                    "title": paper.get("title"),
                    "year": paper.get("year"),
                    "first_author": paper.get("first_author"),
                    "journal": paper.get("journal"),
                    "raw_path": paper.get("raw_path"),
                    "manual_url": paper.get("manual_url"),
                    "error": paper.get("error"),
                    "original_row": row,
                }
            )
        conn.commit()
        _export_registry(conn)
    run_archive_path = _write_failure_manifest_run_archive(
        source_path=path,
        collection_name=inferred_collection_name,
        collection_type=collection_type,
        imported_at=imported_at,
        imported_rows=imported_rows,
        skipped_rows=skipped_rows,
    )
    return {
        "collection": inferred_collection_name,
        "source_path": str(path),
        "imported": imported,
        "skipped": skipped,
        "run_archive": str(run_archive_path),
    }


def _safe_filename_stem(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_.-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned[:120] or "download_run"


def _write_failure_manifest_run_archive(
    *,
    source_path: Path,
    collection_name: str,
    collection_type: str,
    imported_at: str,
    imported_rows: list[dict[str, Any]],
    skipped_rows: list[dict[str, Any]],
) -> Path:
    RUN_ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = imported_at.replace("-", "").replace(":", "").replace("T", "_")
    archive_path = RUN_ARCHIVES_DIR / f"{timestamp}_{_safe_filename_stem(collection_name)}.json"
    payload = {
        "schema": "paper-downloader.download_run.v1",
        "kind": "failure_manifest_import",
        "imported_at": imported_at,
        "source_manifest": str(source_path),
        "collection": {
            "name": collection_name,
            "type": collection_type,
        },
        "summary": {
            "imported": len(imported_rows),
            "skipped": len(skipped_rows),
            "status_counts": {
                status: sum(1 for row in imported_rows if row.get("status") == status)
                for status in sorted({row.get("status") for row in imported_rows if row.get("status")})
            },
        },
        "items": imported_rows,
        "skipped_items": skipped_rows,
    }
    archive_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return archive_path


def _parse_markdown_papers(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    items: list[dict[str, Any]] = []
    index = 0
    order_index = 0
    while index < len(lines):
        line = lines[index]
        if line.strip().startswith("|"):
            headers = _split_table_row(line)
            if index + 1 < len(lines):
                maybe_separator = _split_table_row(lines[index + 1])
                if _is_separator_row(maybe_separator):
                    title_i = _header_index(headers, "title", "题名", "标题", "文献", "书名")
                    doi_i = _header_index(headers, "doi")
                    author_i = _header_index(headers, "author", "作者")
                    year_i = _header_index(headers, "year", "年份", "年度")
                    journal_i = _header_index(headers, "journal", "venue", "期刊", "来源")
                    tags_i = _header_index(headers, "tag", "标签", "关键词")
                    status_i = _header_index(headers, "status", "状态")
                    notes_i = _header_index(headers, "note", "备注", "说明")
                    index += 2
                    while index < len(lines) and lines[index].strip().startswith("|"):
                        cells = _split_table_row(lines[index])
                        if not _is_separator_row(cells):
                            raw = lines[index].strip()
                            doi_match = DOI_RE.search(raw)
                            title = _cell(cells, title_i)
                            doi = _cell(cells, doi_i) or (doi_match.group(0) if doi_match else None)
                            if title or doi:
                                order_index += 1
                                items.append(
                                    {
                                        "doi": doi,
                                        "title": title or _extract_title_from_line(raw),
                                        "first_author": _cell(cells, author_i),
                                        "year": _cell(cells, year_i),
                                        "journal": _cell(cells, journal_i),
                                        "tags": [
                                            tag.strip()
                                            for tag in re.split(r"[,，;；、]", _cell(cells, tags_i) or "")
                                            if tag.strip()
                                        ],
                                        "status": _cell(cells, status_i),
                                        "notes": _cell(cells, notes_i),
                                        "raw_citation": raw,
                                        "order_index": order_index,
                                    }
                                )
                        index += 1
                    continue
        doi_match = DOI_RE.search(line)
        if doi_match:
            order_index += 1
            items.append(
                {
                    "doi": doi_match.group(0),
                    "title": _extract_title_from_line(line),
                    "raw_citation": line.strip(),
                    "order_index": order_index,
                }
            )
        index += 1
    return items


def _upsert_collection(
    conn: sqlite3.Connection,
    *,
    name: str,
    collection_type: str,
    source_path: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> sqlite3.Row:
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO collections (name, type, source_path, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            type = excluded.type,
            source_path = excluded.source_path,
            metadata_json = excluded.metadata_json,
            updated_at = excluded.updated_at
        """,
        (
            name,
            collection_type,
            _normalize_path(source_path),
            json.dumps(metadata or {}, ensure_ascii=False),
            now,
            now,
        ),
    )
    row = conn.execute("SELECT * FROM collections WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to persist collection {name}")
    return row


def import_collection_markdown(
    markdown_path: str,
    *,
    name: str | None = None,
    collection_type: str = "review_library",
) -> dict[str, Any]:
    path = Path(markdown_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Markdown collection not found: {path}")

    collection_name = name or _collection_name_from_md(path)
    parsed_items = _parse_markdown_papers(path)
    imported = 0
    with _connect() as conn:
        collection = _upsert_collection(
            conn,
            name=collection_name,
            collection_type=collection_type,
            source_path=str(path),
            metadata={"import_format": "markdown"},
        )
        for item in parsed_items:
            paper = _upsert_entry(
                conn,
                {
                    "doi": item.get("doi"),
                    "title": item.get("title"),
                    "year": item.get("year"),
                    "first_author": item.get("first_author"),
                    "journal": item.get("journal"),
                    "keywords": item.get("tags", []),
                    "status": "referenced",
                    "source_method": "collection_import_md",
                },
            )
            now = _now_iso()
            conn.execute(
                """
                INSERT INTO collection_items (
                    collection_id, paper_key, role, status, notes, order_index,
                    raw_citation, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(collection_id, paper_key) DO UPDATE SET
                    role = excluded.role,
                    status = excluded.status,
                    notes = excluded.notes,
                    order_index = excluded.order_index,
                    raw_citation = excluded.raw_citation,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    collection["id"],
                    paper["key"],
                    item.get("role"),
                    item.get("status") or "referenced",
                    item.get("notes"),
                    item.get("order_index") or imported + 1,
                    item.get("raw_citation"),
                    json.dumps({"tags": item.get("tags", [])}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            imported += 1
        conn.commit()
        _export_registry(conn)
    return {"collection": collection_name, "source_path": str(path), "imported": imported}


def list_collections() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.name, c.type, c.source_path, c.created_at, c.updated_at,
                   COUNT(ci.paper_key) AS item_count
            FROM collections c
            LEFT JOIN collection_items ci ON ci.collection_id = c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_collection(name: str) -> dict[str, Any]:
    with _connect() as conn:
        collection = conn.execute("SELECT * FROM collections WHERE name = ?", (name,)).fetchone()
        if collection is None:
            raise KeyError(f"Collection not found: {name}")
        rows = conn.execute(
            """
            SELECT ci.role, ci.status AS collection_status, ci.notes, ci.order_index,
                   ci.raw_citation, ci.metadata_json,
                   p.key, p.doi, p.title, p.year, p.first_author, p.journal,
                   p.keywords_json, p.final_path, p.raw_path, p.status AS paper_status
            FROM collection_items ci
            JOIN papers p ON p.key = ci.paper_key
            WHERE ci.collection_id = ?
            ORDER BY ci.order_index ASC, p.year DESC, p.title ASC
            """,
            (collection["id"],),
        ).fetchall()

    items = []
    for row in rows:
        item = dict(row)
        item["keywords"] = json.loads(item.pop("keywords_json") or "[]")
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        items.append(item)
    result = dict(collection)
    result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
    result["items"] = items
    return result


def export_collection_json(name: str, output_path: str | None = None) -> dict[str, Any]:
    collection = get_collection(name)
    if output_path:
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")
    return collection


def render_collection_markdown(collection: dict[str, Any]) -> str:
    lines = [
        f"# {collection['name']}",
        "",
        f"> Type: {collection['type']}",
        f"> Items: {len(collection['items'])}",
        "",
        "| # | Year | First Author | Title | DOI | Journal | Status | Path | Notes |",
        "|---|------|--------------|-------|-----|---------|--------|------|-------|",
    ]
    for index, item in enumerate(collection["items"], 1):
        path = item.get("final_path") or item.get("raw_path") or ""
        cells = [
            str(index),
            item.get("year") or "",
            item.get("first_author") or "",
            (item.get("title") or "").replace("|", "\\|"),
            item.get("doi") or "",
            (item.get("journal") or "").replace("|", "\\|"),
            item.get("collection_status") or item.get("paper_status") or "",
            path,
            (item.get("notes") or "").replace("|", "\\|"),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def export_collection_markdown(name: str, output_path: str | None = None) -> str:
    collection = get_collection(name)
    rendered = render_collection_markdown(collection)
    if output_path:
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    return rendered


def registry_stats() -> dict[str, Any]:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        by_status = {
            row["status"] or "unknown": row["count"]
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM papers GROUP BY status ORDER BY count DESC"
            ).fetchall()
        }
        by_method = {
            row["source_method"] or "unknown": row["count"]
            for row in conn.execute(
                "SELECT source_method, COUNT(*) AS count FROM papers GROUP BY source_method ORDER BY count DESC"
            ).fetchall()
        }
    return {"total": total, "by_status": by_status, "by_method": by_method, "database": str(REGISTRY_DB)}


def render_registry_markdown(registry: dict[str, Any]) -> str:
    rows = []
    papers = sorted(
        registry.get("papers", []),
        key=lambda item: item.get("updated_at", ""),
        reverse=True,
    )
    for paper in papers:
        rows.append(
            "| {status} | {category} | {doi} | {author} | {year} | {title} | {method} | {final_path} |".format(
                status=paper.get("status", "-"),
                category=paper.get("category_slug", "-"),
                doi=paper.get("doi", "-"),
                author=paper.get("first_author", "-"),
                year=paper.get("year", "-"),
                title=(paper.get("title") or "-").replace("\n", " ")[:100],
                method=paper.get("source_method", "-"),
                final_path=paper.get("final_path", paper.get("raw_path", "-")),
            )
        )
    header = [
        "# Paper Registry",
        "",
        "> Persistent registry of papers downloaded, archived, scanned, or queued for manual completion.",
        "",
        f"- Storage: `{REGISTRY_DB}`",
        f"- Total records: {len(papers)}",
        "",
        "| Status | Category | DOI | Author | Year | Title | Method | Path |",
        "|--------|----------|-----|--------|------|-------|--------|------|",
    ]
    return "\n".join(header + rows + [""])


def _status_from_session_cell(value: str) -> str:
    if "FAILED" in value:
        return "failed"
    if "MANUAL" in value:
        return "manual_download"
    return "downloaded"


def backfill_from_session_logs() -> dict[str, int]:
    session_files = sorted(LOGS_DIR.glob("session_*.md"))
    imported = 0
    skipped = 0
    with _connect() as conn:
        for session_file in session_files:
            lines = session_file.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if not line.startswith("| ") or line.startswith("| DOI ") or line.startswith("|-----"):
                    continue
                parts = [part.strip() for part in line.strip().strip("|").split("|")]
                if len(parts) != 5:
                    continue
                doi, title, method, status_cell, notes = parts
                if not doi or doi == "-":
                    skipped += 1
                    continue
                payload = {
                    "doi": doi,
                    "title": None if title == "-" else title,
                    "source_method": None if method == "-" else method,
                    "status": _status_from_session_cell(status_cell),
                    "error": None if notes.endswith("KB") else (None if notes == "-" else notes),
                    "last_attempt_at": datetime.fromtimestamp(session_file.stat().st_mtime).isoformat(timespec="seconds"),
                }
                existing = conn.execute("SELECT key FROM papers WHERE key = ?", (f"doi:{doi.lower()}",)).fetchone()
                _upsert_entry(conn, payload)
                if existing is None:
                    imported += 1
        conn.commit()
        _export_registry(conn)
    return {"sessions": len(session_files), "imported": imported, "skipped": skipped}
