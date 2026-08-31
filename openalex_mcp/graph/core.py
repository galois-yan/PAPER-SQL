"""Citation graph construction — build a networkx.DiGraph from the local library.

Nodes are the works explicitly requested by the caller (an induced subgraph):
a directed edge ``A -> B`` means "A cites B", derived from the JSON
``referenced_works`` column. Only edges whose BOTH endpoints are among the
requested works are kept, so the graph is fully self-contained.
"""

from __future__ import annotations

import json
from typing import Any

import networkx as nx


class GraphError(ValueError):
    """Raised for caller errors (empty work_ids, nothing found, etc.)."""


def parse_work_ids(work_ids: str) -> list[str]:
    """Parse a comma-separated work ID string into a list of short IDs.

    Raises GraphError if the string is empty or has no parseable IDs.
    """
    if work_ids is None:
        raise GraphError("work_ids is required.")
    raw = [x.strip() for x in work_ids.split(",") if x.strip()]
    if not raw:
        raise GraphError(
            "work_ids is empty. 请先用 library_query 做本地语义检索选出目标论文,"
            "再把它们的 ID 列表传进来。\n例如:\n"
            "  SELECT id FROM works WHERE vec IS NOT NULL\n"
            "  ORDER BY vec_distance_cosine(vec, {query_vec}) LIMIT 100"
        )
    return raw


def _parse_refs(value: Any) -> list[str]:
    """Parse a referenced_works column value (JSON string or None) into IDs."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(x) for x in value]
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


def _parse_authors(value: Any) -> list[str]:
    """Parse an authors_json column value into a list of display names."""
    if not value:
        return []
    data = value if isinstance(value, list) else None
    if data is None:
        try:
            data = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for auth in data:
        if not isinstance(auth, dict):
            continue
        author = auth.get("author") or {}
        name = (author.get("display_name") or "").strip()
        if name:
            names.append(name)
    return names


# Display cap for author lists in tooltips / analysis output.
AUTHOR_LIMIT = 5


def format_authors(authors: list[str], limit: int = AUTHOR_LIMIT) -> str:
    """Format an author list, truncating after ``limit`` with "等 N 位"."""
    if not authors:
        return ""
    if len(authors) <= limit:
        return ", ".join(authors)
    shown = ", ".join(authors[:limit])
    return f"{shown} 等 {len(authors)} 位"


def build_citation_graph(library, work_ids: list[str]) -> tuple[nx.DiGraph, list[str]]:
    """Build the citation DiGraph over the requested work IDs.

    Returns ``(graph, missing_ids)`` where ``missing_ids`` are requested IDs
    that are not present in the library (reported, not fatal).
    """
    if not work_ids:
        raise GraphError("work_ids is empty.")

    placeholders = ",".join("?" for _ in work_ids)
    rows = library._fetch_dicts(
        f"SELECT id, title, publication_year, cited_by_count, source_name, abstract, "
        f"referenced_works, authors_json FROM works WHERE id IN ({placeholders})",
        work_ids,
    )

    found = {r["id"]: r for r in rows}
    missing = [wid for wid in work_ids if wid not in found]

    graph = nx.DiGraph()
    for row in rows:
        graph.add_node(
            row["id"],
            title=row["title"] or "(untitled)",
            year=row["publication_year"],
            cited_by_count=row["cited_by_count"] or 0,
            source_name=row["source_name"] or "",
            abstract=(row["abstract"] or "").strip(),
            authors=_parse_authors(row["authors_json"]),
        )

    for row in rows:
        for target in _parse_refs(row["referenced_works"]):
            if target in found:
                graph.add_edge(row["id"], target)

    return graph, missing
