#!/usr/bin/env python3
"""Convert a paper PDF to Markdown through file-format-converter/MinerU only.

This wrapper exists so paper-downloader can enforce the shared File Converter
rules: PDF -> Markdown must use MinerU/magic-pdf, and the final paper folder
must contain only the canonical Markdown plus an images/ folder.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from update_paper_properties import update_markdown_properties
except ImportError:  # pragma: no cover
    from .update_paper_properties import update_markdown_properties  # type: ignore


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
INTERMEDIATE_DIR_NAMES = {"mineru_output", "magicpdf_output", "mineru_local_ocr", "auto", "ocr"}
INTERMEDIATE_SUFFIXES = (".json", ".jsonl", ".log", "_layout.pdf", "_origin.pdf", "_span.pdf", "_spans.pdf")


class MinerUConversionError(RuntimeError):
    pass


def intermediate_stem(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return safe or "book"


def trash_path_for(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path.home() / ".Trash" / "paper-downloader-mineru-cleanup" / stamp
    target = root / path.name
    if not target.exists():
        return target
    for index in range(2, 1000):
        candidate = root / f"{path.stem}_{index}{path.suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not resolve Trash conflict for {path}")


def move_to_trash(path: Path, dry_run: bool) -> str:
    target = trash_path_for(path)
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
    return str(target)


def copy_backup_to_trash(path: Path) -> str:
    target = trash_path_for(path.with_name(f"{path.stem}.conversion-backup-{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}"))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return str(target)


def conversion_env(model_source: str) -> dict[str, str]:
    env = os.environ.copy()
    env["MINERU_DEVICE_MODE"] = "mps"
    env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    env["MINERU_MODEL_SOURCE"] = model_source
    return env


def run_mineru(pdf_path: Path, output_dir: Path, *, method: str, model_source: str, timeout: int) -> dict[str, Any]:
    magic_pdf = shutil.which("magic-pdf")
    if not magic_pdf:
        raise MinerUConversionError("MinerU legacy CLI magic-pdf not found; PDF -> Markdown conversion is blocked.")
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [magic_pdf, "-p", str(pdf_path), "-o", str(output_dir), "-m", method]
    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=conversion_env(model_source),
        timeout=timeout,
    )
    return {"cmd": cmd, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def find_mineru_markdown(output_dir: Path, pdf_stem: str) -> Path:
    candidates = list(output_dir.rglob(f"{pdf_stem}.md")) + list(output_dir.rglob("*.md"))
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        raise MinerUConversionError(f"MinerU did not produce Markdown under {output_dir}")
    return sorted(set(candidates), key=lambda p: (0 if p.name == f"{pdf_stem}.md" else 1, len(str(p)), str(p)))[0]


def copy_images_and_rewrite_links(markdown_path: Path, target_images: Path, *, dry_run: bool) -> tuple[str, int]:
    text = markdown_path.read_text(encoding="utf-8", errors="replace")
    image_count = 0
    image_sources: list[Path] = []
    sibling_images = markdown_path.parent / "images"
    if sibling_images.is_dir():
        image_sources.append(sibling_images)
    loose_images = [p for p in markdown_path.parent.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    if loose_images:
        image_sources.append(markdown_path.parent)

    if not dry_run:
        for source in dict.fromkeys(image_sources):
            target_images.mkdir(parents=True, exist_ok=True)
            if source.is_dir() and source.name == "images":
                for item in source.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(source)
                        dest = target_images / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, dest)
                        image_count += 1
            else:
                for item in loose_images:
                    shutil.copy2(item, target_images / item.name)
                    image_count += 1
    else:
        for source in dict.fromkeys(image_sources):
            image_count += sum(1 for p in source.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)

    # Paper library final view always uses images/, not <stem>_images/.
    text = text.replace("./images/", "images/")
    text = text.replace("images/", "images/")
    text = re.sub(r"(?<![\w./-])[^\s)]+_images/", "images/", text)
    return text, image_count


def cleanup_final_folder(folder: Path, output_dir: Path, pdf_stem: str, dry_run: bool) -> list[str]:
    removed: list[str] = []
    for child in sorted(folder.iterdir(), key=lambda p: p.name):
        if child == output_dir or child.name in INTERMEDIATE_DIR_NAMES:
            removed.append(move_to_trash(child, dry_run))
            continue
        if child.is_file():
            lower = child.name.lower()
            if any(lower.endswith(suffix) for suffix in INTERMEDIATE_SUFFIXES):
                removed.append(move_to_trash(child, dry_run))
    # Remove old noncanonical image folders after final images/ exists.
    for child in sorted(folder.iterdir(), key=lambda p: p.name):
        if child.is_dir() and child.name.endswith("_images") and child.name != "images":
            removed.append(move_to_trash(child, dry_run))
    return removed


def convert_pdf_to_markdown(
    pdf_path: Path,
    md_path: Path,
    *,
    method: str = "auto",
    model_source: str = "local",
    timeout: int = 2400,
    dry_run: bool = False,
    keep_output: bool = False,
    import_source: str | None = None,
) -> dict[str, Any]:
    pdf_path = pdf_path.expanduser().resolve()
    md_path = md_path.expanduser().resolve()
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        raise MinerUConversionError(f"PDF not found: {pdf_path}")
    if md_path.suffix.lower() != ".md":
        raise MinerUConversionError(f"Target is not Markdown: {md_path}")

    output_dir = md_path.parent / "mineru_output" / intermediate_stem(pdf_path.stem)
    result = {"pdf_path": str(pdf_path), "md_path": str(md_path), "output_dir": str(output_dir), "dry_run": dry_run}
    if dry_run:
        result["status"] = "dry_run"
        result["command"] = ["magic-pdf", "-p", str(pdf_path), "-o", str(output_dir), "-m", method]
        return result

    run = run_mineru(pdf_path, output_dir, method=method, model_source=model_source, timeout=timeout)
    result["mineru"] = {k: v for k, v in run.items() if k != "stdout" and k != "stderr"}
    log_path = md_path.parent / f"{pdf_path.stem}.mineru_conversion.log"
    log_path.write_text(
        json.dumps({**run, "cmd": run["cmd"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if run["returncode"] != 0:
        result["status"] = "failed"
        result["log_path"] = str(log_path)
        raise MinerUConversionError(f"MinerU failed with exit {run['returncode']}; see {log_path}")

    mineru_md = find_mineru_markdown(output_dir, pdf_path.stem)
    backup_path = ""
    if md_path.exists():
        backup_path = copy_backup_to_trash(md_path)

    text, image_count = copy_images_and_rewrite_links(mineru_md, md_path.parent / "images", dry_run=False)
    md_path.write_text(text, encoding="utf-8")
    update_markdown_properties(md_path, import_source=import_source)

    removed = []
    if not keep_output:
        removed.extend(cleanup_final_folder(md_path.parent, output_dir, pdf_path.stem, dry_run=False))
    result.update(
        {
            "status": "converted",
            "source_markdown": str(mineru_md),
            "backup": backup_path,
            "image_count": image_count,
            "log_path": str(log_path),
            "removed_to_trash": removed,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert a paper PDF to canonical Markdown using MinerU only")
    parser.add_argument("pdf_path")
    parser.add_argument("md_path")
    parser.add_argument("--method", choices=["auto", "txt", "ocr"], default="auto")
    parser.add_argument("--model-source", choices=["local", "modelscope"], default="local")
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-output", action="store_true")
    parser.add_argument("--import-source", choices=["local", "paper-downloader"], default="local")
    args = parser.parse_args()
    try:
        result = convert_pdf_to_markdown(
            Path(args.pdf_path),
            Path(args.md_path),
            method=args.method,
            model_source=args.model_source,
            timeout=args.timeout,
            dry_run=args.dry_run,
            keep_output=args.keep_output,
            import_source=args.import_source,
        )
    except MinerUConversionError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
