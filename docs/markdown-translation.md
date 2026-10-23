# Markdown Translation

Before writing into the PAPER library, read `~/Documents/PAPER/Rules/INDEX rules.md`; PAPER Rules own paths, naming, conversion, and Property boundaries.

This workflow adds literal Chinese translations to existing paper Markdown files while preserving the English source.

## Command

Dry-run first:

~~~bash
python scripts/translate_markdown.py ~/Documents/PAPER/Papers/<paper_stem>/<paper_stem>.md \
  --model gpt-5.4-mini \
  --concurrency 5 \
  --mode interleaved \
  --dry-run
~~~

Apply or resume:

~~~bash
python scripts/translate_markdown.py ~/Documents/PAPER/Papers/<paper_stem>/<paper_stem>.md \
  --model gpt-5.4-mini \
  --concurrency 8 \
  --chunk-chars 10000 \
  --mode interleaved \
  --output-mode sidecar \
  --glossary /path/to/glossary.tsv \
  --qa \
  --apply \
  --resume
~~~

By default, apply writes a sidecar file named `<paper_stem>.translation.zh-cn.md`
beside the source Markdown. Use `--output-mode in-place` only when the original
paper Markdown should be replaced.

After a sidecar translation succeeds, the script also updates Obsidian-friendly
properties in both files. The source paper gets fixed links to the PDF and the
Chinese translation, and the translation sidecar gets fixed links back to the
English source and the PDF:

~~~yaml
paper_pdf: "[[2019_Faber_IRRC_Interpersona.pdf|PDF]]"
translation_zh_cn: "[[2019_Faber_IRRC_Interpersona.translation.zh-cn|中文译文]]"
translation_status: "translated"
source_paper: "[[2019_Faber_IRRC_Interpersona|英文原文]]"
~~~

Use the property updater directly for already-translated papers:

~~~bash
python scripts/update_paper_properties.py ~/Documents/PAPER/Papers/<paper_stem>/<paper_stem>.md
python scripts/update_paper_properties.py ~/Documents/PAPER/Papers/<paper_stem>/<paper_stem>.translation.zh-cn.md --source-path ~/Documents/PAPER/Papers/<paper_stem>/<paper_stem>.md
~~~

## PDF to Markdown Source

Paper Markdown conversion must use the shared File Converter/MinerU path. In paper-downloader, run:

~~~bash
python scripts/pdf_to_markdown.py ~/Documents/PAPER/Papers/<paper_stem>/<paper_stem>.pdf \
  ~/Documents/PAPER/Papers/<paper_stem>/<paper_stem>.md \
  --method txt \
  --model-source local
~~~

This wrapper calls local `magic-pdf` with MPS environment variables and refuses non-MinerU fallbacks.
After conversion, the final paper folder is compacted to one canonical Markdown file plus `images/`.
Intermediate MinerU outputs, JSON/log sidecars, and layout/origin/span PDFs are moved out of the
final view.

## Glossary

Use a glossary when terminology must stay stable across chunks. TSV is the
simplest format:

~~~text
romantic competence	浪漫能力	fixed construct translation
adolescent courtship	青少年求爱/恋爱追求	context-specific phrase
AICQc	AICQc	acronym, keep unchanged
~~~

JSON is also supported:

~~~json
{
  "terms": [
    {"source": "romantic competence", "target": "浪漫能力", "note": "fixed construct translation"}
  ]
}
~~~

Glossary entries are injected into every child-agent prompt. The translator is
instructed to use the target exactly when the source term appears.

## QA Pass

Add `--qa` to write static QA reports after a successful translation:

~~~text
logs/translation_runs/<timestamp>_<paper_stem>/
  qa_report.json
  qa_report.md
~~~

The QA pass does not rewrite translations. It checks glossary target usage,
missing citation markers, missing numbers or percentages, missing URLs, and
records chunk boundaries for human review.

## Ordering Model

The parent script parses the source Markdown into stable blocks before any child process starts.

- Block ids are generated in source order: b0001, b0002, b0003.
- Chunk ids are generated in source order: chunk_0001, chunk_0002, chunk_0003.
- Child Codex processes translate only their assigned numbered chunk.
- Child processes never edit the paper Markdown.
- The parent process validates all returned ids and merges only by original block order.

Parallelism only changes how quickly chunk JSON files arrive. It cannot change the final order.

## Interleaved Output

The sidecar or in-place target file is written as one marked translation block:

~~~markdown
<!-- paper-downloader-translation:begin zh-CN mode=interleaved source_sha256=... -->

<!-- zh:b0001 -->
中文直译

<!-- en:b0001 -->
English source

<!-- paper-downloader-translation:end -->
~~~

Each Chinese block is directly above its English source block. Code blocks, image-only lines, URL-only lines, and HTML tables are preserved without generated Chinese blocks.

## Stability and Resume

Each run writes a manifest and chunk artifacts under:

~~~text
logs/translation_runs/<timestamp>_<paper_stem>/
  manifest.json
  chunks/source/chunk_0001.json
  chunks/prompts/chunk_0001.txt
  chunks/raw/chunk_0001.json
  chunks/translated/chunk_0001.json
~~~

Use --resume with the same --run-dir to reuse successful translated chunk JSON files. If a chunk fails after retries, the script writes failures.json and does not modify the paper.

Before writeback, the script:

- checks source_sha256 so concurrent edits are not overwritten
- checks every translatable block has exactly one non-empty translation
- rejects unexpected, duplicate, or missing block ids
- creates <paper>.translation-backup-YYYYMMDD_HHMMSS.md only in --output-mode in-place
- writes through a temporary file and atomic replace

## Replacing Existing Translation

A second in-place run refuses to write if paper-downloader translation markers already exist. Use --replace-existing-translation only when the old marked translation block should be removed and regenerated from the English source that follows it. Sidecar mode leaves the source Markdown untouched.
