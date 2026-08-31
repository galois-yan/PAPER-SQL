from __future__ import annotations

import asyncio
import unittest

from openalex_mcp.remote.client import OpenAlexClient
from openalex_mcp.remote.search import search_keyword


class _RecordingClient(OpenAlexClient):
    def __init__(self):
        self.calls: list[tuple[dict, str]] = []

    async def _request(self, params, endpoint="/works"):
        self.calls.append((dict(params), endpoint))
        return {"results": []}


class SearchIdsTests(unittest.TestCase):
    def test_normalizes_and_deduplicates_work_ids_and_dois(self):
        client = _RecordingClient()

        asyncio.run(
            client.search(
                "W123, https://openalex.org/W456, "
                "doi:10.1000/ABC, https://doi.org/10.1001/xyz, W123",
                mode="ids",
            )
        )

        self.assertEqual(len(client.calls), 1)
        params, endpoint = client.calls[0]
        self.assertEqual(endpoint, "/works")
        self.assertEqual(params["per_page"], 100)
        self.assertEqual(
            params["filter"],
            "openalex_id:W123|W456,doi:10.1000/ABC|10.1001/xyz",
        )

    def test_rejects_invalid_only_input_before_request(self):
        client = _RecordingClient()

        with self.assertRaisesRegex(ValueError, "Invalid work ID or DOI"):
            asyncio.run(client.search("not-an-id", mode="ids"))

        self.assertEqual(client.calls, [])

    def test_rejects_mixed_valid_and_invalid_input_before_request(self):
        client = _RecordingClient()

        with self.assertRaisesRegex(ValueError, "Invalid work ID or DOI"):
            asyncio.run(client.search("W123, not-an-id", mode="ids"))

        self.assertEqual(client.calls, [])


class SearchKeywordFilterTests(unittest.TestCase):
    def test_empty_publication_year_omits_year_filter(self):
        client = _RecordingClient()

        asyncio.run(
            search_keyword(
                client,
                query="battery state of charge",
                publication_year="",
            )
        )

        self.assertEqual(len(client.calls), 1)
        params, endpoint = client.calls[0]
        self.assertEqual(endpoint, "/works")
        self.assertNotIn("filter", params)

    def test_omitted_publication_year_keeps_existing_default(self):
        client = _RecordingClient()

        asyncio.run(search_keyword(client, query="battery state of charge"))

        params, endpoint = client.calls[0]
        self.assertEqual(endpoint, "/works")
        self.assertEqual(params["filter"], "publication_year:>2021")


class SourceBatchTests(unittest.TestCase):
    def test_get_sources_batch_uses_one_filtered_sources_request(self):
        client = _RecordingClient()

        sources = asyncio.run(
            client.get_sources_batch(["S1", "https://openalex.org/S2", "S1", ""])
        )

        self.assertEqual(sources, [])
        self.assertEqual(len(client.calls), 1)
        params, endpoint = client.calls[0]
        self.assertEqual(endpoint, "/sources")
        self.assertEqual(params["filter"], "openalex_id:S1|S2")
        self.assertEqual(params["per_page"], 2)
        self.assertIn("display_name", params["select"])

    def test_get_sources_batch_empty_input_skips_request(self):
        client = _RecordingClient()

        sources = asyncio.run(client.get_sources_batch([]))

        self.assertEqual(sources, [])
        self.assertEqual(client.calls, [])

if __name__ == "__main__":
    unittest.main()
