from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from openalex_mcp.local.manager import LibraryManager


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.library = LibraryManager(Path(self.temp_dir.name) / "library.db")

    def tearDown(self):
        self.library.close()
        self.temp_dir.cleanup()

    def test_readonly_connection_allows_queries_and_tracks_wal_writes(self):
        self.library.upsert_work({"id": "W1", "title": "First"})

        read_conn = self.library.read_conn
        self.assertIs(read_conn, self.library.read_conn)
        self.assertEqual(
            self.library.execute_readonly("SELECT count(*) FROM works").fetchone()[0],
            1,
        )
        self.assertTrue(
            self.library.execute_readonly("PRAGMA table_info(works)").fetchall()
        )
        distance = self.library.execute_readonly(
            "SELECT vec_distance_cosine("
            "X'0000803f00000000', X'0000803f00000000')"
        ).fetchone()[0]
        self.assertAlmostEqual(distance, 0.0)

        self.library.upsert_work({"id": "W2", "title": "Second"})
        self.assertEqual(
            self.library.execute_readonly(
                "WITH selected AS (SELECT * FROM works) "
                "SELECT count(*) FROM selected"
            ).fetchone()[0],
            2,
        )

    def test_readonly_connection_rejects_mutation_and_unsafe_pragmas(self):
        self.library.upsert_work({"id": "W1", "title": "First"})
        statements = [
            "WITH selected AS (SELECT 1) DELETE FROM works",
            "UPDATE works SET title = 'changed'",
            "INSERT INTO works(id) VALUES ('W2')",
            "CREATE TABLE unexpected(value TEXT)",
            "ATTACH DATABASE ':memory:' AS attached",
            "PRAGMA query_only=OFF",
            "PRAGMA wal_checkpoint",
        ]

        for sql in statements:
            with self.subTest(sql=sql):
                with self.assertRaises(sqlite3.DatabaseError):
                    self.library.execute_readonly(sql)

        row = self.library.conn.execute(
            "SELECT title FROM works WHERE id = 'W1'"
        ).fetchone()
        self.assertEqual(row, ("First",))
        self.assertEqual(self.library.get_work_count(), 1)

    def test_close_releases_and_recreates_both_connections(self):
        self.library.upsert_work({"id": "W1", "title": "First"})
        old_write = self.library.conn
        old_read = self.library.read_conn

        self.library.close()

        with self.assertRaises(sqlite3.ProgrammingError):
            old_write.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            old_read.execute("SELECT 1")
        self.assertIsNot(self.library.conn, old_write)
        self.assertIsNot(self.library.read_conn, old_read)
        self.assertEqual(self.library.get_work_count(), 1)

    def test_upsert_refreshes_metadata_without_overwriting_local_enrichment(self):
        self.library.upsert_work(
            {
                "id": "W1",
                "title": "Old title",
                "publication_year": 2020,
                "doi": "10.1000/old",
                "abstract": "",
                "authorships": [{"author": {"display_name": "Old Author"}}],
            }
        )
        self.library.update_work_fulltext("W1", "Locally extracted full text")
        self.library.store_embeddings_batch([("W1", [1.0, 0.0])])

        self.library.upsert_work(
            {
                "id": "W1",
                "title": "New title",
                "publication_year": 2024,
                "doi": "https://doi.org/10.1000/new",
                "abstract": "Recovered abstract",
                "authorships": [{"author": {"display_name": "New Author"}}],
            }
        )
        self.library.upsert_work(
            {
                "id": "W1",
                "title": "",
                "publication_year": None,
                "doi": "",
                "abstract": "Lower-priority replacement",
                "authorships": [],
            }
        )

        row = self.library.conn.execute(
            "SELECT title, publication_year, doi, abstract, authors_json, "
            "fulltext, vec FROM works WHERE id = 'W1'"
        ).fetchone()
        self.assertEqual(row[0], "New title")
        self.assertEqual(row[1], 2024)
        self.assertEqual(row[2], "10.1000/new")
        self.assertEqual(row[3], "Recovered abstract")
        self.assertEqual(
            json.loads(row[4])[0]["author"]["display_name"],
            "New Author",
        )
        self.assertEqual(row[5], "Locally extracted full text")
        self.assertIsNotNone(row[6])

    def test_delete_works_reports_actual_rows_deleted(self):
        for index in range(1, 6):
            self.library.upsert_work({"id": f"W{index}", "title": str(index)})

        deleted = self.library.delete_works(["W1", "W2", "W2", "W404"])

        self.assertEqual(deleted, 2)
        self.assertEqual(self.library.get_work_count(), 3)


if __name__ == "__main__":
    unittest.main()
