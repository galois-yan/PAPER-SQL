from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from openalex_mcp.remote.backfill import _resolve_one, backfill_missing_abstracts


class BackfillTests(unittest.TestCase):
    def test_missing_abstract_work_is_filled_without_dropping_it(self):
        works = [
            {
                "id": "W1",
                "doi": "https://doi.org/10.1000/example",
                "title": "Important paper",
                "abstract": "",
            }
        ]

        async def fake_resolve(_client, _doi, _elsevier):
            return "Recovered abstract.", "elsevier"

        with patch.dict(os.environ, {"BACKFILL_ABSTRACTS": "true"}, clear=False):
            with patch(
                "openalex_mcp.remote.backfill._resolve_one",
                new=fake_resolve,
            ):
                stats = asyncio.run(backfill_missing_abstracts(works))

        self.assertEqual(works[0]["abstract"], "Recovered abstract.")
        self.assertEqual(stats["filled"], 1)
        self.assertEqual(stats["sources"]["elsevier"], 1)

    def test_scopus_is_used_after_empty_elsevier_abstract(self):
        class Elsevier:
            async def fetch_abstract_by_doi(self, _doi):
                return {"abstract": ""}

            async def fetch_abstract_via_scopus(self, _doi):
                return {"abstract": "Recovered from Scopus."}

        async def empty_crossref(_client, _doi):
            return ""

        with patch(
            "openalex_mcp.remote.backfill._fetch_crossref",
            new=empty_crossref,
        ):
            abstract, source = asyncio.run(
                _resolve_one(object(), "10.1000/example", Elsevier())
            )

        self.assertEqual(abstract, "Recovered from Scopus.")
        self.assertEqual(source, "scopus")

    def test_missing_abstract_without_doi_is_retained(self):
        works = [{"id": "W2", "title": "No DOI", "abstract": ""}]

        with patch.dict(os.environ, {"BACKFILL_ABSTRACTS": "true"}, clear=False):
            stats = asyncio.run(backfill_missing_abstracts(works))

        self.assertEqual(len(works), 1)
        self.assertEqual(stats["no_doi"], 1)
        self.assertEqual(stats["failed"], 0)

    def test_large_batch_prioritizes_top_cited_elsevier_journal_work(self):
        works = [
            {
                "id": "W1",
                "doi": "10.1000/lower",
                "title": "Lower-cited Elsevier paper",
                "abstract": "",
                "type": "article",
                "publication_year": 2021,
                "cited_by_count": 7,
                "primary_location": {
                    "source": {
                        "type": "journal",
                        "host_organization_name": "Elsevier BV",
                    }
                },
            },
            {
                "id": "W2",
                "doi": "10.1000/higher",
                "title": "Higher-cited Elsevier paper",
                "abstract": "",
                "type": "article",
                "publication_year": 2022,
                "cited_by_count": 12,
                "primary_location": {
                    "source": {
                        "type": "journal",
                        "host_organization_name": "Elsevier BV",
                    }
                },
            },
            {
                "id": "W3",
                "doi": "10.1000/other",
                "title": "Higher-cited non-Elsevier paper",
                "abstract": "",
                "type": "article",
                "publication_year": 2023,
                "cited_by_count": 1000,
                "primary_location": {
                    "source": {
                        "type": "journal",
                        "host_organization_name": "Other Publisher",
                    }
                },
            },
        ]

        async def fake_resolve(_client, doi, _elsevier):
            return f"Recovered {doi}.", "elsevier"

        with patch.dict(
            os.environ,
            {"BACKFILL_ABSTRACTS": "true", "BACKFILL_MAX_TARGETS": "1"},
            clear=False,
        ):
            with patch(
                "openalex_mcp.remote.backfill._resolve_one",
                new=fake_resolve,
            ):
                stats = asyncio.run(backfill_missing_abstracts(works))

        self.assertEqual(works[0]["abstract"], "")
        self.assertEqual(works[1]["abstract"], "Recovered 10.1000/higher.")
        self.assertEqual(works[2]["abstract"], "")
        self.assertTrue(stats["limited"])
        self.assertFalse(stats["skipped"])
        self.assertEqual(stats["targets"], 3)
        self.assertEqual(stats["selected"], 1)
        self.assertEqual(stats["priority_candidates"], 2)
        self.assertEqual(stats["priority_selected"], 1)
        self.assertEqual(stats["filled"], 1)

    def test_large_batch_without_priority_still_skips(self):
        works = [
            {
                "id": "W1",
                "doi": "10.1000/ordinary1",
                "title": "Ordinary paper 1",
                "abstract": "",
                "type": "article",
                "publication_year": 2024,
                "cited_by_count": 5,
            },
            {
                "id": "W2",
                "doi": "10.1000/ordinary2",
                "title": "Ordinary paper 2",
                "abstract": "",
                "type": "article",
                "publication_year": 2024,
                "cited_by_count": 6,
            },
        ]

        with patch.dict(
            os.environ,
            {"BACKFILL_ABSTRACTS": "true", "BACKFILL_MAX_TARGETS": "1"},
            clear=False,
        ):
            stats = asyncio.run(backfill_missing_abstracts(works))

        self.assertTrue(stats["limited"])
        self.assertTrue(stats["skipped"])
        self.assertEqual(stats["attempted"], 0)
        self.assertIn("no top-cited Elsevier", stats["reason"])


if __name__ == "__main__":
    unittest.main()
