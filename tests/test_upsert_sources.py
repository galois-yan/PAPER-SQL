from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openalex_mcp.local.manager import LibraryManager
from openalex_mcp.local.upsert import upsert_works
from openalex_mcp.remote import set_client, set_embed


class _BatchSourceClient:
    def __init__(self):
        self.batch_calls: list[list[str]] = []
        self.single_calls: list[str] = []

    async def get_sources_batch(self, source_ids: list[str]):
        self.batch_calls.append(list(source_ids))
        return [
            {
                "id": source_id,
                "display_name": f"Source {source_id}",
            }
            for source_id in source_ids
        ]

    async def get_source(self, source_id: str):
        self.single_calls.append(source_id)
        return {
            "id": source_id,
            "display_name": f"Source {source_id}",
        }


def _work(work_id: str, source_id: str) -> dict:
    return {
        "id": work_id,
        "title": work_id,
        "primary_location": {
            "source": {
                "id": f"https://openalex.org/{source_id}",
                "display_name": f"Inline {source_id}",
            }
        },
    }


class UpsertSourceFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.library = LibraryManager(Path(self.temp_dir.name) / "library.db")
        set_embed(None)

    def tearDown(self):
        set_client(None)
        set_embed(None)
        self.library.close()
        self.temp_dir.cleanup()

    async def test_upsert_fetches_missing_sources_in_one_batch(self):
        self.library.upsert_source({"id": "S1", "display_name": "Existing S1"})
        client = _BatchSourceClient()
        set_client(client)

        added, failed, total = await upsert_works(
            self.library,
            [
                _work("W1", "S1"),
                _work("W2", "S2"),
                _work("W3", "S3"),
                _work("W4", "S2"),
            ],
        )

        self.assertEqual((added, failed, total), (4, 0, 4))
        self.assertEqual(client.batch_calls, [["S2", "S3"]])
        self.assertEqual(client.single_calls, [])
        self.assertEqual(
            self.library.get_existing_source_ids(["S1", "S2", "S3", "S4"]),
            {"S1", "S2", "S3"},
        )


if __name__ == "__main__":
    unittest.main()
