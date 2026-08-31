"""Citation network analysis — PageRank, degree, betweenness, communities.

All functions are pure: they take a networkx.DiGraph (see core.py) and return
human-readable Markdown.  ``detect_communities`` is separated out so the
community→color/shape mapping can be shared with visualize.py.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from .core import format_authors

EXACT_BETWEENNESS_NODE_LIMIT = 500
PURE_PYTHON_PAGERANK_NODE_LIMIT = 500
APPROX_BETWEENNESS_SAMPLES = 200
GREEDY_COMMUNITY_NODE_LIMIT = 500
GREEDY_COMMUNITY_EDGE_LIMIT = 10_000
CENTRALITY_RANDOM_SEED = 42
COMMUNITY_RANDOM_SEED = 42


def _pagerank(graph: nx.DiGraph) -> dict[str, float]:
    """Compute PageRank, avoiding SciPy lazy-import overhead for small graphs."""
    if graph.number_of_nodes() <= PURE_PYTHON_PAGERANK_NODE_LIMIT:
        from networkx.algorithms.link_analysis.pagerank_alg import (
            _pagerank_python,
        )

        return _pagerank_python(graph)

    try:
        return nx.pagerank(graph)
    except ModuleNotFoundError as exc:
        if exc.name != "scipy":
            raise
        from networkx.algorithms.link_analysis.pagerank_alg import (
            _pagerank_python,
        )

        return _pagerank_python(graph)


def _use_louvain_communities(graph: nx.Graph) -> bool:
    return (
        graph.number_of_nodes() > GREEDY_COMMUNITY_NODE_LIMIT
        or graph.number_of_edges() > GREEDY_COMMUNITY_EDGE_LIMIT
    )


def _community_method_note(graph: nx.Graph) -> str | None:
    if not _use_louvain_communities(graph):
        return None
    return (
        "大图社区划分使用 Louvain 启发式算法,避免 greedy modularity 在大图上"
        "拖慢或触发客户端超时。"
    )


def detect_communities(graph: nx.DiGraph) -> dict[str, int]:
    """Map each node ID to a community number (0 = largest community).

    Uses greedy modularity for small graphs and Louvain for larger graphs.
    Degenerates to a single community 0 when community detection fails.
    """
    communities: dict[str, int] = {}
    undirected = graph.to_undirected()
    if undirected.number_of_nodes() == 0:
        return communities

    try:
        if _use_louvain_communities(undirected):
            groups_iter = nx.community.louvain_communities(
                undirected,
                seed=COMMUNITY_RANDOM_SEED,
            )
        else:
            groups_iter = nx.community.greedy_modularity_communities(undirected)
        groups = sorted(groups_iter, key=len, reverse=True)
    except Exception:
        groups = [set(undirected.nodes())]

    for num, group in enumerate(groups):
        for node in group:
            communities[node] = num
    return communities


def _top(
    ranked: list[tuple[str, float]],
    graph: nx.DiGraph,
    n: int = 10,
) -> list[dict[str, Any]]:
    """Format top-ranked nodes with their titles/years for display."""
    ranked = list(ranked)
    out: list[dict[str, Any]] = []
    for node, score in ranked[:n]:
        attrs = graph.nodes[node]
        out.append(
            {
                "id": node,
                "title": attrs.get("title", ""),
                "year": attrs.get("year"),
                "authors": format_authors(attrs.get("authors", [])),
                "cited_by_count": attrs.get("cited_by_count", 0),
                "score": round(float(score), 5),
            }
        )
    return out


def _render_ranked(title: str, items: list[dict[str, Any]]) -> list[str]:
    """Render a ranked section as Markdown lines."""
    lines = [f"### {title}", ""]
    if not items:
        lines.append("(无足够数据)")
        lines.append("")
        return lines
    lines.append("| # | ID | title | year | authors | cited | score |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, it in enumerate(items, 1):
        t = it["title"]
        if len(t) > 40:
            t = t[:37] + "..."
        lines.append(
            f"| {i} | {it['id']} | {t} | {it['year'] or '-'} | "
            f"{it['authors'] or '-'} | {it['cited_by_count']} | {it['score']} |"
        )
    lines.append("")
    return lines


def _betweenness_centrality(
    graph: nx.DiGraph,
) -> tuple[dict[str, float], str | None]:
    """Compute betweenness exactly for small graphs, approximately for large."""
    n_nodes = graph.number_of_nodes()
    if n_nodes <= EXACT_BETWEENNESS_NODE_LIMIT:
        return nx.betweenness_centrality(graph), None

    samples = min(APPROX_BETWEENNESS_SAMPLES, n_nodes)
    scores = nx.betweenness_centrality(
        graph,
        k=samples,
        seed=CENTRALITY_RANDOM_SEED,
    )
    note = (
        f"大图中介中心度使用确定性抽样近似(k={samples}/{n_nodes}),"
        "避免精确 Brandes 算法在大图上拖慢或触发客户端超时。"
    )
    return scores, note


def analyze_citation_graph(graph: nx.DiGraph, communities: dict[str, int]) -> str:
    """Produce a Markdown analysis of the citation graph."""
    n_nodes = graph.number_of_nodes()
    n_edges = graph.number_of_edges()
    if n_nodes == 0:
        return "图为空:请求的 work_ids 中没有找到任何库内论文。"

    undirected = graph.to_undirected()
    components = list(nx.weakly_connected_components(graph))
    isolates = list(nx.isolates(undirected))

    lines: list[str] = []
    lines.append("## 引文网络分析")
    lines.append("")
    lines.append(
        f"节点(论文): **{n_nodes}** ｜ 有向边(引用): **{n_edges}** ｜ "
        f"弱连通分量: **{len(components)}** ｜ 孤立节点: **{len(isolates)}**"
    )
    lines.append("")
    lines.append("> 边方向:A → B 表示 A 引用了 B。PageRank 越高 = 被引越多、越核心。")
    lines.append("")

    # --- PageRank ---
    pr = _pagerank(graph)
    lines.extend(_render_ranked("PageRank Top 10(核心论文)", _top(pr.items(), graph)))

    # --- In-degree / out-degree ---
    lines.extend(
        _render_ranked(
            "被引最多 Top 10(入度 = 被引前驱数)",
            _top(sorted(graph.in_degree(), key=lambda kv: kv[1], reverse=True), graph),
        )
    )
    lines.extend(
        _render_ranked(
            "引用最多 Top 10(出度 = 引用的其他论文数)",
            _top(sorted(graph.out_degree(), key=lambda kv: kv[1], reverse=True), graph),
        )
    )

    # --- Betweenness ---
    if n_nodes > 2:
        bc, bc_note = _betweenness_centrality(graph)
        if bc_note:
            lines.append(f"> {bc_note}")
            lines.append("")
        lines.extend(
            _render_ranked("中介中心度 Top 10(桥接不同子领域)", _top(bc.items(), graph))
        )

    # --- Communities ---
    lines.append("### 社区划分")
    lines.append("")
    lines.append("> 社区 = 图结构上密集互引的论文簇(内部边多、社区间边少)。")
    community_note = _community_method_note(undirected)
    if community_note:
        lines.append(f"> {community_note}")
    lines.append("")
    if communities:
        by_comm: dict[int, list[tuple[str, Any]]] = {}
        for node, num in communities.items():
            by_comm.setdefault(num, []).append(
                (node, graph.nodes[node].get("year"))
            )
        for num, members in sorted(
            by_comm.items(), key=lambda kv: len(kv[1]), reverse=True
        ):
            members_sorted = sorted(members, key=lambda m: m[1] or 0)
            titles = []
            for node, year in members_sorted:
                attrs = graph.nodes[node]
                title = attrs.get("title", "")
                if len(title) > 35:
                    title = title[:32] + "..."
                authors = format_authors(attrs.get("authors", []))
                suffix = f" — {authors}" if authors else ""
                titles.append(f"`{node}` {title} ({year or '-'}){suffix}")
            lines.append(f"**社区 {num}** ｜ {len(members)} 篇:")
            lines.append("")
            for t in titles:
                lines.append(f"- {t}")
            lines.append("")
    else:
        lines.append("(无社区数据)")
        lines.append("")

    return "\n".join(lines)


def get_neighbors(
    graph: nx.DiGraph,
    work_ids: list[str],
    direction: str = "both",
) -> str:
    """Describe predecessors/successors for the given work IDs (Markdown).

    Scoped to the induced subgraph built from the requested work IDs only —
    "in" lists papers WITHIN the set that cite the target; "out" lists papers
    WITHIN the set that the target cites.  Off-set references are ignored.
    """
    direction = (direction or "both").lower()
    if direction not in ("in", "out", "both"):
        raise ValueError(
            f"direction 必须是 in / out / both,收到: {direction}"
        )

    def _shorten(t: str, n: int = 45) -> str:
        return t if len(t) <= n else t[: n - 2] + "..."

    lines: list[str] = ["## 引用邻居", ""]

    for wid in work_ids:
        if wid not in graph:
            lines.append(f"- **{wid}** — 不在库中,已忽略。")
            lines.append("")
            continue

        attrs = graph.nodes[wid]
        header_authors = format_authors(attrs.get("authors", []))
        lines.append(
            f"### {wid} — {_shorten(attrs.get('title', ''))} "
            f"({attrs.get('year') or '-'}, 被引 {attrs.get('cited_by_count', 0)})"
            + (f" ｜ {header_authors}" if header_authors else "")
        )
        lines.append("")

        if direction in ("in", "both"):
            preds = list(graph.predecessors(wid))
            lines.append(f"**被引用(前驱,{len(preds)} 篇):**")
            if preds:
                for p in preds:
                    pa = graph.nodes[p]
                    pa_authors = format_authors(pa.get("authors", []))
                    lines.append(
                        f"- `{p}` {_shorten(pa.get('title', ''))} "
                        f"({pa.get('year') or '-'})"
                        + (f" — {pa_authors}" if pa_authors else "")
                    )
            else:
                lines.append("- (这批论文中没有引用它的)")
            lines.append("")

        if direction in ("out", "both"):
            succs = list(graph.successors(wid))
            lines.append(f"**引用(后继,{len(succs)} 篇):**")
            if succs:
                for s in succs:
                    sa = graph.nodes[s]
                    sa_authors = format_authors(sa.get("authors", []))
                    lines.append(
                        f"- `{s}` {_shorten(sa.get('title', ''))} "
                        f"({sa.get('year') or '-'})"
                        + (f" — {sa_authors}" if sa_authors else "")
                    )
            else:
                lines.append("- (这批论文中没有它引用的)")
            lines.append("")

    return "\n".join(lines)
