---
name: boat-paper-downloader
description: Download paper PDFs from one or more DOIs or browser-only sources, convert successful PDFs to local Markdown by default with MinerU, and report unresolved items. Prefer direct open-access and publisher routes before configured proxy or browser fallback.
---

# BOAT Paper Downloader

Acquire academic PDFs and perform default PDF-to-Markdown technical conversion. The Skill does not own semantic classification: the user's literature library owns naming, category and Property policy.

Resolve all `scripts/`, `config/` and `docs/` paths relative to this Skill directory. Execute commands with that directory as the working directory.

## Core workflow

1. Normalize and deduplicate the requested DOI list; determine an explicit output directory.
2. Run the coordinator with bounded concurrency. Open-access and publisher HTTP attempts race using isolated candidate files.
3. If HTTP fails, retry only through already configured usable proxies, then use bounded browser fallback. The source retains a final Sci-Hub fallback.
4. Treat PDF bytes as a candidate until identity and completeness are reviewed. Report unresolved or human-required results without inventing success.
5. Convert successful PDFs to same-stem Markdown using File Converter/MinerU by default. Use `--no-convert` only when the user wants PDF-only output.
6. Return actual paths and separate PDF acquisition, conversion and library-intake results.

Do not launch a VPN, change system proxy settings, open persistent failed tabs, or start an interactive login solely because a download failed. Authentication, OTP and CAPTCHA require the user's own authorized interaction.

## Evidence-driven recovery within a run

For each unresolved item, inspect the manifest and the actual response or landing page before choosing a different supported route. Distinguish missing full text, access denial, a challenge page, a viewer wrapper, and a conversion failure. Do not retry the same failed request unchanged or substitute a different paper just to obtain a PDF.

Use the bounded coordinator first. If an authorized browser session is needed, inspect only the current article's PDF controls, embedded PDF targets or download event; keep the attempt scoped to that article and report the concrete blocker when no supported route remains. Do not bypass access controls.

This distribution keeps built-in HTTP/browser handlers and a static execution mapping. It excludes the local edition's cross-run site memory, learned route ranking, host manuals and historical validation ledger. `strategy_attempts` in a result is a trace of this run, not a reusable success recipe. Do not build a persistent strategy store or rewrite handler priority from download logs.

## Download commands

```bash
python scripts/scihub_downloader.py ./papers <doi1> [doi2] \
  --concurrency 2 --result-json ./download-result.json

python scripts/scihub_downloader.py ./papers <doi> --no-convert
```

Default output:

```text
<output>/<doi-safe-stem>/<doi-safe-stem>.pdf
<output>/<doi-safe-stem>/<doi-safe-stem>.md
<output>/<doi-safe-stem>/images/
```

The conversion wrapper requires a locally available `magic-pdf` command and models. Its current environment is configured for MPS on Apple Silicon. Missing conversion dependencies must be reported explicitly.

## Library intake and Property boundary

Keep successful files in the requested output directory unless the user explicitly requests formal library intake. `--organize-after-download` enables that separate action.

Read the target library's own instructions before moving, renaming, deduplicating or writing properties. For an Obsidian PAPER library with a rule index, read `<PAPER-root>/Rules/INDEX rules.md` and its matching `论文进入 Rule` and `论文 Property Rule`; these are local user policies and are not bundled here.

- Preserve one canonical folder per paper, with matching PDF and Markdown stems and an `images/` directory.
- Intake writes deterministic key/path fields only: `paper_key`, `p0_import_source`, `p0_local_pdf`, `p0_local_md` and, when applicable, `p0_local_assets_dir`.
- Keep `p0_import_source: paper-downloader` for compatibility with existing data.
- Never automatically create `fileClass` or semantic categories. Apply semantic Property classification only when explicitly requested.
- Category values are properties, not directory names.
- Use dry-run support before library reorganization or deduplication; preserve originals and review uncertain matches.

The public copy defaults to `~/Documents/PAPER/Papers`; inspect each command's `--help` for an explicit target override.

## Browser-assisted sources

For CNKI or other sources requiring existing access, configure a local browser session and use the site's own download flow. Prefer PDF; a CAJ-only source is a manual side path. See [CNKI integration](docs/cnki-download-integration.md), [browser configuration](docs/institutional-browser.md) and [institutional login](docs/institutional-login-automation.md).

Use `local/institution_login.example.json` as a template only. Never include real credentials, cookies, browser profiles or logs in a shared result.

## Batch coordination

Use one coordinator as owner of shared run logs, library intake and conversion. For large batches with explicitly available workers, assign disjoint DOI shards; never race separate processes on the same paper.

```bash
python scripts/scihub_downloader.py ./papers <shard-dois> \
  --worker-mode --concurrency 2 --result-json ./runs/worker-1.json

python scripts/scihub_downloader.py ./papers <all-dois> \
  --fallback-only --browser-concurrency 1 --result-json ./runs/coordinator.json
```

Read atomic worker manifests. `needs_coordinator_fallback` is a handoff state, not completion. Keep the total DOI worker budget bounded; conversion runs only in the coordinator.

## Optional tools

- `scripts/paper_index.py`: SQLite index and literature collections. Generated data lives in ignored `logs/`.
- `scripts/rename_papers.py`, `scripts/normalize_paper_names.py`: canonical naming and organization.
- `scripts/dedupe_paper_library.py`: conservative duplicate review and merging.
- `scripts/pdf_to_markdown.py`: local MinerU conversion and output normalization.
- `scripts/translate_markdown.py`: chunked translation through an installed, authenticated Codex CLI; default sidecar output preserves source Markdown. See [translation guide](docs/markdown-translation.md).

Use each script's `--help`; run mutation tools only for the user's requested target.

## Validation and reporting

Verify the actual PDF, Markdown and image paths. Read result manifests rather than inferring success from progress messages. Separate downloaded, converted, archived and still-unresolved items. Do not claim real-site or model validation based only on unit tests.

- The downloader's signature/size check and `status: downloaded` establish acquisition only. Open the PDF with an available reader, check that pages are readable, and compare its title, authors and DOI where present against the requested work. A DOI mentioned only in references does not prove identity.
- Distinguish the main article from a supplement, preview, correction or different version. Check that the body and ending are present; page count alone cannot establish completeness. Retain the version label for a preprint or accepted manuscript.
- If identity or completeness remains uncertain, report it as pending review and keep it out of formal library intake. Preserve existing files when replacing a rejected candidate.
- Check the converted Markdown against the PDF for missing sections, tables or images. Report missing dependencies and conversion errors separately; do not count an empty or partial Markdown file as a completed reading copy.
- Return one receipt per DOI: acquisition result, actual source/method, file paths, identity/completeness review, conversion result and any required next step. Never include credentials, cookies or authenticated URL secrets in a shared receipt.
