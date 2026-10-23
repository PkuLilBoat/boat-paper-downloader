# Library Organization Workflow

Policy authority lives in `~/Documents/PAPER/Rules/INDEX rules.md` and the routed PAPER Rules. This document only describes helper-script mechanics and must not override those rules.

Use this document after PDFs are downloaded, renamed, or converted.

## Hard Rules

- Canonical library root is `~/Documents/PAPER/Papers`.
- Root-level paper entries should be **one folder per paper**; do not use category folders.
- Topic/category belongs in Markdown properties, not in the folder path.
- `inbox/` is the only non-paper root folder. Use it for intermediate outputs, download artifacts, source collections that still need splitting, and reference fragments that need user review.
- Each paper folder should contain at least one PDF and one Markdown file.
- Canonical paper folders may contain only:
  - `<paper_stem>.pdf`
  - `<paper_stem>.md`
  - `images/` when converted
  - optional `metadata.json`
  - optional `attachments/` for real supplementary files
- Do not leave loose PDFs, Markdown files, CSV attachments, Cloudflare HTML blocks, MinerU outputs, or backup extracts in the library root.

## Target Layout

```text
~/Documents/PAPER/Papers/
├── 2025_Wulff_semantic_embeddings_psychological_measurement_NatHumBehav/
│   ├── 2025_Wulff_semantic_embeddings_psychological_measurement_NatHumBehav.pdf
│   ├── 2025_Wulff_semantic_embeddings_psychological_measurement_NatHumBehav.md
│   └── images/
├── 2017_Davila_romantic_competence_Personal_Relationships/
│   ├── 2017_Davila_romantic_competence_Personal_Relationships.pdf
│   ├── 2017_Davila_romantic_competence_Personal_Relationships.md
│   └── images/
└── inbox/
    ├── 00_intermediate_outputs/
    ├── 01_download_artifacts/
    ├── 02_reference_fragments_review/
    ├── 03_collections_to_split/
    ├── 04_empty_legacy_dirs/
    ├── 05_loose_attachments_review/
    └── 06_problematic_papers_review/
```


## Canonical Naming Rules

The paper folder name and the main file names must match exactly. For a folder named `<paper_stem>`:

```text
~/Documents/PAPER/Papers/<paper_stem>/
├── <paper_stem>.pdf
├── <paper_stem>.md
└── images/
```

Rules:

- Rename `content.md`, `file.md`, or other generic Markdown names to `<paper_stem>.md`.
- Rename generic PDFs such as `content.pdf`, `paper.pdf`, or `download.pdf` to `<paper_stem>.pdf`.
- Do not keep DOI-only, numbered, Chinese, or punctuation-heavy stems as canonical paper names. Semantic stems must use `{year}_{FirstAuthor}_{keywords}_{JournalOrVenue}` with ASCII words and underscores/hyphens.
- Normalize image folders to `images/`.
- Rewrite Markdown image links after image folder normalization.
- Keep alternate Markdown files only when they are deliberate notes/translations/reports; the canonical paper Markdown still must be `<paper_stem>.md`.

Check or repair internal folder/file consistency with:

```bash
python scripts/normalize_paper_names.py --library-dir ~/Documents/PAPER/Papers --dry-run
python scripts/normalize_paper_names.py --library-dir ~/Documents/PAPER/Papers
```

For semantic renaming, first create a TSV from the paper PDF/Markdown content, then apply it safely:

```bash
python scripts/apply_semantic_rename_map.py rename_map.tsv --library-dir ~/Documents/PAPER/Papers
python scripts/apply_semantic_rename_map.py rename_map.tsv --library-dir ~/Documents/PAPER/Papers --apply
```

The TSV schema is `old_name<TAB>new_stem<TAB>confidence<TAB>evidence`. `SKIP` and low-confidence rows are staged into `inbox/06_problematic_papers_review/`.

## Markdown Properties

Use `config/paper_categories.json` as the controlled vocabulary for topic-like properties only. These values must not become directories.

Suggested frontmatter:

```yaml
---
title: "Paper title"
year:
authors: []
doi:
source_pdf: "paper.pdf"
category: "01_mbti_psychological_type" # controlled property value, not a folder
status: inbox
tags: []
---
```

Current allowed `category` values:

- `01_mbti_psychological_type`
- `02_trauma`
- `03_romantic_competence`
- `04_psychometrics_methodology`
- `00_uncategorized` only when the paper does not fit or needs review

## Cleanup Rules

- PDF conversion should leave only Markdown and `images/` next to the canonical PDF.
- Move intermediate artifacts into `~/Documents/PAPER/Papers/inbox/00_intermediate_outputs/` rather than deleting them.
- Move failed download pages, progress logs, raw downloader folders, and Cloudflare block pages into `~/Documents/PAPER/Papers/inbox/01_download_artifacts/`.
- Move multi-paper source folders or thematic bundles into `~/Documents/PAPER/Papers/inbox/03_collections_to_split/` until each paper is split into its own root-level folder.
- Move summary/reference fragments into `~/Documents/PAPER/Papers/inbox/02_reference_fragments_review/` and report them to the user before deciding whether to create a separate reference folder.
- Clean these artifacts from the canonical paper view:
  - `*_layout.pdf`, `*_origin.pdf`, `*_span.pdf`, `*_spans.pdf`
  - MinerU raw output dirs: `mineru_output/`, `magicpdf_output/`, `mineru_local_ocr/`, `auto/`, `ocr/`
  - JSON/JSONL/log/text sidecars unless deliberately saved as `metadata.json`
  - loose extracted images once they are under `images/`

## Normalize Existing Library

Use the flat-layout normalizer when the root contains loose files, category folders, or source bundles:

```bash
python scripts/normalize_flat_library.py
```

The script stages non-paper material into `inbox/`, creates Markdown stubs for PDF-only paper folders, and writes a normalization report under `inbox/`.

## Index Structure

SQLite is the source of truth: `logs/paper_registry.sqlite`.

Derived snapshots are exported for quick reading:

- `logs/paper_registry.json`
- `logs/paper_registry.md`

For Obsidian indexing, prefer Markdown properties in each paper folder. SQLite remains useful for download history, deduplication, and batch queries; Markdown properties are useful for interactive vault browsing.

## Promote Paper-Like Folders From Inbox

If `inbox/` still contains folders that are already single papers, including folders where the Markdown is named `content.md`, promote them back to root-level paper folders:

```bash
python scripts/promote_inbox_papers.py
```

The promoter handles:

- folders with one PDF and one or more Markdown files
- Markdown-only converted papers with `content.md` and extracted images
- PDF-only paper folders by creating a minimal Markdown stub
- image normalization into `images/`

After promotion, `inbox/03_collections_to_split/` should contain only non-paper project/source fragments that need later review.
