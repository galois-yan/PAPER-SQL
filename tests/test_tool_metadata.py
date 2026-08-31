from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastmcp import FastMCP

import openalex_mcp.local as local_state
from openalex_mcp.local import LibraryManager, set_library
from openalex_mcp.registry import register_all


EXPECTED_TOOL_PROPERTIES = {
    "search_keyword": {
        "query", "publication_year", "cited_by_count", "cites", "cited_by",
        "related_to", "source_id", "institution_id", "author_id", "publisher_id",
        "funder_id", "page", "fetch_fulltext", "fulltext_limit",
        "use_openalex_content_api",
    },
    "search_semantic": {
        "query", "publication_year", "cited_by_count", "cites", "cited_by",
        "related_to", "source_id", "institution_id", "author_id", "publisher_id",
        "funder_id", "page", "fetch_fulltext", "fulltext_limit",
        "use_openalex_content_api",
    },
    "search_ids": {
        "query", "fetch_fulltext", "fulltext_limit", "use_openalex_content_api",
    },
    "autocomplete": {"entity_type", "query"},
    "download_pdf": {"work_ids", "save_to_project"},
    "fetch_elsevier_abstracts": {
        "query", "input_type", "update_library", "overwrite",
    },
    "library_generate_embeddings": set(),
    "library_export": {"work_ids", "target", "sort", "cite_key_style"},
    "literature_review_prompt": set(),
    "library_stats": set(),
    "library_delete": {"work_ids"},
    "library_close": set(),
    "graph_analyze": {"work_ids"},
    "graph_neighbors": {"work_ids", "direction"},
    "graph_visualize": {"work_ids", "output_dir"},
    "library_query": {"sql", "semantic_query"},
}

EXPECTED_REQUIRED = {
    "search_semantic": {"query"},
    "search_ids": {"query"},
    "autocomplete": {"entity_type", "query"},
    "download_pdf": {"work_ids"},
    "fetch_elsevier_abstracts": {"query"},
    "library_delete": {"work_ids"},
    "graph_analyze": {"work_ids"},
    "graph_neighbors": {"work_ids"},
    "graph_visualize": {"work_ids"},
    "library_query": {"sql"},
}


class ToolMetadataTests(unittest.TestCase):
    def test_tool_names_and_parameter_shapes_remain_compatible(self):
        server = FastMCP("metadata-test")
        register_all(server)

        tools = asyncio.run(server.list_tools())
        by_name = {tool.name: tool for tool in tools}

        self.assertEqual(set(by_name), set(EXPECTED_TOOL_PROPERTIES))
        self.assertEqual(len(tools), 16)
        for name, expected_properties in EXPECTED_TOOL_PROPERTIES.items():
            with self.subTest(tool=name):
                schema = by_name[name].parameters
                self.assertEqual(set(schema.get("properties", {})), expected_properties)
                self.assertEqual(
                    set(schema.get("required", [])),
                    EXPECTED_REQUIRED.get(name, set()),
                )

        self.assertEqual(
            by_name["search_keyword"].parameters["properties"]["publication_year"]["default"],
            ">2021",
        )
        self.assertEqual(
            by_name["library_export"].parameters["properties"]["target"]["default"],
            "export.bib",
        )
        self.assertFalse(
            by_name["download_pdf"].parameters["properties"]["save_to_project"]["default"]
        )
        pdf_description = by_name["download_pdf"].description or ""
        self.assertIn("STRICT RULE", pdf_description)
        self.assertIn("explicitly asks", pdf_description)
        self.assertEqual(
            by_name["graph_neighbors"].parameters["properties"]["direction"]["default"],
            "both",
        )

    def test_literature_review_prompt_contains_required_review_rules(self):
        server = FastMCP("review-prompt-test")
        register_all(server)

        result = asyncio.run(server.call_tool("literature_review_prompt", {}))
        prompt = result.content[0].text

        self.assertIn(r"\cite{key}", prompt)
        self.assertIn("从简单到复杂", prompt)
        self.assertIn("BibTeX", prompt)

    def test_library_query_tool_uses_database_readonly_connection(self):
        temp_dir = tempfile.TemporaryDirectory()
        library = LibraryManager(Path(temp_dir.name) / "library.db")
        previous_library = local_state._library
        try:
            library.upsert_work({"id": "W1", "title": "Safe"})
            set_library(library)
            server = FastMCP("query-integration-test")
            register_all(server)

            async def call_queries():
                selected = await server.call_tool(
                    "library_query",
                    {"sql": "SELECT id, title FROM works"},
                )
                blocked = await server.call_tool(
                    "library_query",
                    {"sql": "WITH selected AS (SELECT 1) DELETE FROM works"},
                )
                return selected, blocked

            selected, blocked = asyncio.run(call_queries())

            self.assertEqual(selected.content[0].text, '[{"id": "W1", "title": "Safe"}]')
            self.assertIn('"error":', blocked.content[0].text)
            self.assertEqual(library.get_work_count(), 1)
        finally:
            library.close()
            local_state._library = previous_library
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
