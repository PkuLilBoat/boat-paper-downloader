import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import dedupe_paper_library


class DedupePaperLibraryTests(TestCase):
    def test_dedupes_by_pdf_hash_and_rewrites_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            library = root / "PAPER"
            notes = root / "notes"
            library.mkdir()
            notes.mkdir()
            old = library / "old_stem"
            new = library / "new_stem"
            old.mkdir()
            new.mkdir()
            for folder, stem in ((old, "old_stem"), (new, "new_stem")):
                (folder / f"{stem}.pdf").write_bytes(b"%PDF same\n")
            (old / "old_stem.md").write_text("<!-- paper-downloader-translation:begin -->\n10.1234/demo\n", encoding="utf-8")
            (new / "new_stem.md").write_text("---\ntitle: Demo\n---\n\n10.1234/demo\n", encoding="utf-8")
            (new / "new_stem.translation.zh-cn.md").write_text("translated", encoding="utf-8")
            note = notes / "topic.md"
            note.write_text("[[old_stem|Old]]", encoding="utf-8")

            groups = dedupe_paper_library.find_duplicate_groups(library)
            self.assertEqual(len(groups), 1)
            with patch.object(dedupe_paper_library, "TRASH_ROOT", root / "Trash"):
                row = dedupe_paper_library.apply_group(groups[0], notes, dry_run=False)

            self.assertEqual(row["canonical"], str(new))
            self.assertFalse(old.exists())
            self.assertIn("[[new_stem|Old]]", note.read_text(encoding="utf-8"))
            self.assertIn("old_stem", (new / "new_stem.md").read_text(encoding="utf-8"))

    def test_pdf_hash_only_with_weak_metadata_requires_review(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            library = root / "PAPER"
            notes = root / "notes"
            library.mkdir()
            notes.mkdir()
            first = library / "2022_Francis_Village_FPTS_MBTI_mental_health"
            second = library / "2025_Rajeswari_psychometric_properties_MBTI_IJSSR"
            first.mkdir()
            second.mkdir()
            for folder in (first, second):
                (folder / f"{folder.name}.pdf").write_bytes(b"%PDF placeholder reused\n")
                (folder / f"{folder.name}.md").write_text("# No DOI here\n", encoding="utf-8")

            groups = dedupe_paper_library.find_duplicate_groups(library)
            self.assertEqual(len(groups), 1)
            row = dedupe_paper_library.apply_group(groups[0], notes, dry_run=False)

            self.assertEqual(row["status"], "requires_review")
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
