"""Structured filter builder for the OpenAlex API.

Maps short, user-friendly parameter names to OpenAlex API filter field paths,
assembles filter strings deterministically, and validates free-form filter
strings to catch typos early.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Short name → OpenAlex API filter field
# ---------------------------------------------------------------------------

OPENALEX_FILTER_FIELDS: dict[str, str] = {
    "author_id": "authorships.author.id",
    "cited_by": "cited_by",
    "cited_by_count": "cited_by_count",
    "cites": "cites",
    "concept_id": "concepts.id",
    "funder_id": "grants.funder",
    "institution_id": "authorships.institutions.id",
    "is_oa": "is_oa",
    "issn": "primary_location.source.issn",
    "publication_year": "publication_year",
    "publisher_id": "primary_location.source.host_organization",
    "related_to": "related_to",
    "source_id": "primary_location.source.id",
    "work_type": "type",
}

# ---------------------------------------------------------------------------
# Valid field names (short names + API fields + common OpenAlex fields)
# ---------------------------------------------------------------------------

_VALID_FIELDS: set[str] = set(OPENALEX_FILTER_FIELDS.keys())
_VALID_FIELDS.update(OPENALEX_FILTER_FIELDS.values())

_VALID_FIELDS.update(
    [
        "abstract.search",
        "apc_list.value",
        "authorships.author.display_name",
        "authorships.author.id",
        "authorships.countries",
        "best_oa_location.source.id",
        "concepts.display_name",
        "display_name",
        "display_name.search",
        "doi",
        "from_publication_date",
        "fulltext.search",
        "grants.funder",
        "has_abstract",
        "host_venue.id",
        "ids.openalex",
        "is_retracted",
        "keywords.keyword",
        "openalex_id",
        "primary_location.source.host_organization",
        "primary_location.source.id",
        "primary_location.source.issn",
        "publication_date",
        "referenced_works",
        "relevance_score",
        "title.search",
        "to_publication_date",
    ]
)


def build_filter_string(**kwargs) -> str | None:
    """Build a deterministic OpenAlex filter string from structured parameters."""
    parts: list[str] = []

    for short_name in sorted(OPENALEX_FILTER_FIELDS):
        if short_name not in kwargs:
            continue
        value = kwargs[short_name]
        if value is None or (
            isinstance(value, str) and not value.strip()
        ):
            continue
        api_field = OPENALEX_FILTER_FIELDS[short_name]

        if short_name == "is_oa":
            str_value = "true" if value else "false"
        else:
            str_value = str(value)

        parts.append(f"{api_field}:{str_value}")

    extra = kwargs.get("extra_filters")
    if extra:
        parts.append(extra)

    return ",".join(parts) if parts else None


def _strip_search_suffix(field: str) -> str:
    """Strip a trailing ``.search`` suffix so the base field can be validated."""
    if field.endswith(".search"):
        return field[: -len(".search")]
    return field


def validate_filter_string(filters: str) -> None:
    """Validate that every field name in *filters* is a known OpenAlex field."""
    if not filters or not filters.strip():
        return

    invalid: list[str] = []
    for segment in filters.split(","):
        segment = segment.strip()
        if not segment:
            continue
        if ":" not in segment:
            invalid.append(segment)
            continue

        field = segment.split(":", 1)[0].strip()
        base = _strip_search_suffix(field)

        if base in _VALID_FIELDS or "." in base:
            continue

        invalid.append(field)

    if invalid:
        known = sorted(_VALID_FIELDS)
        raise ValueError(
            f"Unknown filter field(s): {', '.join(invalid)}. "
            f"Valid fields include: {', '.join(known[:20])}..."
        )
