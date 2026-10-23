import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import translate_markdown


class TranslateMarkdownTests(TestCase):
    def test_block_parser_uses_stable_contiguous_ids(self):
        source = """# Title

First paragraph spans
two lines.

- Existing list item

![figure](images/a.png)

~~~
do not translate
~~~

<table>
<tr><td>Keep table</td></tr>
</table>
"""
        blocks = translate_markdown.parse_markdown_blocks(source)
        self.assertEqual([block.block_id for block in blocks], [f"b{i:04d}" for i in range(1, len(blocks) + 1)])
        by_id = {block.block_id: block for block in blocks}
        self.assertEqual(by_id["b0001"].kind, "heading")
        self.assertTrue(by_id["b0001"].translatable)
        self.assertEqual(by_id["b0003"].kind, "paragraph")
        self.assertTrue(by_id["b0003"].translatable)
        self.assertEqual(by_id["b0007"].kind, "image")
        self.assertFalse(by_id["b0007"].translatable)
        self.assertTrue(any(block.kind == "code" and not block.translatable for block in blocks))
        self.assertTrue(any(block.kind == "html_table" and not block.translatable for block in blocks))

    def test_render_interleaves_by_original_block_order_even_with_reversed_results(self):
        source = "# Abstract\n\n- First English block.\n- Second English block.\n"
        blocks = translate_markdown.parse_markdown_blocks(source)
        translations = {
            block.block_id: f"中文 {block.block_id}"
            for block in reversed([block for block in blocks if block.translatable])
        }

        rendered = translate_markdown.render_interleaved(blocks, translations, "abc123")

        self.assertIn("<!-- paper-downloader-translation:begin zh-CN mode=interleaved source_sha256=abc123 -->", rendered)
        self.assertLess(rendered.index("<!-- zh:b0001 -->"), rendered.index("<!-- en:b0001 -->"))
        self.assertLess(rendered.index("<!-- en:b0001 -->"), rendered.index("<!-- zh:b0003 -->"))
        self.assertIn("中文 b0001\n\n<!-- en:b0001 -->\n# Abstract", rendered)
        self.assertIn("中文 b0003\n\n<!-- en:b0003 -->\n- First English block.", rendered)
        self.assertIn("- Second English block.", rendered)

    def test_chunk_validation_rejects_mismatched_chunk_or_source(self):
        block = translate_markdown.MarkdownBlock("b0001", "paragraph", "Text", True)
        chunk = translate_markdown.TranslationChunk("chunk_0001", "sha", [block])

        with self.assertRaises(translate_markdown.TranslationError):
            translate_markdown.validate_chunk_result(
                chunk,
                {"chunk_id": "chunk_9999", "source_sha256": "sha", "translations": [{"block_id": "b0001", "translation": "文本"}]},
            )

        with self.assertRaises(translate_markdown.TranslationError):
            translate_markdown.validate_chunk_result(
                chunk,
                {"chunk_id": "chunk_0001", "source_sha256": "other", "translations": [{"block_id": "b0001", "translation": "文本"}]},
            )

    def test_chunk_validation_rejects_missing_duplicate_and_empty_blocks(self):
        blocks = [
            translate_markdown.MarkdownBlock("b0001", "paragraph", "One", True),
            translate_markdown.MarkdownBlock("b0002", "paragraph", "Two", True),
        ]
        chunk = translate_markdown.TranslationChunk("chunk_0001", "sha", blocks)

        for rows in (
            [{"block_id": "b0001", "translation": "一"}],
            [{"block_id": "b0001", "translation": "一"}, {"block_id": "b0001", "translation": "一 again"}],
            [{"block_id": "b0001", "translation": ""}, {"block_id": "b0002", "translation": "二"}],
        ):
            with self.assertRaises(translate_markdown.TranslationError):
                translate_markdown.validate_chunk_result(chunk, {"chunk_id": "chunk_0001", "source_sha256": "sha", "translations": rows})

    def test_dry_run_does_not_modify_markdown_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            md = tmp / "paper.md"
            source = "# Title\n\n- Original English.\n"
            md.write_text(source, encoding="utf-8")
            run_dir = tmp / "run"

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "translate_markdown.py"),
                    str(md),
                    "--dry-run",
                    "--run-dir",
                    str(run_dir),
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(md.read_text(encoding="utf-8"), source)
            data = json.loads(proc.stdout)
            self.assertTrue(data["dry_run"])
            self.assertEqual(data["concurrency"], 5)
            self.assertTrue((run_dir / "manifest.json").exists())

    def test_apply_with_resume_uses_existing_chunk_json_and_backs_up_original(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            md = tmp / "paper.md"
            source = "# Title\n\n- Original English.\n"
            md.write_text(source, encoding="utf-8")
            source_sha = translate_markdown.sha256_text(source)
            blocks = translate_markdown.parse_markdown_blocks(source)
            chunks = translate_markdown.build_chunks(blocks, source_sha, chunk_chars=200)
            run_dir = tmp / "run"
            translated_dir = run_dir / "chunks" / "translated"
            translated_dir.mkdir(parents=True)

            for chunk in chunks:
                payload = {
                    "chunk_id": chunk.chunk_id,
                    "source_sha256": chunk.source_sha256,
                    "translations": [
                        {"block_id": block.block_id, "translation": f"中文 {block.block_id}"}
                        for block in chunk.blocks
                    ],
                }
                (translated_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "translate_markdown.py"),
                    str(md),
                    "--apply",
                    "--resume",
                    "--output-mode",
                    "in-place",
                    "--run-dir",
                    str(run_dir),
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            rendered = md.read_text(encoding="utf-8")
            self.assertIn("<!-- zh:b0001 -->\n中文 b0001\n\n<!-- en:b0001 -->\n# Title", rendered)
            self.assertIn("<!-- zh:b0003 -->\n中文 b0003\n\n<!-- en:b0003 -->\n- Original English.", rendered)
            self.assertEqual(rendered.count("paper-downloader-translation:begin"), 1)
            self.assertEqual(len(list(tmp.glob("paper.translation-backup-*.md"))), 1)

    def test_default_apply_writes_sidecar_without_changing_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            md = tmp / "paper.md"
            source = "# Title\n\n- Original English.\n"
            md.write_text(source, encoding="utf-8")
            source_sha = translate_markdown.sha256_text(source)
            blocks = translate_markdown.parse_markdown_blocks(source)
            chunks = translate_markdown.build_chunks(blocks, source_sha, chunk_chars=200)
            run_dir = tmp / "run"
            translated_dir = run_dir / "chunks" / "translated"
            translated_dir.mkdir(parents=True)
            for chunk in chunks:
                payload = {
                    "chunk_id": chunk.chunk_id,
                    "source_sha256": chunk.source_sha256,
                    "translations": [
                        {"block_id": block.block_id, "translation": f"中文 {block.block_id}"}
                        for block in chunk.blocks
                    ],
                }
                (translated_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "translate_markdown.py"),
                    str(md),
                    "--apply",
                    "--resume",
                    "--run-dir",
                    str(run_dir),
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            data = json.loads(proc.stdout)
            sidecar = (tmp / "paper.translation.zh-cn.md").resolve()
            updated_source = md.read_text(encoding="utf-8")
            self.assertIn('translation_zh_cn: "[[paper.translation.zh-cn|中文译文]]"', updated_source)
            self.assertTrue(updated_source.endswith(source))
            self.assertEqual(data["output_path"], str(sidecar))
            self.assertTrue(sidecar.exists())
            sidecar_text = sidecar.read_text(encoding="utf-8")
            self.assertIn('source_paper: "[[paper|英文原文]]"', sidecar_text)
            self.assertIn("<!-- zh:b0001 -->", sidecar_text)
            self.assertFalse(list(tmp.glob("paper.translation-backup-*.md")))

    def test_custom_output_path_writes_requested_sidecar(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            md = tmp / "paper.md"
            output = tmp / "custom.zh.md"
            source = "# Title\n\n- Original English.\n"
            md.write_text(source, encoding="utf-8")
            source_sha = translate_markdown.sha256_text(source)
            blocks = translate_markdown.parse_markdown_blocks(source)
            chunks = translate_markdown.build_chunks(blocks, source_sha, chunk_chars=200)
            run_dir = tmp / "run"
            translated_dir = run_dir / "chunks" / "translated"
            translated_dir.mkdir(parents=True)
            for chunk in chunks:
                payload = {
                    "chunk_id": chunk.chunk_id,
                    "source_sha256": chunk.source_sha256,
                    "translations": [
                        {"block_id": block.block_id, "translation": f"中文 {block.block_id}"}
                        for block in chunk.blocks
                    ],
                }
                (translated_dir / f"{chunk.chunk_id}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "translate_markdown.py"),
                    str(md),
                    "--apply",
                    "--resume",
                    "--output-path",
                    str(output),
                    "--run-dir",
                    str(run_dir),
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertTrue(md.read_text(encoding="utf-8").endswith(source))
            self.assertTrue(output.exists())

    def test_glossary_json_tsv_and_prompt_injection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            json_path = tmp / "glossary.json"
            tsv_path = tmp / "glossary.tsv"
            json_path.write_text(json.dumps({"terms": [{"source": "romantic competence", "target": "浪漫能力", "note": "fixed"}]}, ensure_ascii=False), encoding="utf-8")
            tsv_path.write_text("AICQc\tAICQc\tacronym\n", encoding="utf-8")

            json_terms = translate_markdown.load_glossary(str(json_path))
            tsv_terms = translate_markdown.load_glossary(str(tsv_path))
            self.assertEqual(json_terms[0].target, "浪漫能力")
            self.assertEqual(tsv_terms[0].source, "AICQc")

            chunk = translate_markdown.TranslationChunk("chunk_0001", "sha", [translate_markdown.MarkdownBlock("b0001", "paragraph", "romantic competence", True)])
            prompt = translate_markdown.build_prompt(chunk, json_terms)
            self.assertIn("romantic competence", prompt)
            self.assertIn("浪漫能力", prompt)

    def test_static_qa_reports_glossary_and_marker_issues(self):
        source = "# Title\n\n- romantic competence scored 12.5% [1].\n"
        blocks = translate_markdown.parse_markdown_blocks(source)
        source_sha = translate_markdown.sha256_text(source)
        chunks = translate_markdown.build_chunks(blocks, source_sha, chunk_chars=200)
        translations = {
            block.block_id: "中文"
            for block in blocks
            if block.translatable
        }
        glossary = [translate_markdown.GlossaryTerm("romantic competence", "浪漫能力", "fixed")]
        qa = translate_markdown.run_static_qa(blocks, translations, glossary, chunks)
        issue_types = {issue["type"] for issue in qa["issues"]}
        self.assertEqual(qa["status"], "issues_found")
        self.assertIn("glossary_missing", issue_types)
        self.assertIn("number_missing", issue_types)
        self.assertIn("citation_missing", issue_types)

    def test_strip_yaml_frontmatter_before_translation(self):
        source = "---\ntitle: Demo\n---\n\n# Body\n"
        stripped = translate_markdown.strip_yaml_frontmatter(source)
        self.assertEqual(stripped, "# Body\n")
