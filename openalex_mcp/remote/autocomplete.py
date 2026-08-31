"""autocomplete tool — fast typeahead lookup for OpenAlex entities.

Obtain IDs for authors, sources (journals), institutions, publishers, and
funders without the ~$0.001 cost of a full search.  Use the returned IDs in
``search_keyword`` or ``search_semantic`` to filter works by entity.
"""

from __future__ import annotations

from openalex_mcp.common import short_openalex_id

from .client import OpenAlexClient

# ---------------------------------------------------------------------------
# Supported entity types
# ---------------------------------------------------------------------------

ENTITY_TYPES: frozenset[str] = frozenset(
    ["authors", "sources", "institutions", "publishers", "funders"]
)

_ENTITY_LABELS: dict[str, str] = {
    "authors": "Author",
    "sources": "Journal / Source",
    "institutions": "Institution",
    "publishers": "Publisher",
    "funders": "Funder",
}

# Map entity_type → the filter parameter name in search_keyword/search_semantic
_FILTER_PARAM: dict[str, str] = {
    "authors": "author_id",
    "sources": "source_id",
    "institutions": "institution_id",
    "publishers": "publisher_id",
    "funders": "funder_id",
}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _format_result(entry: dict) -> str:
    """Format a single autocomplete result as a compact line."""
    eid = short_openalex_id(entry.get("id", ""))
    name = entry.get("display_name", "Unknown")
    if len(name) > 80:
        name = name[:77] + "..."

    parts = [f"[{eid}] {name}"]

    hint = entry.get("hint", "") or ""
    if hint:
        parts.append(f"  ({hint})")

    wc = entry.get("works_count")
    cc = entry.get("cited_by_count", 0)
    if wc is not None:
        parts.append(f"  Works: {wc:,}  Cites: {cc:,}")
    else:
        parts.append(f"  Cites: {cc:,}")

    ext = entry.get("external_id", "") or ""
    if ext:
        parts.append(f"  ext: {ext}")

    return "".join(parts)


def _next_steps(entity_type: str) -> str:
    """Build a Next steps hint showing how to use the ID in a search filter."""
    label = _ENTITY_LABELS.get(entity_type, entity_type)
    param = _FILTER_PARAM.get(entity_type, "source_id")
    examples: dict[str, str] = {
        "authors": 'author_id="A5023888391"',
        "sources": 'source_id="S4210208519"',
        "institutions": 'institution_id="I129432676"',
        "publishers": 'publisher_id="P4310319901"',
        "funders": 'funder_id="F4320306076"',
    }
    example = examples.get(entity_type, f'{param}="..."')
    return (
        f"Copy an ID above, then use it in ``search_keyword`` / "
        f"``search_semantic`` with the ``{param}`` filter.\n"
        f"  Example: ``search_keyword(query=\"your topic\", {example})``"
    )


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------


async def autocomplete(
    client: OpenAlexClient,
    entity_type: str,
    query: str,
) -> str:
    """Look up OpenAlex entity IDs by name — fast typeahead search.

    Returns up to 10 results (~200 ms).  Use the IDs in
    ``search_keyword`` / ``search_semantic`` to filter works by author,
    journal, institution, publisher, or funder.

    Args:
        client: OpenAlexClient instance.
        entity_type: One of ``"authors"``, ``"sources"``, ``"institutions"``,
            ``"publishers"``, ``"funders"``.
        query: Name fragment to search for (e.g. ``"Northwestern"``).
    """
    if entity_type not in ENTITY_TYPES:
        label_list = ", ".join(ENTITY_TYPES)
        return (
            f"Error: unknown entity_type ``{entity_type}``. "
            f"Must be one of: {label_list}"
        )

    label = _ENTITY_LABELS.get(entity_type, entity_type)

    try:
        data = await client.autocomplete(
            entity_type=entity_type,
            query=query,
        )
    except (ValueError, RuntimeError) as e:
        return f"Error: {e}"

    results: list[dict] = data.get("results", [])
    meta: dict = data.get("meta", {})
    count: int = meta.get("count", 0)

    lines = [f"## {label} Autocomplete: `{query}`", f"Found {count} match(es)", ""]

    if not results:
        lines.append("_No matches found._")
        return "\n".join(lines)

    shown = min(len(results), 10)
    for entry in results[:10]:
        lines.append(_format_result(entry))
        lines.append("")

    lines.append("---")
    lines.append(f"Showing {shown} of {count}")
    lines.append("")
    lines.append(_next_steps(entity_type))

    return "\n".join(lines)
