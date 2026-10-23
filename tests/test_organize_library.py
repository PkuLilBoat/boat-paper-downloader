import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import organize_library


class OrganizeLibraryTests(TestCase):
    def write_categories(self, base: Path) -> Path:
        table = base / "categories.json"
        table.write_text(
            json.dumps(
                {
                    "default_category": "00_uncategorized",
                    "categories": [
                        {"slug": "00_uncategorized", "label_zh": "未分类"},
                        {"slug": "01_psychology", "label_zh": "心理学"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return table

    def test_load_categories_rejects_unknown_slug(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            table = self.write_categories(Path(tmpdir))
            data = organize_library.load_categories(table)
            with self.assertRaises(ValueError):
                organize_library.validate_category("made_up", data)

    def test_organize_root_pdf_into_flat_paper_folder(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            library = Path(tmpdir) / "paper"
            library.mkdir()
            pdf = library / "2025_Chen_AI_arXiv.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            (library / "2025_Chen_AI_arXiv.md").write_text("# md", encoding="utf-8")

            with patch.object(organize_library, "record_archive") as record_archive:
                row = organize_library.organize_pdf(
                    pdf,
                    library,
                    "01_psychology",
                    dry_run=False,
                    allowed={"00_uncategorized", "01_psychology"},
                )

            target_dir = library / "2025_Chen_AI_arXiv"
            self.assertEqual(Path(row["final_pdf"]), target_dir / "2025_Chen_AI_arXiv.pdf")
            self.assertTrue((target_dir / "2025_Chen_AI_arXiv.pdf").exists())
            self.assertTrue((target_dir / "2025_Chen_AI_arXiv.md").exists())
            record_archive.assert_called_once()
            self.assertEqual(record_archive.call_args.kwargs["category_slug"], "01_psychology")

    def test_default_library_is_user_paper_folder(self):
        self.assertEqual(
            organize_library.DEFAULT_LIBRARY_DIR,
            (Path.home() / "Documents" / "PAPER" / "Papers"),
        )

    def test_cleanup_moves_intermediates_to_trash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            paper_dir = base / "paper"
            paper_dir.mkdir()
            (paper_dir / "paper.pdf").write_bytes(b"%PDF-1.4\n")
            (paper_dir / "paper.md").write_text("ok", encoding="utf-8")
            (paper_dir / "paper_layout.pdf").write_bytes(b"derived")
            (paper_dir / "paper.json").write_text("{}", encoding="utf-8")
            (paper_dir / "mineru_output").mkdir()
            trash = base / "Trash"

            with patch.object(organize_library, "TRASH_ROOT", trash):
                actions = organize_library.cleanup_paper_dir(paper_dir, "paper", dry_run=False)

            self.assertFalse((paper_dir / "paper_layout.pdf").exists())
            self.assertFalse((paper_dir / "paper.json").exists())
            self.assertFalse((paper_dir / "mineru_output").exists())
            self.assertEqual(len([a for a in actions if a["action"] == "trash_intermediate"]), 3)
            self.assertTrue(any(trash.rglob("paper_layout.pdf")))
