from __future__ import annotations

import json
import unittest

from openalex_mcp.local.export import export_library_bibtex


class _Result:
    def fetchone(self):
        return None


class _Connection:
    def execute(self, *_args):
        return _Result()


class _Library:
    conn = _Connection()

    def __init__(self, rows):
        self.rows = rows

    def _fetch_dicts(self, _sql, _params):
        return self.rows

    def get_source_name(self, _source_id):
        return "Journal of Tests"


def _row(work_id: str, author: str, year: int, doi: str = "10.1000/test"):
    return {
        "id": work_id,
        "type": "journal-article",
        "title": "A test paper",
        "authors_json": json.dumps([{"author": {"display_name": author}}]),
        "publication_year": year,
        "source_id": "S1",
        "doi": doi,
        "oa_url": "https://example.org/paper",
        "concepts_json": json.dumps([{"display_name": "Electrochemistry", "score": 0.9}]),
        "keywords_json": json.dumps([{"keyword": "battery"}]),
    }


class BibtexExportTests(unittest.TestCase):
    def test_author_year_keys_include_suffixes_and_identifiers(self):
        library = _Library(
            [
                _row("W1", "Ada Lovelace", 2024),
                _row("W2", "Ada Lovelace", 2024),
            ]
        )

        bibtex = export_library_bibtex(library)

        self.assertIn("@article{lovelace2024,", bibtex)
        self.assertIn("@article{lovelace2024a,", bibtex)
        self.assertIn("doi = {10.1000/test},", bibtex)
        self.assertIn("url = {https://example.org/paper}", bibtex)
        self.assertNotIn("note =", bibtex)

    def test_openalex_id_keys_remain_available(self):
        bibtex = export_library_bibtex(
            _Library([_row("W1", "Ada Lovelace", 2024)]),
            cite_key_style="openalex_id",
        )

        self.assertIn("@article{W1,", bibtex)

    def test_current_openalex_article_type_maps_to_article(self):
        row = _row("W1", "Ada Lovelace", 2024)
        row["type"] = "article"
        bibtex = export_library_bibtex(_Library([row]))

        self.assertIn("@article{lovelace2024,", bibtex)
        self.assertNotIn("@misc", bibtex)
        self.assertNotIn("note =", bibtex)


if __name__ == "__main__":
    unittest.main()
