import sys
import tempfile
from pathlib import Path
from unittest import TestCase

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import update_paper_properties


class UpdatePaperPropertiesTests(TestCase):
    def test_uses_obsidian_library_and_writes_only_deterministic_ingest_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            library = Path(tmpdir) / "Papers"
            folder = library / "2025_Chen_AI_Journal"
            folder.mkdir(parents=True)
            md = folder / "2025_Chen_AI_Journal.md"
            pdf = folder / "2025_Chen_AI_Journal.pdf"
            md.write_text("# Title\n", encoding="utf-8")
            pdf.write_bytes(b"%PDF-1.4\n")

            update_paper_properties.update_markdown_properties(
                md,
                library_dir=library,
                import_source="paper-downloader",
            )
            text = md.read_text(encoding="utf-8")

            self.assertEqual(
                update_paper_properties.DEFAULT_LIBRARY_DIR,
                (Path.home() / "Documents" / "PAPER" / "Papers"),
            )
            self.assertIn('paper_key: "2025_Chen_AI_Journal"', text)  # gitleaks:allow -- paper identifier fixture
            self.assertIn('p0_local_pdf: "Papers/2025_Chen_AI_Journal/2025_Chen_AI_Journal.pdf"', text)
            self.assertIn('p0_local_md: "Papers/2025_Chen_AI_Journal/2025_Chen_AI_Journal.md"', text)
            self.assertIn('p0_import_source: "paper-downloader"', text)
            self.assertNotIn("fileClass:", text)

    def test_rejects_unknown_import_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir) / "paper"
            folder.mkdir()
            md = folder / "paper.md"
            md.write_text("# Title\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                update_paper_properties.update_markdown_properties(md, import_source="browser")

    def test_canonical_source_does_not_add_sidecar_navigation_properties(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir) / "2019_Faber_IRRC_Interpersona"
            folder.mkdir()
            md = folder / "2019_Faber_IRRC_Interpersona.md"
            pdf = folder / "2019_Faber_IRRC_Interpersona.pdf"
            translation = folder / "2019_Faber_IRRC_Interpersona.translation.zh-cn.md"
            md.write_text("# Title\n", encoding="utf-8")
            pdf.write_bytes(b"%PDF-1.4\n")
            translation.write_text("# 中文\n", encoding="utf-8")

            row = update_paper_properties.update_markdown_properties(md, translation_path=translation)
            text = md.read_text(encoding="utf-8")

            self.assertTrue(row["changed"])
            self.assertIn('p0_local_pdf:', text)
            self.assertNotIn('paper_pdf:', text)
            self.assertNotIn('translation_zh_cn:', text)
            self.assertNotIn('translation_status:', text)
            self.assertTrue(text.endswith("# Title\n"))

    def test_updates_translation_with_source_link(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir) / "paper"
            folder.mkdir()
            source = folder / "paper.md"
            translation = folder / "paper.translation.zh-cn.md"
            pdf = folder / "paper.pdf"
            source.write_text("# Source\n", encoding="utf-8")
            translation.write_text("# Translation\n", encoding="utf-8")
            pdf.write_bytes(b"%PDF-1.4\n")

            update_paper_properties.update_markdown_properties(translation, source_path=source)
            text = translation.read_text(encoding="utf-8")

            self.assertIn('source_paper: "[[paper|英文原文]]"', text)
            self.assertIn('paper_pdf: "[[paper.pdf|PDF]]"', text)

    def test_preserves_legacy_metadata_and_body_without_migration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            library = Path(tmpdir) / "Papers"
            folder = library / "paper"
            folder.mkdir(parents=True)
            md = folder / "paper.md"
            legacy = 'pdf_path: "old.pdf"\nmd_path: "old.md"\npaper_pdf: "[[old.pdf]]"\nfileClass: psychology-paper\ndomains:\n  - existing-domain\n'
            body = "# Original\n\n- (Author, 2020)\n"
            md.write_text("---\n" + legacy + "---\n\n" + body)
            (folder / "paper.pdf").write_bytes(b"%PDF-1.4\n")
            (folder / "images").mkdir()
            before = md.read_bytes()
            dry = update_paper_properties.update_markdown_properties(md, library_dir=library, dry_run=True)
            self.assertTrue(dry['changed'])
            self.assertEqual(md.read_bytes(), before)
            first = update_paper_properties.update_markdown_properties(md, library_dir=library)
            text = md.read_text()
            self.assertIn(legacy, text)
            self.assertTrue(text.endswith(body))
            self.assertEqual(first['properties']['p0_local_assets_dir'], 'Papers/paper/images')
            second = update_paper_properties.update_markdown_properties(md, library_dir=library)
            self.assertFalse(second['changed'])

    def test_pdf_absent_does_not_invent_path_or_classification(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir) / "paper"
            folder.mkdir()
            md = folder / "paper.md"
            md.write_text("# Source\n")
            result = update_paper_properties.update_markdown_properties(md, import_source='local')
            self.assertEqual(set(result['properties']), {'paper_key', 'p0_local_md', 'p0_import_source'})
