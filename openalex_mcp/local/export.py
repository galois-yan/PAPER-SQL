"""BibTeX export from library — standalone functions."""

from __future__ import annotations

from typing import Any

from .bibtex import (
    _ENTRY_TYPE_MAP,
    _LANGUAGE_MAP,
    author_display_names,
    citation_key_base,
    citation_key_suffix,
    escape_bibtex,
    parse_author_name,
)


_CITE_KEY_STYLES = {"author_year", "openalex_id"}
_SORT_FIELDS = {
    "id",
    "title",
    "publication_year",
    "publication_date",
    "cited_by_count",
    "source_name",
    "is_oa",
}


def _normalize_sort(sort: str) -> str:
    """Validate a single-column sort expression for BibTeX export."""
    parts = [part.strip().lower() for part in sort.split(":")]
    if len(parts) not in (1, 2) or not parts[0]:
        raise ValueError("sort must use column[:asc|desc]")
    column = parts[0]
    direction = parts[1] if len(parts) == 2 else "asc"
    if column not in _SORT_FIELDS:
        raise ValueError(
            "sort column must be one of: " + ", ".join(sorted(_SORT_FIELDS))
        )
    if direction not in ("asc", "desc"):
        raise ValueError("sort direction must be 'asc' or 'desc'")
    return f"{column} {direction.upper()}"


def export_library_bibtex(
    library,
    work_ids: list[str] | None = None,
    sort: str | None = None,
    cite_key_style: str = "author_year",
) -> str:
    """Generate BibTeX entries from library columns."""
    if cite_key_style not in _CITE_KEY_STYLES:
        raise ValueError(
            "cite_key_style must be 'author_year' or 'openalex_id'"
        )

    sql = "SELECT * FROM works"
    params: list[Any] = []

    if work_ids:
        placeholders = ",".join("?" for _ in range(len(work_ids)))
        sql += f" WHERE id IN ({placeholders})"
        params = work_ids

    if sort:
        sql += f" ORDER BY {_normalize_sort(sort)}"
    else:
        sql += " ORDER BY publication_year DESC"

    rows = library._fetch_dicts(sql, params)

    entries: list[str] = []
    used_keys: dict[str, int] = {}
    for row in rows:
        work_id = row.get("id", "")
        if not work_id:
            continue
        if cite_key_style == "author_year":
            base_key = citation_key_base(
                row.get("authors_json"),
                row.get("publication_year"),
                fallback=work_id,
            )
        else:
            base_key = work_id
        count = used_keys.get(base_key, 0)
        used_keys[base_key] = count + 1
        cite_key = base_key if count == 0 else f"{base_key}{citation_key_suffix(count)}"
        bib = _row_to_bibtex(row, library, cite_key)
        if bib:
            entries.append(bib)

    return "\n\n".join(entries)


def _row_to_bibtex(
    row: dict[str, Any], library, cite_key: str | None = None
) -> str | None:
    """Convert a single works row dict into a BibTeX entry string."""
    work_id = row.get("id", "")
    cite_key = cite_key or work_id
    if not cite_key:
        return None

    entry_type = _ENTRY_TYPE_MAP.get(row.get("type", ""), "misc")

    lines: list[str] = [f"@{entry_type}{{{cite_key},"]

    # Title
    title = row.get("title", "")
    if title:
        lines.append(f"  title = {{{escape_bibtex(title)}}},")

    # Authors
    author_names = [
        parse_author_name(name)
        for name in author_display_names(row.get("authors_json"))
    ]
    if author_names:
        author_str = " and ".join(author_names)
        lines.append(f"  author = {{{escape_bibtex(author_str)}}},")

    # Year
    year = row.get("publication_year")
    if year is not None:
        lines.append(f"  year = {{{year}}},")

    # Date
    pub_date = row.get("publication_date")
    if pub_date:
        lines.append(f"  date = {{{pub_date}}},")

    # Journal
    source_id = row.get("source_id")
    if source_id:
        source_name = library.get_source_name(source_id)
        if source_name:
            lines.append(f"  journal = {{{escape_bibtex(source_name)}}},")

    # DOI
    doi = row.get("doi", "")
    if doi:
        lines.append(f"  doi = {{{doi}}},")

    # URL
    oa_url = row.get("oa_url")
    if oa_url:
        lines.append(f"  url = {{{oa_url}}},")
    elif doi:
        lines.append(f"  url = {{https://doi.org/{doi}}},")

    # Abstract
    abstract = row.get("abstract", "")
    if abstract:
        lines.append(f"  abstract = {{{escape_bibtex(abstract)}}},")

    # Language
    lang_code = row.get("language")
    if lang_code:
        lang = _LANGUAGE_MAP.get(
            str(lang_code).lower().strip(), str(lang_code)
        )
        lines.append(f"  language = {{{lang}}},")

    # Remove trailing comma
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]

    lines.append("}")
    return "\n".join(lines)
