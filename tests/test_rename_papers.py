import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

import rename_papers


class RenamePapersTests(TestCase):
    def test_manifest_auto_metadata_fills_missing_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pdf_path = base / 'raw.pdf'
            pdf_path.write_bytes(b'%PDF-1.4\nplaceholder')
            manifest_path = base / 'manifest.json'
            manifest_path.write_text('{"papers": [{"file": "raw.pdf", "keywords": ["HSPS"]}]}', encoding='utf-8')

            with patch.object(
                rename_papers,
                'extract_metadata_from_pdf',
                return_value={
                    'year': '1997',
                    'first_author': 'Aron',
                    'keywords': [],
                    'title': 'Highly Sensitive Person Scale',
                    'journal': 'JPSP',
                },
            ):
                results = rename_papers.rename_papers_from_manifest(
                    str(manifest_path),
                    dry_run=True,
                    auto_metadata=True,
                )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['success'])
        self.assertTrue(results[0]['new'].endswith('1997_Aron_HSPS_JPSP.pdf'))

    def test_organize_pdf_moves_to_fixed_library_and_applies_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pdf_path = base / 'raw.pdf'
            pdf_path.write_bytes(b'%PDF-1.4\nplaceholder')
            dest_dir = base / 'paper'
            tag_calls = []

            with patch.object(
                rename_papers,
                'apply_finder_tags',
                side_effect=lambda path, tags: tag_calls.append((Path(path), list(tags))),
            ), patch.object(rename_papers, 'record_archive'):
                result = rename_papers.organize_pdf(
                    str(pdf_path),
                    {
                        'year': '2025',
                        'first_author': 'Chen',
                        'keywords': ['AI4Research'],
                        'journal': 'arXiv',
                    },
                    destination_dir=str(dest_dir),
                    finder_tags=['AI在学术上的应用'],
                )
            self.assertTrue(result['success'])
            self.assertEqual(
                Path(result['new']),
                dest_dir / '2025_Chen_AI4Research_arXiv' / '2025_Chen_AI4Research_arXiv.pdf',
            )
            self.assertEqual(
                tag_calls,
                [(dest_dir / '2025_Chen_AI4Research_arXiv' / '2025_Chen_AI4Research_arXiv.pdf', ['AI在学术上的应用'])],
            )
            md = dest_dir / '2025_Chen_AI4Research_arXiv' / '2025_Chen_AI4Research_arXiv.md'
            text = md.read_text(encoding='utf-8')
            self.assertIn('p0_import_source: "paper-downloader"', text)
            self.assertNotIn('fileClass:', text)

    def test_default_library_is_user_paper_folder(self):
        self.assertEqual(
            rename_papers.DEFAULT_LIBRARY_DIR,
            str((Path.home() / "Documents" / "PAPER" / "Papers")),
        )

    def test_normalize_finder_tags_preserves_spaces(self):
        self.assertEqual(
            rename_papers.normalize_finder_tags([' romantic   competence ', 'Romantic competence']),
            ['romantic competence'],
        )
