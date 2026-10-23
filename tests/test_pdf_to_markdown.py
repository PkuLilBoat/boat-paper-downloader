import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pdf_to_markdown


class PdfToMarkdownTests(TestCase):
    def test_dry_run_uses_magic_pdf_command_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir) / "paper"
            folder.mkdir()
            pdf = folder / "paper.pdf"
            md = folder / "paper.md"
            pdf.write_bytes(b"%PDF-1.4\n")
            result = pdf_to_markdown.convert_pdf_to_markdown(pdf, md, dry_run=True)
            self.assertEqual(result["status"], "dry_run")
            self.assertIn("magic-pdf", result["command"][0])
            self.assertIn("-m", result["command"])

    def test_compacts_mineru_markdown_to_canonical_md_and_images(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            folder = Path(tmpdir) / "paper"
            folder.mkdir()
            pdf = folder / "paper.pdf"
            md = folder / "paper.md"
            pdf.write_bytes(b"%PDF-1.4\n")
            md.write_text("# old\n", encoding="utf-8")
            mineru_md = folder / "mineru_output" / "paper" / "txt" / "paper.md"
            images = mineru_md.parent / "images"
            images.mkdir(parents=True)
            mineru_md.write_text("# New\n\n![x](images/a.png)\n", encoding="utf-8")
            (images / "a.png").write_bytes(b"png")

            with patch.object(pdf_to_markdown, "run_mineru", return_value={"cmd": ["magic-pdf"], "returncode": 0, "stdout": "", "stderr": ""}):
                result = pdf_to_markdown.convert_pdf_to_markdown(pdf, md, import_source="paper-downloader")

            self.assertEqual(result["status"], "converted")
            self.assertIn("# New", md.read_text(encoding="utf-8"))
            self.assertTrue((folder / "images" / "a.png").exists())
            self.assertFalse((folder / "mineru_output").exists())
            self.assertIn('p0_import_source: "paper-downloader"', md.read_text(encoding="utf-8"))
