import sys
import tempfile
from pathlib import Path
from unittest import TestCase

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import apply_semantic_rename_map


class ApplySemanticRenameMapTests(TestCase):
    def test_existing_target_blocks_suffix_duplicate_creation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            library = Path(tmpdir)
            old_dir = library / "old_semantic_name"
            target_dir = library / "2026_Test_target"
            old_dir.mkdir()
            target_dir.mkdir()
            (old_dir / "old_semantic_name.pdf").write_bytes(b"%PDF old")
            (target_dir / "2026_Test_target.pdf").write_bytes(b"%PDF target")

            row = apply_semantic_rename_map.apply_row(
                library,
                library / "inbox" / "06_problematic_papers_review",
                {
                    "old_name": "old_semantic_name",
                    "new_stem": "2026_Test_target",
                    "confidence": 0.99,
                    "evidence": "test",
                },
                threshold=0.70,
                dry_run=False,
            )

            self.assertEqual(row["status"], "target_exists_requires_dedupe")
            self.assertTrue(old_dir.exists())
            self.assertTrue(target_dir.exists())
            self.assertFalse((library / "2026_Test_target_2").exists())
