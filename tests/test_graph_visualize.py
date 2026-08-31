from __future__ import annotations

import math
import unittest

import networkx as nx

from openalex_mcp.graph.visualize import (
    _compute_layout,
    _render_html,
    _styled_edges,
    size_by_citations,
)


class GraphVisualizeTests(unittest.TestCase):
    def test_citation_sizes_are_monotonic_and_bounded(self):
        graph = nx.DiGraph()
        for index, citations in enumerate([0, 1, 10, 100, 10_000]):
            graph.add_node(f"W{index}", cited_by_count=citations)

        sizes = size_by_citations(graph)

        ordered = [sizes[f"W{index}"] for index in range(5)]
        self.assertEqual(ordered, sorted(ordered))
        self.assertGreaterEqual(min(ordered), 13)
        self.assertLessEqual(max(ordered), 34)
        self.assertGreaterEqual(sizes["W3"], 26)

    def test_equal_citation_counts_use_balanced_default_size(self):
        graph = nx.DiGraph()
        for index in range(4):
            graph.add_node(f"W{index}", cited_by_count=8)

        sizes = size_by_citations(graph)

        self.assertEqual(set(sizes.values()), {23})

    def test_dense_local_edges_render_darker_than_sparse_edges(self):
        graph = nx.DiGraph()
        for node in ["A", "B", "C", "D", "E", "F"]:
            graph.add_node(node, cited_by_count=1)
        graph.add_edges_from(
            [
                ("A", "B"),
                ("A", "C"),
                ("B", "C"),
                ("C", "A"),
                ("D", "E"),
            ]
        )

        edges = _styled_edges(graph, {"A": 0, "B": 0, "C": 0, "D": 1, "E": 1})
        by_pair = {(edge["from"], edge["to"]): edge for edge in edges}

        self.assertGreater(by_pair[("A", "B")]["width"], by_pair[("D", "E")]["width"])
        self.assertIn("rgba(68,82,105,", by_pair[("A", "B")]["color"]["color"])

    def test_disconnected_nodes_stay_on_compact_periphery(self):
        graph = nx.DiGraph()
        graph.add_edges_from([("A", "B"), ("B", "C"), ("C", "A")])
        graph.add_node("I1")
        graph.add_node("I2")

        layout = _compute_layout(graph)
        factor = 90.0 * math.sqrt(graph.number_of_nodes())

        for node in ["I1", "I2"]:
            radius = math.hypot(layout[node][0], layout[node][1])
            self.assertGreaterEqual(radius, 0.55 * factor)
            self.assertLessEqual(radius, 0.98 * factor)

    def test_html_has_side_panels_without_top_bar(self):
        graph = nx.DiGraph()
        graph.add_node(
            "W1",
            title="Example Paper",
            year=2024,
            authors=[],
            abstract="Short abstract.",
            cited_by_count=12,
            source_name="Example Journal",
        )

        html = _render_html(graph, {"W1": "#7b8ea8"}, {"W1": 23}, {"W1": 0})

        self.assertIn('class="side-panel left-panel"', html)
        self.assertIn('class="side-panel right-panel"', html)
        self.assertIn('"paperAbstract": "Short abstract."', html)
        self.assertIn('id="detailAbstract"', html)
        self.assertIn("updateDetail(paperIndex[node.id]);", html)
        self.assertNotIn('class="topbar"', html)
        self.assertNotIn("paperSearch", html)


if __name__ == "__main__":
    unittest.main()
