import sys
import tempfile
from pathlib import Path
from unittest import TestCase

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import markdown_blockify
import normalize_paper_names


class MarkdownBlockifyTests(TestCase):
    def test_blockifies_citation_sentences_and_preserves_structure(self):
        source = """---
title: Demo
---

# Heading

Dating and romantic relationships are an important part of development. Although some recent studies note a possible decline over time (Beckmeyer et al. 2020), many studies have found that most adolescents date (Beckmeyer et al. 2020, Carver et al. 2003). Adolescent relationships involve intimacy (Collins et al. 2009). These relationships last about 6-12 months (Connolly & McIsaac 2011).

![figure](images/a.png)

- Existing block

```
plain paragraph should stay untouched
```
"""
        rendered = markdown_blockify.blockify_markdown(source)

        self.assertIn("---\ntitle: Demo\n---\n\n# Heading", rendered)
        self.assertIn("- Dating and romantic relationships are an important part of development. Although some recent studies note a possible decline over time (Beckmeyer et al. 2020), many studies have found that most adolescents date (Beckmeyer et al. 2020, Carver et al. 2003).", rendered)
        self.assertIn("- Adolescent relationships involve intimacy (Collins et al. 2009).", rendered)
        self.assertIn("- These relationships last about 6-12 months (Connolly & McIsaac 2011).", rendered)
        self.assertIn("![figure](images/a.png)", rendered)
        self.assertIn("- Existing block", rendered)
        self.assertIn("```\nplain paragraph should stay untouched\n```", rendered)

    def test_single_paragraph_without_multiple_citations_becomes_one_block(self):
        rendered = markdown_blockify.blockify_markdown("A plain wrapped\nparagraph without citations.\n")
        self.assertEqual(rendered, "- A plain wrapped paragraph without citations.\n")

    def test_already_blockified_markdown_is_unchanged(self):
        source = "- Existing citation block (Smith et al. 2020).\n- Second block.\n"
        self.assertEqual(markdown_blockify.blockify_markdown(source), source)

    def test_list_continuations_and_tables_are_not_reprocessed(self):
        source = (
            "- Existing item (Smith et al. 2020)\n"
            "  continuation line stays inside the same list item\n\n"
            "<table>\n"
            "<tr><td>Cell with text (Jones 2021)</td></tr>\n"
            "</table>\n\n"
            "New paragraph (Brown et al. 2022).\n"
        )
        rendered = markdown_blockify.blockify_markdown(source)
        self.assertIn("  continuation line stays inside the same list item", rendered)
        self.assertIn("<tr><td>Cell with text (Jones 2021)</td></tr>", rendered)
        self.assertIn("- New paragraph (Brown et al. 2022).", rendered)
        self.assertNotIn("- <tr><td>", rendered)

    def test_contents_section_is_preserved(self):
        source = "# Paper\n\n## Contents\nINTRODUCTION 1.1   \nMETHODS 1.2   \n\n## Body\nPlain text.\n"
        rendered = markdown_blockify.blockify_markdown(source)
        self.assertIn("## Contents\nINTRODUCTION 1.1   \nMETHODS 1.2", rendered)
        self.assertIn("## Body\n- Plain text.", rendered)
        self.assertNotIn("- INTRODUCTION 1.1 METHODS", rendered)

    def test_iter_markdown_paths_skips_inbox_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            paper = base / "paper" / "paper.md"
            inbox = base / "inbox" / "report.md"
            readme = base / "README.md"
            paper.parent.mkdir()
            inbox.parent.mkdir()
            paper.write_text("paper", encoding="utf-8")
            inbox.write_text("report", encoding="utf-8")
            readme.write_text("readme", encoding="utf-8")

            self.assertEqual(markdown_blockify.iter_markdown_paths([str(base)]), [paper.resolve()])
            self.assertEqual(
                markdown_blockify.iter_markdown_paths([str(base)], include_inbox=True),
                [inbox.resolve(), paper.resolve()],
            )

    def test_normalize_paper_names_applies_blockify_to_main_markdown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            paper_dir = Path(tmpdir) / "2026_Test_Paper"
            paper_dir.mkdir()
            md = paper_dir / "content.md"
            md.write_text("# Title\n\nFirst claim (Smith et al. 2020). Second claim (Jones & Lee 2021).\n", encoding="utf-8")

            row = normalize_paper_names.normalize_folder(paper_dir, dry_run=False)

            target = paper_dir / "2026_Test_Paper.md"
            self.assertTrue(target.exists())
            self.assertIn({"action": "blockify_markdown", "from": str(target), "to": str(target)}, row["actions"])
            self.assertIn("- First claim (Smith et al. 2020).", target.read_text(encoding="utf-8"))
            self.assertIn("- Second claim (Jones & Lee 2021).", target.read_text(encoding="utf-8"))

    def test_normalize_paper_names_blocks_duplicate_suffix_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            existing = base / "2026_Test_Paper"
            messy = base / "2026  Test  Paper"
            existing.mkdir()
            messy.mkdir()
            (messy / "content.md").write_text("# Title\n\nBody.", encoding="utf-8")

            row = normalize_paper_names.normalize_folder(messy, dry_run=False)

            self.assertEqual(row["status"], "target_exists_requires_dedupe")
            self.assertTrue(messy.exists())
            self.assertTrue(existing.exists())
            self.assertFalse((base / "2026_Test_Paper_2").exists())
