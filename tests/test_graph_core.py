from __future__ import annotations

import unittest

from openalex_mcp.graph.core import build_citation_graph


class _Library:
    def __init__(self):
        self.sql = ""

    def _fetch_dicts(self, sql, _params):
        self.sql = sql
        return [
            {
                "id": "W1",
                "title": "Example",
                "publication_year": 2024,
                "cited_by_count": 12,
                "source_name": "Journal",
                "abstract": "  Abstract text.  ",
                "referenced_works": "[]",
                "authors_json": "[]",
            }
        ]


class GraphCoreTests(unittest.TestCase):
    def test_build_citation_graph_includes_abstract(self):
        library = _Library()

        graph, missing = build_citation_graph(library, ["W1"])

        self.assertEqual(missing, [])
        self.assertIn("abstract", library.sql)
        self.assertEqual(graph.nodes["W1"]["abstract"], "Abstract text.")


if __name__ == "__main__":
    unittest.main()
