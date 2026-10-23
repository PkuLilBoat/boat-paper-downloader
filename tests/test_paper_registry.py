import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import paper_registry


class PaperRegistryTests(TestCase):
    def test_record_archive_persists_to_sqlite_and_exports_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db_path = base / "paper_registry.sqlite"
            json_path = base / "paper_registry.json"
            md_path = base / "paper_registry.md"

            with patch.object(paper_registry, "LOGS_DIR", base), patch.object(
                paper_registry, "REGISTRY_DB", db_path
            ), patch.object(paper_registry, "REGISTRY_JSON", json_path), patch.object(
                paper_registry, "REGISTRY_MD", md_path
            ):
                entry = paper_registry.record_archive(
                    doi="10.1000/test",
                    raw_path="/tmp/raw.pdf",
                    final_path="/tmp/final.pdf",
                    metadata={
                        "title": "Registry Test",
                        "year": "2026",
                        "first_author": "Chen",
                        "journal": "JPA",
                        "keywords": ["Rorschach"],
                    },
                    finder_tags=["投射测验"],
                    source_method="organize_pdf",
                )

                self.assertEqual(entry["doi"], "10.1000/test")
                self.assertTrue(db_path.exists())
                self.assertTrue(json_path.exists())
                self.assertTrue(md_path.exists())

                with sqlite3.connect(db_path) as conn:
                    row = conn.execute(
                        "SELECT doi, first_author, status, keywords_json FROM papers WHERE key = ?",
                        ("doi:10.1000/test",),
                    ).fetchone()

                self.assertEqual(row[0], "10.1000/test")
                self.assertEqual(row[1], "Chen")
                self.assertEqual(row[2], "archived")
                self.assertIn("Rorschach", row[3])

    def test_find_registry_entries_filters_by_query(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db_path = base / "paper_registry.sqlite"
            json_path = base / "paper_registry.json"
            md_path = base / "paper_registry.md"

            with patch.object(paper_registry, "LOGS_DIR", base), patch.object(
                paper_registry, "REGISTRY_DB", db_path
            ), patch.object(paper_registry, "REGISTRY_JSON", json_path), patch.object(
                paper_registry, "REGISTRY_MD", md_path
            ):
                paper_registry.record_download_result(
                    {
                        "doi": "10.1000/a",
                        "title": "Rorschach Meta Analysis",
                        "success": True,
                        "method": "oa",
                        "file_path": "/tmp/a.pdf",
                    }
                )
                paper_registry.record_download_result(
                    {
                        "doi": "10.1000/b",
                        "title": "Attachment Review",
                        "success": True,
                        "method": "oa",
                        "file_path": "/tmp/b.pdf",
                    }
                )

                results = paper_registry.find_registry_entries(query="Rorschach", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["doi"], "10.1000/a")

    def test_backfill_from_session_logs_imports_session_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db_path = base / "paper_registry.sqlite"
            json_path = base / "paper_registry.json"
            md_path = base / "paper_registry.md"
            (base / "session_20260426_010101.md").write_text(
                "\n".join(
                    [
                        "# Download Session",
                        "",
                        "| DOI | Title | Method | Status | Notes |",
                        "|-----|-------|--------|--------|-------|",
                        "| 10.1000/test | Session Imported | scihub | ✅ SUCCESS | 120KB |",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.object(paper_registry, "LOGS_DIR", base), patch.object(
                paper_registry, "REGISTRY_DB", db_path
            ), patch.object(paper_registry, "REGISTRY_JSON", json_path), patch.object(
                paper_registry, "REGISTRY_MD", md_path
            ):
                result = paper_registry.backfill_from_session_logs()
                entries = paper_registry.find_registry_entries(doi="10.1000/test")

        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["title"], "Session Imported")

    def test_import_collection_markdown_links_items_to_papers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db_path = base / "paper_registry.sqlite"
            json_path = base / "paper_registry.json"
            md_path = base / "paper_registry.md"
            source = base / "心理测量综述文献库_20260426.md"
            source.write_text(
                "\n".join(
                    [
                        "# 心理测量综述文献库_20260426",
                        "",
                        "| Title | DOI | Author | Year | Journal | Tags | Notes |",
                        "|-------|-----|--------|------|---------|------|-------|",
                        "| The validity of individual Rorschach variables | 10.1037/a0033689 | Mihura | 2013 | Psychological Bulletin | Rorschach, meta-analysis | core evidence |",
                        "| SCORS-G comment | 10.1037/pas0001437 | Stein | 2026 | Psychological Assessment | SCORS-G | response paper |",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.object(paper_registry, "LOGS_DIR", base), patch.object(
                paper_registry, "REGISTRY_DB", db_path
            ), patch.object(paper_registry, "REGISTRY_JSON", json_path), patch.object(
                paper_registry, "REGISTRY_MD", md_path
            ):
                result = paper_registry.import_collection_markdown(str(source))
                collections = paper_registry.list_collections()
                collection = paper_registry.get_collection("心理测量综述文献库_20260426")

        self.assertEqual(result["imported"], 2)
        self.assertEqual(collections[0]["item_count"], 2)
        self.assertEqual(collection["items"][0]["doi"], "10.1037/a0033689")
        self.assertIn("Rorschach", collection["items"][0]["keywords"])

    def test_export_collection_markdown_renders_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            db_path = base / "paper_registry.sqlite"
            json_path = base / "paper_registry.json"
            md_path = base / "paper_registry.md"
            source = base / "collection.md"
            output = base / "export.md"
            source.write_text(
                "\n".join(
                    [
                        "# Test Collection",
                        "",
                        "- The validity of individual Rorschach variables 10.1037/a0033689",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.object(paper_registry, "LOGS_DIR", base), patch.object(
                paper_registry, "REGISTRY_DB", db_path
            ), patch.object(paper_registry, "REGISTRY_JSON", json_path), patch.object(
                paper_registry, "REGISTRY_MD", md_path
            ):
                paper_registry.import_collection_markdown(str(source))
                rendered = paper_registry.export_collection_markdown("Test Collection", str(output))
                exported_text = output.read_text(encoding="utf-8")

            self.assertIn("# Test Collection", rendered)
            self.assertIn("10.1037/a0033689", exported_text)
