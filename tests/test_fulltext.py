from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from openalex_mcp.remote.fulltext import fetch_and_store_fulltext


class _Library:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.texts = {}

    def _get_lock(self):
        return self.lock

    def update_work_fulltext(self, work_id, text):
        self.texts[work_id] = text
        return True


class _Client:
    api_key = ""


class FulltextTests(unittest.TestCase):
    def test_extracts_selected_pdf_text_into_database(self):
        library = _Library()
        works = [
            {
                "id": "https://openalex.org/W1",
                "title": "Detailed paper",
                "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
            }
        ]

        async def fake_fetch(_http, source, _url, _params, **_kwargs):
            return "Methods: the cells were incubated for 24 hours.", source, 123

        with patch(
            "openalex_mcp.remote.fulltext._fetch_candidate_text",
            new=fake_fetch,
        ):
            report = asyncio.run(
                fetch_and_store_fulltext(
                    _Client(), library, works, limit=1
                )
            )

        self.assertEqual(report["stored"], 1)
        self.assertEqual(
            library.texts["W1"],
            "Methods: the cells were incubated for 24 hours.",
        )

    def test_content_api_is_not_used_without_explicit_flag(self):
        works = [
            {
                "id": "W1",
                "has_content": {"pdf": True},
            }
        ]
        library = _Library()
        report = asyncio.run(
            fetch_and_store_fulltext(_Client(), library, works, limit=1)
        )

        self.assertEqual(report["requested_count"], 0)
        self.assertEqual(report["stored"], 0)


if __name__ == "__main__":
    unittest.main()
