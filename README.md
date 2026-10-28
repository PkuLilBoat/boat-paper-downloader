<img src="assets/community-banner.png" alt="Learning and creating in the AI era" width="680">

<table><tr><td width="180"><img src="assets/wechat.jpg" alt="WeChat community QR code" width="156"></td><td>Scan the QR code to join our lifelong learning community and discussion groups.<br>Let's exchange ideas about AI, knowledge management, and productivity tools.<br>WeChat Official Account: 湖畔的赛博格 (same name).</td></tr></table>

---

# BOAT Paper Downloader

English | [简体中文](README.zh-CN.md)

Turn a list of DOIs into local paper full texts. The downloader tries open-access, publisher and browser-assisted routes, saves valid PDFs, converts successful downloads to Markdown through local MinerU by default, and reports unresolved items.

This is the public distribution of the author's `paper-downloader` Skill. The repository and public Skill use `boat-paper-downloader`; script names, result schemas and `p0_import_source: paper-downloader` retain their existing identifiers for compatibility.

## Features and limits

- Download PDFs for one DOI or a batch of DOIs.
- Race open-access and publisher HTTP requests, then fall back to existing configured proxies and bounded browser assistance. The source retains a final Sci-Hub fallback.
- Validate PDFs and record failures or steps requiring human interaction. Availability is not guaranteed.
- Keep per-run attempt traces for diagnosis; PDF acquisition still requires identity and completeness review before library intake.
- Convert successful PDFs into same-stem Markdown with accompanying `images/` assets.
- Optionally rename papers, review duplicates, maintain a SQLite index and collections, or produce interleaved English/Chinese translations.

Downloads stay in the requested output directory by default. Formal library intake requires an explicit option and the user's own library policies. Institutional access requires the user's own valid permissions; no login state is included.

## Installation and compatibility

The core coordinator requires Python 3.10+ on macOS or Linux because it uses Unix `fcntl` locks. Browser assistance requires Playwright.

```bash
git clone https://github.com/PkuLilBoat/boat-paper-downloader.git
cd boat-paper-downloader
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

For Codex or Claude Skill use, place this directory in a Skill location supported by your client, or link it there. The entry point is [SKILL.md](SKILL.md).

Optional dependencies:

- **PDF → Markdown:** install a MinerU version and models that provide the `magic-pdf` command. The current wrapper configures MPS for Apple Silicon; other devices require conversion-environment adaptation. Missing conversion dependencies are reported explicitly.
- **Translation:** `scripts/translate_markdown.py` requires an installed, authenticated `codex` CLI.
- **Finder tags:** available on macOS only.

## Quick start

Download a paper and perform the default conversion:

```bash
python scripts/scihub_downloader.py ./papers 10.1037/0022-3514.73.2.345
```

Download PDF only:

```bash
python scripts/scihub_downloader.py ./papers 10.1037/0022-3514.73.2.345 --no-convert
```

Download a batch and save a result manifest:

```bash
python scripts/scihub_downloader.py ./papers 10.1037/0022-3514.73.2.345 10.1016/j.paid.2007.07.016 --concurrency 2 --result-json ./download-result.json
```

Default output:

```text
papers/
└── <doi-safe-stem>/
    ├── <doi-safe-stem>.pdf
    ├── <doi-safe-stem>.md
    └── images/
```

PDF acquisition and Markdown conversion have separate results. A downloaded PDF does not establish successful conversion; check each stage in the manifest.

## Configuration and privacy

- [local/institution_login.example.json](local/institution_login.example.json) contains placeholders only. Copy it to `local/institution_login.private.json` and supply your own settings if needed. Private configuration is ignored by Git.
- Browser profiles, credentials, logs, run manifests, downloaded papers and databases are local data and are excluded from the public repository. Runtime tools may create these files on your computer.
- The default library location is `~/Documents/PAPER/Papers`. Inspect each script's `--help` for supported target overrides such as `--library-dir` or `--destination-dir`; use available dry-run options before organizing or deduplicating files.
- `--organize-after-download` explicitly enables formal library intake.
- Personal Obsidian rules and Skill management records are excluded. Apply your own naming, classification and provenance policies.
- Built-in download handlers remain available. Cross-run site memory, learned route ranking, host-specific manuals and historical site-validation records from the local edition are excluded. Result traces do not train or reorder future runs.

## Documentation

The detailed guides are currently in Chinese:

- [Browser sessions](docs/institutional-browser.md)
- [Institutional login](docs/institutional-login-automation.md)
- [CNKI integration](docs/cnki-download-integration.md)
- [Download workflow and review](docs/technical-background.md)
- [Library organization](docs/library-organization.md)
- [Markdown translation](docs/markdown-translation.md)

Configured handlers do not guarantee current site accessibility or account entitlement.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests primarily use temporary files and mocked network responses. Passing tests does not verify live publisher login, real DOI acquisition, MinerU models or translation services.

## Distribution

This public copy uses fresh Git history and excludes the original repository's browser profiles, credentials, logs, databases and personal project notes. It is maintained separately from the author's local runtime Skill.

The current tree also excludes the earlier site playbook and validation matrix. Earlier public commits may still contain those documents; this update does not rewrite published Git history. The public source includes built-in handlers and is not encrypted or protected by paid activation.

## License and third-party sources

No open-source license has been assigned to this repository. Source comments in the institutional downloader identify Zotero's full-text lookup logic and site translators as references. Third-party code provenance and license review is not complete, so this update does not apply a blanket MIT license. See the [institutional downloader](scripts/institutional_downloader.py) and [Zotero integration notes](docs/zotero-integration-research.md) for the recorded references.

Python dependencies and external MinerU and Codex CLI tools retain their respective project licenses. Rights in papers and downloaded content are separate from software licensing.

## Community and feedback

You can also report problems or suggest improvements through this repository's [GitHub Issues](https://github.com/PkuLilBoat/boat-paper-downloader/issues). Joining the community is optional and is not required to use the tool.
