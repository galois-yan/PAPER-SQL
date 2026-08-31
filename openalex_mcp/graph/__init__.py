"""Citation network analysis — public API layer.

Import from this module (not its submodules) to access graph tools.
All functions are pure: they take a ``library`` (LocalManager) and return
human-readable strings.  Callers obtain the library via
``openalex_mcp.local.get_library()`` (the registry does this).
"""

from __future__ import annotations

import logging
import math

import networkx as nx

from .core import (
    GraphError,
    build_citation_graph,
    parse_work_ids,
)
from .metrics import (
    analyze_citation_graph,
    detect_communities,
    get_neighbors,
)
from .visualize import visualize_citation_graph

__all__ = [
    "GraphError",
    "graph_analyze",
    "graph_neighbors",
    "graph_visualize",
]

logger = logging.getLogger(__name__)

# Keep the interactive view readable; graph_analyze remains uncapped.
_MAX_VISUALIZE_NODES = 120


def _normalize_scores(values: dict[str, float]) -> dict[str, float]:
    """Normalize a score mapping to [0, 1], handling ties safely."""
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {key: 1.0 for key in values}
    return {key: (value - lo) / (hi - lo) for key, value in values.items()}


def _select_important_nodes(graph, limit: int) -> tuple[set[str], dict[str, float]]:
    """Select influential/foundational-looking nodes for the visual subset.

    This is a transparent ranking proxy, not a claim of peer-review quality:
    global citations carry the most weight, followed by graph centrality,
    within-selection citations, and older publication year.
    """
    if graph.number_of_nodes() <= limit:
        return set(graph.nodes), {node: 1.0 for node in graph.nodes}

    citation = {
        node: math.log1p(max(0, graph.nodes[node].get("cited_by_count", 0)))
        for node in graph.nodes
    }
    incoming = {
        node: math.log1p(max(0, graph.in_degree(node))) for node in graph.nodes
    }
    try:
        pagerank = nx.pagerank(graph, alpha=0.85, max_iter=100)
    except nx.PowerIterationFailedConvergence:
        logger.warning("PageRank did not converge; using degree proxy for truncation")
        pagerank = {node: float(graph.in_degree(node)) for node in graph.nodes}

    years = {
        node: graph.nodes[node].get("year")
        for node in graph.nodes
        if isinstance(graph.nodes[node].get("year"), int)
    }
    newest = max(years.values()) if years else 0
    oldest = min(years.values()) if years else 0
    age = {
        node: float(newest - graph.nodes[node].get("year", newest))
        if isinstance(graph.nodes[node].get("year"), int)
        else 0.0
        for node in graph.nodes
    }
    scores = {}
    citation_n = _normalize_scores(citation)
    pagerank_n = _normalize_scores(pagerank)
    incoming_n = _normalize_scores(incoming)
    age_n = _normalize_scores(age) if newest != oldest else {n: 0.5 for n in graph.nodes}
    for node in graph.nodes:
        scores[node] = (
            0.50 * citation_n[node]
            + 0.25 * pagerank_n[node]
            + 0.15 * incoming_n[node]
            + 0.10 * age_n[node]
        )

    ranked = sorted(
        graph.nodes,
        key=lambda node: (scores[node], citation[node], str(node)),
        reverse=True,
    )
    return set(ranked[:limit]), scores


def graph_analyze(library, work_ids: str) -> str:
    """Analyze the citation network induced by ``work_ids`` (Markdown).

    Args:
        library: LibraryManager instance.
        work_ids: Comma-separated work IDs (required).

    Returns:
        Markdown: PageRank, degree, betweenness, communities.
    """
    try:
        ids = parse_work_ids(work_ids)
    except GraphError as e:
        return str(e)

    graph, missing = build_citation_graph(library, ids)
    communities = detect_communities(graph)
    text = analyze_citation_graph(graph, communities)

    if missing:
        text += (
            "\n\n> 以下 ID 不在库中,已忽略:"
            + ", ".join(f"`{m}`" for m in missing[:20])
            + (" ..." if len(missing) > 20 else "")
        )
    return text


def graph_neighbors(library, work_ids: str, direction: str = "both") -> str:
    """Show which works cite (predecessors) / are cited by (successors).

    Args:
        library: LibraryManager instance.
        work_ids: Comma-separated work IDs (required).
        direction: One of ``in`` (谁引用了它), ``out`` (它引用了谁),
            ``both`` (默认,两者都列出).

    Returns:
        Markdown listing neighbors for each requested work.
    """
    try:
        ids = parse_work_ids(work_ids)
    except GraphError as e:
        return str(e)

    graph, _missing = build_citation_graph(library, ids)
    try:
        return get_neighbors(graph, ids, direction)
    except ValueError as e:
        return str(e)


def graph_visualize(
    library,
    work_ids: str,
    output_dir: str | None = None,
) -> str:
    """Generate an interactive HTML citation graph (vis.js).

    Args:
        library: LibraryManager instance.
        work_ids: Comma-separated work IDs (required).
        output_dir: Optional output directory.  Defaults to
            ``~/.AI-CACHE/openalex/graphs/``.

    Returns:
        Absolute path to the generated HTML file.
    """
    try:
        ids = parse_work_ids(work_ids)
    except GraphError as e:
        return str(e)

    graph, missing = build_citation_graph(library, ids)
    if graph.number_of_nodes() == 0:
        return "没有找到任何库内论文,无法生成可视化。"

    original_node_count = graph.number_of_nodes()
    selected_nodes, _importance_scores = _select_important_nodes(
        graph, _MAX_VISUALIZE_NODES
    )
    truncated = original_node_count > len(selected_nodes)
    if truncated:
        graph = graph.subgraph(selected_nodes).copy()

    communities = detect_communities(graph)
    path = visualize_citation_graph(graph, communities, output_dir)

    msg = (
        f"已生成交互式引文网络图: `{path}`\n\n"
        "在浏览器中打开即可查看。视觉编码:\n"
        "- **颜色** = 发表年份(浅色较早,深色较新)\n"
        "- **标签** = 重要节点显示第一作者与年份;其余悬停显示\n"
        "- **大小** = 被引量(稳健对数缩放,压缩极端高被引节点)\n"
        "- **线深** = 局部引用结构强度(共同邻居、同社区、端点连接度)\n"
        "- 加载时短暂力导向聚拢,稳定后自动冻结;点击节点琥珀色高亮\n"
        "- 悬停节点同步右侧详情栏,可看标题/作者/摘要/年份/期刊/社区/ID"
    )
    if truncated:
        msg += (
            "\n\n> 原始论文 "
            f"{original_node_count} 篇,已按重要性保留 {graph.number_of_nodes()} 篇,"
            f"隐藏 {original_node_count - graph.number_of_nodes()} 篇。"
            "排序依据: 被引量 50% + PageRank 25% + 集合内被引用次数 15% + 奠基性年份 10%。"
        )
    if missing:
        msg += (
            "\n\n> 以下 ID 不在库中,已忽略:"
            + ", ".join(f"`{m}`" for m in missing[:20])
            + (" ..." if len(missing) > 20 else "")
        )
    return msg
