#!/usr/bin/env python3
"""Apply a reviewed semantic rename TSV to the flat paper library.

TSV columns: old_name, new_stem, confidence, evidence.
Rows with new_stem=SKIP or confidence below threshold are staged for review, not deleted.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_LIBRARY_DIR = (Path.home() / "Documents" / "PAPER" / "Papers")
DOI_STYLE_RE = re.compile(r"^10[._]\d", re.I)


def clean_stem(value: str) -> str:
    stem = value.strip()
    stem = re.sub(r"[<>:\"/\\|?*]", "", stem)
    stem = re.sub(r"[^0-9A-Za-z_-]+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("._-")
    return (stem[:180] or "paper")


def resolve_conflict(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.name}_{index}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not resolve conflict for {path}")


def read_tsv(paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_old: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                if not row.get("old_name"):
                    continue
                old = row["old_name"].strip()
                new = (row.get("new_stem") or "").strip()
                try:
                    confidence = float((row.get("confidence") or "0").strip())
                except ValueError:
                    confidence = 0.0
                item = {
                    "old_name": old,
                    "new_stem": new,
                    "confidence": confidence,
                    "evidence": (row.get("evidence") or "").strip(),
                    "source": str(path),
                }
                previous = by_old.get(old)
                if previous:
                    duplicates.append({"old_name": old, "kept": previous, "dropped": item})
                    if confidence > previous["confidence"]:
                        by_old[old] = item
                else:
                    by_old[old] = item
    rows = [by_old[key] for key in sorted(by_old)]
    return rows, duplicates


def write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["old_name", "new_stem", "confidence", "evidence"], delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in ["old_name", "new_stem", "confidence", "evidence"]})


def rename_inside_folder(folder: Path, old_name: str, new_name: str, dry_run: bool) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for ext in (".pdf", ".md"):
        exact = folder / f"{old_name}{ext}"
        target = folder / f"{new_name}{ext}"
        if exact.exists() and exact != target:
            if target.exists():
                target = folder / f"{new_name}_main{ext}"
            actions.append({"action": f"rename_main{ext}", "from": str(exact), "to": str(target)})
            if not dry_run:
                shutil.move(str(exact), str(target))

    # If the canonical file was not named after the old folder, normalize the largest/primary file.
    for ext in (".pdf", ".md"):
        target = folder / f"{new_name}{ext}"
        if target.exists():
            continue
        candidates = sorted(folder.glob(f"*{ext}"), key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
        candidates = [p for p in candidates if not re.search(r"backup|layout|origin|span|supplement|attachment", p.stem, re.I)] or candidates
        if candidates:
            source = candidates[0]
            if source != target:
                if target.exists():
                    target = folder / f"{new_name}_main{ext}"
                actions.append({"action": f"rename_primary{ext}", "from": str(source), "to": str(target)})
                if not dry_run:
                    shutil.move(str(source), str(target))

    if not dry_run:
        for md_path in folder.glob("*.md"):
            text = md_path.read_text(encoding="utf-8", errors="ignore")
            updated = text.replace(f"{old_name}.pdf", f"{new_name}.pdf").replace(f"{old_name}.md", f"{new_name}.md")
            if updated != text:
                md_path.write_text(updated, encoding="utf-8")
    return actions


def apply_row(library_dir: Path, review_dir: Path, row: dict[str, Any], threshold: float, dry_run: bool) -> dict[str, Any]:
    old_name = row["old_name"]
    old_dir = library_dir / old_name
    new_raw = row["new_stem"]
    confidence = row["confidence"]
    result: dict[str, Any] = {"old_name": old_name, "new_stem": new_raw, "confidence": confidence, "actions": []}
    if not old_dir.is_dir():
        result["status"] = "missing_old"
        return result

    if new_raw.upper() == "SKIP" or confidence < threshold:
        target = resolve_conflict(review_dir / old_name)
        result["status"] = "staged_for_review"
        result["actions"].append({"action": "move_to_review", "from": str(old_dir), "to": str(target)})
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_dir), str(target))
        return result

    new_name = clean_stem(new_raw)
    target_dir = library_dir / new_name
    if old_dir != target_dir and target_dir.exists():
        result["status"] = "target_exists_requires_dedupe"
        result["actions"].append({
            "action": "blocked_duplicate_suffix_creation",
            "from": str(old_dir),
            "to": str(target_dir),
        })
        return result
    final_name = target_dir.name
    if old_dir != target_dir:
        result["actions"].append({"action": "rename_folder", "from": str(old_dir), "to": str(target_dir)})
        if not dry_run:
            shutil.move(str(old_dir), str(target_dir))
    work_dir = old_dir if dry_run else target_dir
    result["actions"].extend(rename_inside_folder(work_dir, old_name, final_name, dry_run))
    result["status"] = "renamed"
    result["final_name"] = final_name
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply semantic paper folder/file rename map")
    parser.add_argument("tsv", nargs="+", help="One or more TSV mapping files")
    parser.add_argument("--library-dir", default=str(DEFAULT_LIBRARY_DIR))
    parser.add_argument("--threshold", type=float, default=0.70)
    parser.add_argument("--apply", action="store_true", help="Actually modify files; default is dry-run")
    parser.add_argument("--combined-tsv", help="Optional path to write de-duplicated combined TSV")
    parser.add_argument("--skip-tsv", help="Optional path to write rows staged for review")
    parser.add_argument("--report", help="Optional JSON report path")
    args = parser.parse_args()

    library_dir = Path(args.library_dir).expanduser().resolve()
    inbox_dir = library_dir / "inbox"
    review_dir = inbox_dir / "06_problematic_papers_review"
    rows, duplicates = read_tsv([Path(p).expanduser().resolve() for p in args.tsv])
    active_rows = [r for r in rows if r["new_stem"].upper() != "SKIP" and r["confidence"] >= args.threshold]
    skip_rows = [r for r in rows if r["new_stem"].upper() == "SKIP" or r["confidence"] < args.threshold]

    if args.combined_tsv:
        write_tsv(Path(args.combined_tsv).expanduser(), rows)
    if args.skip_tsv:
        write_tsv(Path(args.skip_tsv).expanduser(), skip_rows)

    dry_run = not args.apply
    results = [apply_row(library_dir, review_dir, row, args.threshold, dry_run) for row in rows]
    report = {
        "dry_run": dry_run,
        "library_dir": str(library_dir),
        "threshold": args.threshold,
        "input_rows": sum(1 for _ in rows),
        "active_rows": len(active_rows),
        "review_rows": len(skip_rows),
        "duplicate_old_names": duplicates,
        "results": results,
    }
    default_report = inbox_dir / f"semantic_rename_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path = Path(args.report).expanduser() if args.report else default_report
    if not dry_run:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "dry_run": dry_run,
        "input_rows": report["input_rows"],
        "active_rows": len(active_rows),
        "review_rows": len(skip_rows),
        "duplicates": len(duplicates),
        "renamed_or_reviewed": sum(1 for item in results if item.get("status") in {"renamed", "staged_for_review"}),
        "blocked_existing_targets": sum(1 for item in results if item.get("status") == "target_exists_requires_dedupe"),
        "missing_old": sum(1 for item in results if item.get("status") == "missing_old"),
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
