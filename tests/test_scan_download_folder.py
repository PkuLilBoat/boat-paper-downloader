import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

import scan_download_folder


class ScanDownloadFolderTests(TestCase):
    def test_browser_download_scan_converts_archived_pdf_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            source_pdf = base / 'recent.pdf'
            source_pdf.write_bytes(b'%PDF-1.4\nplaceholder')
            archive = base / 'library' / 'paper' / 'paper.pdf'
            archive.parent.mkdir(parents=True)

            def fake_organize(*args, **kwargs):
                source_pdf.rename(archive)
                (archive.with_suffix('.md')).write_text('# stub\n', encoding='utf-8')
                return {'success': True, 'new': str(archive)}

            with patch.object(scan_download_folder, 'load_registry', return_value={'papers': []}), patch.object(
                scan_download_folder, 'extract_metadata_from_pdf', return_value={'title': 'Paper'}
            ), patch.object(scan_download_folder, 'organize_pdf', side_effect=fake_organize), patch.object(
                scan_download_folder, 'record_archive'
            ), patch.object(
                scan_download_folder,
                'convert_pdf_to_markdown',
                return_value={'status': 'converted', 'md_path': str(archive.with_suffix('.md'))},
            ) as convert:
                results = scan_download_folder.scan_download_folder(source_dir=str(base), hours=24)

        self.assertEqual(results[0]['conversion_status'], 'converted')
        self.assertEqual(convert.call_count, 1)

    def test_browser_download_scan_can_explicitly_disable_conversion(self):
        with patch.object(sys, 'argv', ['scan_download_folder.py', '--no-convert']):
            args = scan_download_folder.parse_args()
        self.assertFalse(args.convert_to_markdown)

    def test_scan_download_folder_archives_recent_unknown_pdfs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pdf_path = base / 'recent.pdf'
            pdf_path.write_bytes(b'%PDF-1.4\nplaceholder')

            archived = []

            with patch.object(scan_download_folder, 'load_registry', return_value={'papers': []}), patch.object(
                scan_download_folder,
                'extract_metadata_from_pdf',
                return_value={
                    'year': '2025',
                    'first_author': 'Zhou',
                    'keywords': [],
                    'title': 'Recent Paper',
                    'journal': 'Journal',
                },
            ), patch.object(
                scan_download_folder,
                'organize_pdf',
                side_effect=lambda pdf_path, metadata, **kwargs: {
                    'success': True,
                    'new': '/tmp/paper-downloader-fixtures/Papers/2025_Zhou_romantic_competence_Journal.pdf',
                },
            ), patch.object(
                scan_download_folder,
                'record_archive',
                side_effect=lambda **kwargs: archived.append(kwargs),
            ):
                results = scan_download_folder.scan_download_folder(
                    source_dir=str(base),
                    hours=24,
                    finder_tags=['romantic competence'],
                    keywords=['romantic_competence'],
                )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['success'])
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]['status'], 'scanned_from_downloads')

    def test_scan_download_folder_skips_known_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pdf_path = base / 'known.pdf'
            pdf_path.write_bytes(b'%PDF-1.4\nplaceholder')

            with patch.object(
                scan_download_folder,
                'load_registry',
                return_value={'papers': [{'raw_path': str(pdf_path.resolve())}]},
            ), patch.object(scan_download_folder, 'organize_pdf') as organize_mock:
                results = scan_download_folder.scan_download_folder(source_dir=str(base), hours=24)

        self.assertEqual(results, [])
        organize_mock.assert_not_called()
