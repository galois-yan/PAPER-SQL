from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openalex_mcp.local.manage import library_export
from openalex_mcp.local.manager import LibraryManager


class ExportSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name)
        self.library = LibraryManager(self.home / "library.db")
        self.library.upsert_work(
            {
                "id": "W1",
                "title": "Exported work",
                "publication_year": 2024,
                "cited_by_count": 10,
            }
        )
        self.home_patch = patch(
            "openalex_mcp.local.manage.Path.home",
            return_value=self.home,
        )
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.library.close()
        self.temp_dir.cleanup()

    def test_exports_valid_bib_filename_with_allowed_sort(self):
        result = library_export(
            self.library,
            target="selected.bib",
            sort="cited_by_count:desc",
        )

        target = self.home / ".AI-CACHE" / "openalex" / "collections" / "selected.bib"
        self.assertTrue(target.is_file())
        self.assertIn("Exported 1 works", result)

    def test_rejects_paths_outside_collections(self):
        targets = [
            "../outside.bib",
            "nested/inside.bib",
            str(self.home / "absolute.bib"),
            "not-bib.txt",
        ]

        for target in targets:
            with self.subTest(target=target):
                result = library_export(self.library, target=target)
                self.assertTrue(result.startswith("Error:"), result)

        self.assertFalse((self.home / "outside.bib").exists())
        self.assertFalse((self.home / "absolute.bib").exists())

    def test_rejects_symlink_target_outside_collections(self):
        collections = self.home / ".AI-CACHE" / "openalex" / "collections"
        collections.mkdir(parents=True)
        outside = self.home / "outside.bib"
        outside.write_text("keep", encoding="utf-8")
        link = collections / "linked.bib"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        result = library_export(self.library, target="linked.bib")

        self.assertTrue(result.startswith("Error:"), result)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_rejects_unapproved_sort_expression(self):
        result = library_export(
            self.library,
            target="selected.bib",
            sort="random()",
        )

        self.assertTrue(result.startswith("Error:"), result)
        self.assertFalse(
            (
                self.home
                / ".AI-CACHE"
                / "openalex"
                / "collections"
                / "selected.bib"
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()
