"""Interactive HTML visualization (vis.js, rendered directly — no pyvis runtime).

Visual channels:
- color = publication year (light = older, dark = newer)
- label = first-author surname + year on prominent/hovered nodes
- size  = cited_by_count (robust log-scaled)
- edge brightness = local citation-structure strength
- shape = dot
- title = hover tooltip (title, year, source, cited_by_count, community, ID)

Performance design (large graphs):
- A deterministic Python spring layout provides good starting coordinates.
- The browser runs a short visible Barnes-Hut settling animation, then freezes
  physics after stabilization (or six seconds) to avoid continuous CPU use.
- Edges stay straight and are hidden during drag/zoom for fast interaction.
- vis-network is bundled/inlined into each HTML (no network needed at open
  time); if the bundled copy is missing, it falls back to jsdelivr -> unpkg.
- Visual styling uses a custom academic map palette: year gradient, log
  citation size, sparse prominent labels, hover labels, and amber selection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path

import networkx as nx

from .core import format_authors

logger = logging.getLogger(__name__)

# Distinct categorical colors for communities (CSS hex).
COMMUNITY_COLORS = [
    "#4e79a7",  # blue
    "#f28e2b",  # orange
    "#e15759",  # red
    "#76b7b2",
    "#59a14f",  # green
    "#edc948",  # yellow
    "#b07aa1",  # purple
    "#ff9da7",  # pink
    "#9c755f",  # brown
    "#bab0ac",  # grey
]

FALLBACK_COLOR = "#999999"
NODE_SIZE_MIN = 13
NODE_SIZE_MAX = 34
NODE_SIZE_DEFAULT = 23
NODE_SIZE_EASING = 0.74
EDGE_ALPHA_MIN = 0.035
EDGE_ALPHA_MAX = 0.22
EDGE_WIDTH_MIN = 0.35
EDGE_WIDTH_MAX = 0.85
EDGE_RGB = (68, 82, 105)

# Bundled vis-network (MIT, https://visjs.org).  Inlined into every generated
# HTML so the page works offline and opens instantly in China without relying
# on slow/blocked CDNs.  CDN fallbacks remain in the template in case this
# file is missing.
_VIS_LIB_PATH = Path(__file__).with_name("vendor") / "vis-network.min.js"


def _load_vis_lib() -> str:
    """Return the bundled vis-network source, or '' if unavailable."""
    try:
        return _VIS_LIB_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("vendor vis-network.min.js not found; falling back to CDN")
        return ""


def color_by_community(communities: dict[str, int]) -> dict[str, str]:
    """Return node_id -> hex color for each community number."""
    return {
        node: COMMUNITY_COLORS[num % len(COMMUNITY_COLORS)]
        for node, num in communities.items()
    }


def _percentile(values: list[float], p: float) -> float:
    """Return a linearly interpolated percentile from a sorted value list."""
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    index = (len(values) - 1) * p
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return values[lower]
    weight = index - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


def size_by_citations(graph: nx.DiGraph) -> dict[str, int]:
    """Return node_id -> robust log-scaled citation size.

    Citation counts are heavy-tailed, so direct min-max scaling lets one very
    highly cited paper dominate the canvas.  Percentile clipping keeps the
    relative ordering readable while giving mid-citation nodes enough presence.
    """
    counts = [max(0, graph.nodes[n].get("cited_by_count", 0)) for n in graph.nodes]
    if not counts:
        return {}
    logs = sorted(math.log1p(c) for c in counts)
    raw_lo, raw_hi = logs[0], logs[-1]
    lo = _percentile(logs, 0.05)
    hi = _percentile(logs, 0.95)
    if hi <= lo:
        lo, hi = raw_lo, raw_hi
    sizes: dict[str, int] = {}
    for n in graph.nodes:
        value = math.log1p(max(0, graph.nodes[n].get("cited_by_count", 0)))
        if hi == lo:
            sizes[n] = NODE_SIZE_DEFAULT
        else:
            t = max(0.0, min(1.0, (value - lo) / (hi - lo)))
            t = t**NODE_SIZE_EASING
            sizes[n] = int(
                round(NODE_SIZE_MIN + t * (NODE_SIZE_MAX - NODE_SIZE_MIN))
            )
    return sizes


def _normalize(values: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
    """Normalize edge scores to [0, 1], using 0 for ties."""
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {key: 0.0 for key in values}
    return {key: (value - lo) / (hi - lo) for key, value in values.items()}


def _rgba(alpha: float) -> str:
    r, g, b = EDGE_RGB
    return f"rgba({r},{g},{b},{alpha:.3f})"


def _edge_strengths(
    graph: nx.DiGraph, communities: dict[str, int]
) -> dict[tuple[str, str], float]:
    """Score edges by local graph structure for layered visual contrast."""
    if graph.number_of_edges() == 0:
        return {}

    undirected = graph.to_undirected()
    neighbors = {node: set(undirected.neighbors(node)) for node in graph.nodes}
    endpoint_scores = {
        (u, v): math.log1p(
            math.sqrt(max(1, len(neighbors.get(u, set()))) * max(1, len(neighbors.get(v, set()))))
        )
        for u, v in graph.edges()
    }
    endpoint_scores = _normalize(endpoint_scores)

    strengths: dict[tuple[str, str], float] = {}
    for u, v in graph.edges():
        u_neighbors = neighbors.get(u, set()) - {v}
        v_neighbors = neighbors.get(v, set()) - {u}
        if u_neighbors and v_neighbors:
            shared = len(u_neighbors & v_neighbors) / math.sqrt(
                len(u_neighbors) * len(v_neighbors)
            )
        else:
            shared = 0.0
        same_community = (
            1.0
            if u in communities and v in communities and communities[u] == communities[v]
            else 0.0
        )
        strengths[(u, v)] = min(
            1.0,
            0.55 * shared + 0.30 * endpoint_scores[(u, v)] + 0.15 * same_community,
        )
    return strengths


def _styled_edges(
    graph: nx.DiGraph, communities: dict[str, int]
) -> list[dict[str, object]]:
    """Build vis-network edge objects with per-edge brightness."""
    strengths = _edge_strengths(graph, communities)
    edges: list[dict[str, object]] = []
    for u, v in graph.edges():
        strength = strengths.get((u, v), 0.0)
        alpha = EDGE_ALPHA_MIN + strength * (EDGE_ALPHA_MAX - EDGE_ALPHA_MIN)
        width = EDGE_WIDTH_MIN + strength * (EDGE_WIDTH_MAX - EDGE_WIDTH_MIN)
        edges.append(
            {
                "from": u,
                "to": v,
                "width": round(width, 2),
                "color": {
                    "color": _rgba(alpha),
                    "highlight": "rgba(51,67,90,0.56)",
                    "hover": "rgba(51,67,90,0.42)",
                    "inherit": False,
                },
            }
        )
    return edges


def color_by_year(graph: nx.DiGraph) -> dict[str, str]:
    """Return a light mist-blue to deep ink-blue year gradient."""
    years = [
        graph.nodes[n].get("year")
        for n in graph.nodes
        if isinstance(graph.nodes[n].get("year"), int)
    ]
    if not years:
        return {n: "#7b8ea8" for n in graph.nodes}
    lo, hi = min(years), max(years)
    old = (224, 232, 242)
    new = (34, 74, 136)
    colors: dict[str, str] = {}
    for n in graph.nodes:
        year = graph.nodes[n].get("year")
        t = 0.45 if not isinstance(year, int) or hi == lo else (year - lo) / (hi - lo)
        rgb = tuple(round(a + (b - a) * t) for a, b in zip(old, new))
        colors[n] = "#{:02x}{:02x}{:02x}".format(*rgb)
    return colors


def _display_label(graph: nx.DiGraph, node: str) -> str:
    """Compact paper-map label: first-author surname, year."""
    attrs = graph.nodes[node]
    authors = attrs.get("authors", [])
    author = authors[0].split()[-1] if authors and authors[0].strip() else ""
    year = attrs.get("year")
    if author and year:
        return f"{author}, {year}"
    return author or (str(year) if year else "")


def _prominent_nodes(graph: nx.DiGraph) -> set[str]:
    """Choose a sparse label set using local degree and citation prominence."""
    return set(_ranked_nodes(graph)[:_label_limit(graph)])


def _label_limit(graph: nx.DiGraph) -> int:
    n = graph.number_of_nodes()
    if not n:
        return 0
    return min(n, max(18, int(math.sqrt(n) * 2.5)))


def _ranked_nodes(graph: nx.DiGraph) -> list[str]:
    """Rank nodes for initial selection and side-list ordering."""
    return sorted(
        graph.nodes,
        key=lambda node: (
            graph.degree(node),
            math.log1p(max(0, graph.nodes[node].get("cited_by_count", 0))),
            graph.nodes[node].get("year") or 0,
        ),
        reverse=True,
    )


def _component_center(
    positions: dict[str, tuple[float, float]], nodes: set[str]
) -> tuple[float, float]:
    if not nodes:
        return 0.0, 0.0
    x = sum(positions[node][0] for node in nodes) / len(nodes)
    y = sum(positions[node][1] for node in nodes) / len(nodes)
    return x, y


def _arrange_disconnected_components(
    graph: nx.Graph, positions: dict[str, tuple[float, float]]
) -> dict[str, tuple[float, float]]:
    """Keep disconnected papers peripheral without letting them drift too far."""
    components = sorted(
        (set(component) for component in nx.connected_components(graph)),
        key=lambda component: (
            len(component),
            sum(graph.degree(node) for node in component),
            min(str(node) for node in component),
        ),
        reverse=True,
    )
    if len(components) <= 1:
        return positions

    adjusted = {
        node: (float(x), float(y)) for node, (x, y) in positions.items()
    }
    main = components[0]
    main_center = _component_center(adjusted, main)
    adjusted = {
        node: (x - main_center[0], y - main_center[1])
        for node, (x, y) in adjusted.items()
    }

    main_radius = max(
        (math.hypot(adjusted[node][0], adjusted[node][1]) for node in main),
        default=0.35,
    )
    ring_radius = min(0.88, max(0.58, main_radius + 0.22))
    peripheral = components[1:]
    angle_step = (2.0 * math.pi) / max(1, len(peripheral))
    for index, component in enumerate(peripheral):
        angle = -math.pi / 7.0 + index * angle_step
        target_radius = min(0.92, ring_radius + 0.04 * (index % 2))
        target = (
            math.cos(angle) * target_radius,
            math.sin(angle) * target_radius,
        )
        center = _component_center(adjusted, component)
        dx = target[0] - center[0]
        dy = target[1] - center[1]
        for node in component:
            x, y = adjusted[node]
            adjusted[node] = (x + dx, y + dy)

    return adjusted


def _compact_peripheral_nodes(
    graph: nx.Graph, positions: dict[str, tuple[float, float]]
) -> dict[str, tuple[float, float]]:
    """Cap very weakly connected nodes so they stay near, but outside, the core."""
    adjusted = dict(positions)
    max_radius = 0.96
    isolate_radius = 0.72
    for node in graph.nodes:
        x, y = adjusted[node]
        radius = math.hypot(x, y)
        if radius == 0:
            continue
        degree = graph.degree(node)
        target_radius = radius
        if degree == 0:
            target_radius = min(max_radius, max(isolate_radius, radius))
        elif degree == 1 and radius > max_radius:
            target_radius = max_radius
        if target_radius != radius:
            scale = target_radius / radius
            adjusted[node] = (x * scale, y * scale)
    return adjusted


def _compute_layout(graph: nx.DiGraph) -> dict[str, tuple[float, float]]:
    """Precompute a deterministic initial layout in pixel space.

    This gives the browser a good starting point; vis-network only performs a
    short visible settling animation before physics is frozen automatically.
    """
    n = max(1, graph.number_of_nodes())
    undirected = graph.to_undirected()
    try:
        pos = nx.spring_layout(
            undirected,
            seed=42,
            k=2.0 / math.sqrt(n),
            iterations=200,
            scale=1.0,
        )
    except Exception:
        logger.exception("spring_layout failed; falling back to circular layout")
        pos = nx.circular_layout(undirected)
    pos = {
        node: (float(coords[0]), float(coords[1]))
        for node, coords in pos.items()
    }
    pos = _arrange_disconnected_components(undirected, pos)
    pos = _compact_peripheral_nodes(undirected, pos)
    # Unit-space -> pixel space.  Target ~180px between adjacent nodes;
    # the spread grows with sqrt(n) so density stays roughly constant.
    factor = 90.0 * math.sqrt(n)
    return {
        node: (round(x * factor, 1), round(y * factor, 1))
        for node, (x, y) in pos.items()
    }


def _node_title(graph: nx.DiGraph, node: str, community_num: int | None) -> str:
    attrs = graph.nodes[node]
    parts = [
        attrs.get("title", ""),
        f"年份: {attrs.get('year') or '未知'}",
        f"期刊: {attrs.get('source_name') or '-'}",
        f"被引: {attrs.get('cited_by_count', 0)}",
    ]
    authors = format_authors(attrs.get("authors", []))
    if authors:
        parts.append(f"作者: {authors}")
    if community_num is not None:
        parts.append(f"社区: {community_num}")
    parts.append(f"ID: {node}")
    return "\n".join(parts)


def visualize_citation_graph(
    graph: nx.DiGraph,
    communities: dict[str, int],
    output_dir: str | None = None,
) -> str:
    """Render the citation graph to an interactive HTML file.

    Returns the absolute path of the generated HTML file.
    """
    if output_dir is None:
        output_dir = str(Path.home() / ".AI-CACHE" / "openalex" / "graphs")
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    filename = f"graph_{_work_ids_digest(graph)}_{graph.number_of_nodes()}n.html"
    file_path = out_path / filename

    colors = color_by_year(graph)
    sizes = size_by_citations(graph)

    html = _render_html(graph, colors, sizes, communities)
    file_path.write_text(html, encoding="utf-8")
    return str(file_path)


def _work_ids_digest(graph: nx.DiGraph) -> str:
    ids = sorted(graph.nodes)
    digest = hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:12]
    return digest


def _legend_html(graph: nx.DiGraph, communities: dict[str, int]) -> str:
    """Build a compact legend for year, citations, and transient physics."""
    years = sorted(
        graph.nodes[n].get("year")
        for n in graph.nodes
        if isinstance(graph.nodes[n].get("year"), int)
    )
    lo = years[0] if years else "?"
    hi = years[-1] if years else "?"
    community_count = len(set(communities.values())) if communities else 0
    return "\n".join(
        [
            "<b>颜色 = 发表年份</b>",
            '<div class="yearbar"></div>',
            f'<div class="yearrange"><span>{lo}</span><span>{hi}</span></div>',
            "<b>大小 = 被引量</b>(稳健对数缩放)",
            "<b>线深 = 局部引用强度</b>",
            f"引文社区: {community_count} 个(悬停查看)",
            "点击节点高亮;悬停显示作者与年份",
            '<label class="ph"><input type="checkbox" id="physicsToggle" checked>'
            "力导向动画(稳定后自动关闭)</label>",
        ]
    )


def _render_html(
    graph: nx.DiGraph,
    colors: dict[str, str],
    sizes: dict[str, int],
    communities: dict[str, int],
) -> str:
    """Assemble the standalone vis.js HTML document."""
    layout = _compute_layout(graph)
    ranked_nodes = _ranked_nodes(graph)
    prominent = set(ranked_nodes[:_label_limit(graph)])
    ranks = {node: index for index, node in enumerate(ranked_nodes)}
    default_selected = ranked_nodes[0] if ranked_nodes else None
    nodes_data: list[dict] = []
    for node in graph.nodes:
        attrs = graph.nodes[node]
        year = attrs.get("year")
        x, y = layout.get(node, (0.0, 0.0))
        label = _display_label(graph, node)
        background = colors.get(node, FALLBACK_COLOR)
        community = communities.get(node)
        nodes_data.append(
            {
                "id": node,
                "label": label if node in prominent else "",
                "fullLabel": label,
                "pinnedLabel": node in prominent,
                "paperTitle": attrs.get("title", ""),
                "paperAbstract": (attrs.get("abstract") or "").strip(),
                "authorsText": format_authors(attrs.get("authors", [])),
                "year": year,
                "source": attrs.get("source_name") or "",
                "citationCount": max(0, attrs.get("cited_by_count", 0)),
                "community": community,
                "rank": ranks.get(node, len(ranks)),
                "color": {
                    "background": background,
                    "border": "#8fa1b8",
                    "highlight": {"background": background, "border": "#c27803"},
                    "hover": {"background": background, "border": "#4f6f9f"},
                },
                "shape": "dot",
                "size": sizes.get(node, 20),
                "x": x,
                "y": y,
                "title": _node_title(graph, node, community),
            }
        )
    edges_data = _styled_edges(graph, communities)

    graph_json = json.dumps(
        {"nodes": nodes_data, "edges": edges_data, "defaultSelectedId": default_selected}
    )
    legend = _legend_html(graph, communities)
    vis_lib = _load_vis_lib()

    return (
        _HTML_TEMPLATE
        .replace("__VIS_LIB__", vis_lib)
        .replace("__DATA__", graph_json)
        .replace("__LEGEND__", legend)
    )


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>OpenAlex 引文网络</title>
<style>
  :root {
    --left-panel-w: 360px;
    --right-panel-w: 380px;
    --accent: #1f4f8f;
    --selection: #c27803;
    --muted: #6f7d83;
    --rule: #e8ecee;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: #ffffff; color: #17252a;
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
    overflow: hidden; }
  #net { position: fixed; top: 0;
    left: var(--left-panel-w); right: var(--right-panel-w); bottom: 0;
    width: auto; height: auto; background-color: #ffffff; }
  .side-panel { position: fixed; top: 0;
    bottom: 0; width: var(--left-panel-w); background: #fff; z-index: 12;
    overflow: hidden; }
  .left-panel { left: 0; border-right: 1px solid var(--rule); }
  .right-panel { right: 0; width: var(--right-panel-w); border-left: 1px solid var(--rule);
    box-shadow: -6px 0 20px rgba(30,42,46,0.06); }
  .panel-head { height: 42px; padding: 14px 22px 0; color: var(--accent);
    border-bottom: 1px solid #edf0f1; font-size: 12px; font-weight: 800; }
  .paper-list { position: absolute; top: 42px; left: 0; right: 0; bottom: 0;
    overflow-y: auto; padding-bottom: 20px; }
  .paper-row { display: grid; grid-template-columns: 1fr auto; gap: 8px;
    padding: 15px 22px 14px; border-bottom: 1px solid #edf0f1; cursor: pointer;
    background: #fff; }
  .paper-row:hover { background: #f8fbfb; }
  .paper-row.active { background: #fff8e8; box-shadow: inset 3px 0 var(--selection); }
  .origin-label { grid-column: 1 / 3; margin-bottom: -2px; color: #a86704;
    font-size: 12px; font-weight: 800; }
  .paper-row h3 { grid-column: 1 / 3; margin: 0; font-size: 14px; line-height: 1.25;
    font-weight: 700; color: #111b1f; }
  .paper-authors { min-width: 0; overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; color: var(--muted); font-size: 12px; }
  .paper-year { color: #6d7b81; font-size: 12px; align-self: end; }
  .detail-card { padding: 28px 28px 34px; overflow-y: auto; height: 100%; }
  .detail-card h2 { margin: 0 0 14px; color: #111b1f; font-size: 19px;
    line-height: 1.35; font-weight: 800; }
  .detail-authors { display: flex; flex-wrap: wrap; gap: 8px; color: #6c767b;
    font-size: 13px; margin-bottom: 12px; }
  .author-chip { background: #edf1f2; color: #526166; border-radius: 3px;
    padding: 2px 7px; }
  .detail-meta { color: #7a858a; font-size: 13px; line-height: 1.7; margin-bottom: 20px; }
  .detail-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px; margin: 18px 0 22px; }
  .stat { border: 1px solid #e2e7e8; border-radius: 6px; padding: 10px 8px; }
  .stat b { display: block; color: #10252b; font-size: 16px; line-height: 1.1; }
  .stat span { display: block; margin-top: 4px; color: #718086; font-size: 11px; }
  .detail-abstract { margin: 0 0 20px; color: #26383e; font-size: 13px;
    line-height: 1.65; }
  .detail-abstract b { display: block; margin-bottom: 6px; color: #17252a; }
  .detail-section { border-top: 1px solid #edf0f1; padding-top: 16px;
    color: #26383e; font-size: 13px; line-height: 1.65; }
  .id-line { margin-top: 16px; color: #758287; font-size: 12px; word-break: break-all; }
  .empty-list { padding: 18px 22px; color: #7a858a; font-size: 13px; }
  .legend { position: fixed; left: calc(var(--left-panel-w) + 24px); bottom: 18px;
    width: 230px; background: transparent; border: 0; padding: 0; font-size: 11px;
    line-height: 1.6; color: #5b6472; z-index: 10; }
  .legend b { display: none; }
  .legend .yearbar { height: 12px; margin-top: 5px; border-radius: 0;
    background: linear-gradient(90deg, #e0e8f2, #224a88); }
  .legend .yearrange { display: flex; justify-content: space-between; color: #284a77; }
  .legend .ph { display: block; margin-top: 6px; cursor: pointer; color: #7a878b; }
  @media (max-width: 1100px) {
    :root { --left-panel-w: 300px; --right-panel-w: 320px; }
  }
</style>
</head>
<body>
<aside class="side-panel left-panel">
  <div class="panel-head">Papers in this graph</div>
  <div class="paper-list" id="paperList"></div>
</aside>
<aside class="side-panel right-panel">
  <article class="detail-card">
    <h2 id="detailTitle"></h2>
    <div class="detail-authors" id="detailAuthors"></div>
    <div class="detail-meta" id="detailMeta"></div>
    <div class="detail-stats" id="detailStats"></div>
    <div class="detail-abstract" id="detailAbstract"></div>
    <div class="detail-section" id="detailContext"></div>
    <div class="id-line" id="detailId"></div>
  </article>
</aside>
<div id="net"></div>
<div class="legend">
  __LEGEND__
</div>
<script type="text/javascript">
__VIS_LIB__
</script>
<script type="text/javascript">
  var data = __DATA__;
  function init() {
    var nodes = new vis.DataSet(data.nodes);
    var edges = new vis.DataSet(data.edges);
    var container = document.getElementById('net');
    var paperIndex = {};
    data.nodes.forEach(function (node) { paperIndex[node.id] = node; });
    data.edges.forEach(function (edge) {
      if (paperIndex[edge.from]) paperIndex[edge.from].outCount = (paperIndex[edge.from].outCount || 0) + 1;
      if (paperIndex[edge.to]) paperIndex[edge.to].inCount = (paperIndex[edge.to].inCount || 0) + 1;
    });
    var options = {
      nodes: {
        borderWidth: 1.4,
        borderWidthSelected: 4,
        shadow: { enabled: true, color: 'rgba(31,49,73,0.15)', size: 9, x: 0, y: 2 },
        font: { size: 12, color: '#1f2937', strokeWidth: 5,
                strokeColor: 'rgba(255,255,255,0.92)' },
        labelHighlightBold: false,
        chosen: { node: function (values, id, selected) {
          if (selected) {
            values.borderColor = '#c27803';
            values.borderWidth = 4;
            values.shadow = true;
            values.shadowColor = 'rgba(194,120,3,0.30)';
            values.shadowSize = 18;
          }
        } }
      },
      edges: {
        arrows: { to: { enabled: true, scaleFactor: 0.22 } },
        color: { color: 'rgba(68,82,105,0.06)',
                 highlight: 'rgba(51,67,90,0.56)',
                 hover: 'rgba(51,67,90,0.42)', inherit: false },
        width: 0.4,
        selectionWidth: 1.15,
        hoverWidth: 0.8,
        smooth: false
      },
      physics: {
        enabled: true,
        solver: 'barnesHut',
        barnesHut: {
          theta: 0.65,
          gravitationalConstant: -2600,
          centralGravity: 0.18,
          springLength: 128,
          springConstant: 0.018,
          damping: 0.28,
          avoidOverlap: 0.48
        },
        maxVelocity: 24,
        minVelocity: 0.65,
        timestep: 0.35,
        adaptiveTimestep: true,
        stabilization: { enabled: false }
      },
      interaction: { hover: true, tooltipDelay: 120,
                     hideEdgesOnDrag: true, hideEdgesOnZoom: true }
    };
    var network = new vis.Network(container, { nodes: nodes, edges: edges }, options);
    window.__network = network;

    // Show labels for minor nodes only while hovered or selected.
    var selectedNode = null;
    var syncingSelection = false;
    var sortedPapers = data.nodes.slice().sort(function (a, b) {
      return (a.rank || 0) - (b.rank || 0);
    });
    function escapeHtml(value) {
      return String(value || '').replace(/[&<>"']/g, function (char) {
        return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char];
      });
    }
    function paperTitle(node) {
      return node.paperTitle || node.fullLabel || node.id;
    }
    function renderAuthors(text) {
      if (!text) return '<span>Authors unavailable</span>';
      var parts = text.split(',').map(function (part) { return part.trim(); }).filter(Boolean);
      if (parts.length <= 3) {
        return parts.map(function (part) { return '<span>' + escapeHtml(part) + '</span>'; }).join('');
      }
      return '<span>' + escapeHtml(parts[0]) + '</span><span class="author-chip">+' + (parts.length - 1) + ' authors</span>';
    }
    function renderPaperList() {
      var rows = sortedPapers;
      var list = document.getElementById('paperList');
      if (!rows.length) {
        list.innerHTML = '<div class="empty-list">No papers in this graph.</div>';
        return;
      }
      list.innerHTML = rows.map(function (node, index) {
        var active = node.id === selectedNode ? ' active' : '';
        var head = index === 0 ? '<div class="origin-label">Origin paper</div>' : '';
        return '<div class="paper-row' + active + '" data-id="' + escapeHtml(node.id) + '">' +
          head +
          '<h3>' + escapeHtml(paperTitle(node)) + '</h3>' +
          '<div class="paper-authors">' + escapeHtml(node.authorsText || node.source || 'Authors unavailable') + '</div>' +
          '<div class="paper-year">' + escapeHtml(node.year || '-') + '</div>' +
          '</div>';
      }).join('');
      Array.prototype.forEach.call(list.querySelectorAll('.paper-row'), function (row) {
        row.addEventListener('click', function () {
          selectPaper(row.getAttribute('data-id'), true);
        });
      });
    }
    function updateDetail(node) {
      var title = paperTitle(node);
      var inCount = node.inCount || 0;
      var outCount = node.outCount || 0;
      document.getElementById('detailTitle').textContent = title;
      document.getElementById('detailAuthors').innerHTML = renderAuthors(node.authorsText);
      document.getElementById('detailMeta').textContent =
        [node.year || 'Unknown year', node.source || 'Unknown venue'].join(', ');
      document.getElementById('detailStats').innerHTML =
        '<div class="stat"><b>' + escapeHtml(node.citationCount || 0) + '</b><span>Citations</span></div>' +
        '<div class="stat"><b>' + escapeHtml(inCount) + '</b><span>Local cited by</span></div>' +
        '<div class="stat"><b>' + escapeHtml(outCount) + '</b><span>Local refs</span></div>';
      var abstractEl = document.getElementById('detailAbstract');
      if (node.paperAbstract) {
        abstractEl.style.display = 'block';
        abstractEl.innerHTML = '<b>Abstract</b>' + escapeHtml(node.paperAbstract);
      } else {
        abstractEl.style.display = 'none';
        abstractEl.innerHTML = '';
      }
      document.getElementById('detailContext').innerHTML =
        '<b>Community ' + escapeHtml(node.community == null ? '-' : node.community) + '</b><br>' +
        'This paper is connected to ' + escapeHtml(inCount + outCount) +
        ' local citation links in the current graph.';
      document.getElementById('detailId').textContent = 'OpenAlex ID: ' + node.id;
    }
    function selectPaper(id, focusNode) {
      var node = paperIndex[id];
      if (!node) return;
      if (selectedNode && selectedNode !== id) {
        var previous = nodes.get(selectedNode);
        if (previous && !previous.pinnedLabel) nodes.update({ id: previous.id, label: '' });
      }
      selectedNode = id;
      if (node.fullLabel) nodes.update({ id: node.id, label: node.fullLabel });
      updateDetail(node);
      renderPaperList();
      if (!syncingSelection) {
        syncingSelection = true;
        network.selectNodes([id], false);
        syncingSelection = false;
      }
      if (focusNode) {
        network.focus(id, { scale: 1.05, animation: { duration: 450, easingFunction: 'easeInOutQuad' } });
      }
    }
    network.on('hoverNode', function (params) {
      var node = nodes.get(params.node);
      if (node && !node.pinnedLabel && node.fullLabel) {
        nodes.update({ id: node.id, label: node.fullLabel });
      }
      if (node && paperIndex[node.id]) {
        updateDetail(paperIndex[node.id]);
      }
    });
    network.on('blurNode', function (params) {
      var node = nodes.get(params.node);
      if (node && !node.pinnedLabel && node.id !== selectedNode) {
        nodes.update({ id: node.id, label: '' });
      }
      if (selectedNode && paperIndex[selectedNode]) {
        updateDetail(paperIndex[selectedNode]);
      }
    });
    network.on('selectNode', function (params) {
      if (!syncingSelection && params.nodes[0]) {
        selectPaper(params.nodes[0], false);
        return;
      }
      if (selectedNode && selectedNode !== params.nodes[0]) {
        var previous = nodes.get(selectedNode);
        if (previous && !previous.pinnedLabel) {
          nodes.update({ id: previous.id, label: '' });
        }
      }
      selectedNode = params.nodes[0];
      var node = nodes.get(selectedNode);
      if (node && node.fullLabel) nodes.update({ id: node.id, label: node.fullLabel });
    });
    network.on('deselectNode', function () {
      var node = selectedNode ? nodes.get(selectedNode) : null;
      if (node && !node.pinnedLabel) nodes.update({ id: node.id, label: '' });
    });

    // Animate briefly, then freeze so large graphs do not consume CPU forever.
    var toggle = document.getElementById('physicsToggle');
    var freezeTimer = null;
    var animationStarted = performance.now();
    function freezePhysics() {
      if (freezeTimer) clearTimeout(freezeTimer);
      var remaining = Math.max(0, 1200 - (performance.now() - animationStarted));
      freezeTimer = setTimeout(function () {
        network.setOptions({ physics: { enabled: false } });
        if (toggle) toggle.checked = false;
      }, remaining);
    }
    network.once('stabilized', freezePhysics);
    freezeTimer = setTimeout(freezePhysics, 6000);
    if (toggle) {
      toggle.addEventListener('change', function (e) {
        if (freezeTimer) clearTimeout(freezeTimer);
        network.setOptions({ physics: { enabled: e.target.checked } });
        if (e.target.checked) {
          animationStarted = performance.now();
          network.startSimulation();
          freezeTimer = setTimeout(freezePhysics, 6000);
        }
      });
    }
    renderPaperList();
    if (data.defaultSelectedId) {
      selectPaper(data.defaultSelectedId, false);
    }
  }
  (function loadVis(i) {
    if (typeof vis !== 'undefined') { init(); return; }
    var urls = [
      'https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js',
      'https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js',
      'https://registry.npmmirror.com/vis-network/9.1.9/files/standalone/umd/vis-network.min.js'
    ];
    if (i >= urls.length) {
      document.body.insertAdjacentHTML('beforeend',
        '<div style="position:fixed;left:10px;bottom:10px;background:#fdecea;border:1px solid #e15759;padding:8px 12px;font-size:12px;z-index:99">vis-network 加载失败,请检查网络后刷新页面。</div>');
      return;
    }
    var s = document.createElement('script');
    s.src = urls[i];
    s.onload = init;
    s.onerror = function () { s.remove(); loadVis(i + 1); };
    document.head.appendChild(s);
  })(0);
</script>
</body>
</html>
"""
