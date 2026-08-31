from __future__ import annotations

import unittest
from unittest.mock import patch

import networkx as nx

from openalex_mcp.graph.metrics import (
    _pagerank,
    analyze_citation_graph,
    detect_communities,
)


def _citation_chain(size: int) -> nx.DiGraph:
    graph = nx.DiGraph()
    for index in range(size):
        graph.add_node(
            f"W{index}",
            title=f"Work {index}",
            year=2020,
            authors=[],
            cited_by_count=index,
        )
    for index in range(1, size):
        graph.add_edge(f"W{index}", f"W{index - 1}")
    return graph


class GraphMetricTests(unittest.TestCase):
    def test_small_graph_does_not_report_large_graph_approximations(self):
        graph = _citation_chain(5)
        communities = detect_communities(graph)

        text = analyze_citation_graph(graph, communities)

        self.assertNotIn("确定性抽样近似", text)
        self.assertNotIn("Louvain 启发式算法", text)

    def test_small_graph_pagerank_uses_python_backend(self):
        graph = _citation_chain(5)

        with patch(
            "openalex_mcp.graph.metrics.nx.pagerank",
            side_effect=AssertionError("nx.pagerank should not run for small graphs"),
        ):
            scores = _pagerank(graph)

        self.assertEqual(set(scores), set(graph.nodes))

    def test_large_graph_reports_adaptive_algorithms(self):
        graph = _citation_chain(501)
        communities = detect_communities(graph)

        text = analyze_citation_graph(graph, communities)

        self.assertEqual(set(communities), set(graph.nodes))
        self.assertIn("节点(论文): **501**", text)
        self.assertIn("确定性抽样近似(k=200/501)", text)
        self.assertIn("Louvain 启发式算法", text)


if __name__ == "__main__":
    unittest.main()
