"""search tools — keyword, semantic, and IDs-based search for OpenAlex works.

Pure API search + formatting.  Upsert logic lives in local/upsert.py; the
RemoteService facade wires them together via lazy import of local.service.
"""

from openalex_mcp.common import short_openalex_id

from .backfill import backfill_missing_abstracts
from .client import OpenAlexClient
from .filters import build_filter_string
from .fulltext import fetch_and_store_fulltext


def _has_abstract(work: dict) -> bool:
    """Determine if a work has an abstract."""
    val = work.get("has_abstract")
    if val is not None:
        return bool(val)
    return bool(work.get("abstract_inverted_index") or work.get("abstract"))


def _format_summary(
    works: list[dict],
    library_msg: str = "",
) -> str:
    """Format a human-readable summary of search results."""
    count = len(works)

    lines = [
        f"Search returned {count} result(s)",
    ]

    if library_msg:
        lines.append(library_msg)

    lines.append("")

    for i, work in enumerate(works[:5], 1):
        work_id = short_openalex_id(work.get("id", ""))
        title = work.get("title") or work.get("display_name", "Untitled")
        if len(title) > 60:
            title = title[:57] + "..."
        year = work.get("publication_year", "?")
        citations = work.get("cited_by_count", 0)
        has_abs = "Y" if _has_abstract(work) else "N"
        lines.append(
            f"{i}. [{work_id}] {title} ({year}, cites:{citations}, abstract:{has_abs})"
        )

    if count > 5:
        lines.append(f"... and {count - 5} more on this page")

    lines.append("")
    lines.append("Next steps:")
    lines.append("- `library_query` — query your local library")
    lines.append("- `library_export` — export selected papers to BibTeX")
    lines.append("- `library_stats` — view library overview")
    lines.append(
        "- Detail questions: rerun with `fetch_fulltext=true`, then query "
        "`works.fulltext` with `substr()` for a bounded evidence excerpt"
    )

    return "\n".join(lines)


async def _search_and_upsert(
    client: OpenAlexClient,
    query: str,
    mode: str,
    filters: str | None,
    per_page: int,
    page: int,
    fetch_fulltext: bool = False,
    fulltext_limit: int = 2,
    use_openalex_content_api: bool = False,
) -> dict:
    """Search, backfill missing abstracts, and optionally extract OA full text."""
    data = await client.search(
        query=query,
        mode=mode,
        filters=filters,
        per_page=per_page,
        page=page,
    )

    works: list[dict] = data.get("results", [])
    if not works:
        summary = f"No results found for query: {query}"
        return {"summary": summary, "works": []}

    backfill_report = await backfill_missing_abstracts(works)
    library_msg = ""
    fulltext_report: dict | None = None
    try:
        from openalex_mcp.local import get_library, upsert_works  # lazy import

        library = get_library()
        added, failed, after_total = await upsert_works(library, works)
        if failed > 0:
            library_msg = (
                f"Library: {after_total:,} total "
                f"(+{added} new, {failed} failed — DB may be locked)"
            )
        else:
            library_msg = (
                f"Library: {after_total:,} total "
                f"(+{added} new this search)"
            )

        if fetch_fulltext:
            fulltext_report = await fetch_and_store_fulltext(
                client,
                library,
                works,
                limit=fulltext_limit,
                use_openalex_content_api=use_openalex_content_api,
            )
            library_msg += (
                f"; full text: {fulltext_report['stored']} stored, "
                f"{fulltext_report['unavailable']} unavailable, "
                f"{fulltext_report['failed']} failed"
            )
    except Exception as exc:
        library_msg = f"Library: write failed ({exc})"

    if backfill_report["filled"]:
        library_msg += (
            f"; abstract backfill: {backfill_report['filled']} filled "
            f"(Crossref {backfill_report['sources']['crossref']}, "
            f"Elsevier {backfill_report['sources']['elsevier']}, "
            f"Scopus {backfill_report['sources']['scopus']})"
        )
        if backfill_report.get("limited"):
            library_msg += (
                f"; prioritized {backfill_report['selected']} of "
                f"{backfill_report['targets']} missing-abstract works"
            )
    elif backfill_report.get("limited") and backfill_report.get("selected"):
        library_msg += (
            f"; abstract backfill: 0 filled from "
            f"{backfill_report['selected']} prioritized Elsevier journal work(s)"
        )
    elif backfill_report["skipped"]:
        library_msg += f"; abstract backfill skipped: {backfill_report['reason']}"

    summary = _format_summary(works, library_msg=library_msg)
    return {
        "summary": summary,
        "works": works,
        "backfill": backfill_report,
        "fulltext": fulltext_report,
    }


# ---------------------------------------------------------------------------
# Public search functions
# ---------------------------------------------------------------------------


async def search_keyword(
    client: OpenAlexClient,
    query: str = "",
    publication_year: str = ">2021",
    cited_by_count: str | None = None,
    cites: str | None = None,
    cited_by: str | None = None,
    related_to: str | None = None,
    source_id: str | None = None,
    institution_id: str | None = None,
    author_id: str | None = None,
    publisher_id: str | None = None,
    funder_id: str | None = None,
    page: int = 1,
    fetch_fulltext: bool = False,
    fulltext_limit: int = 2,
    use_openalex_content_api: bool = False,
) -> dict:
    """Search OpenAlex works with full-text keyword search.

    Leave ``query`` empty to fetch ALL works matching only the ID filters
    (no full-text search).  Useful for getting every paper by an author,
    institution, journal, publisher, or funder — combine with
    ``publication_year`` or ``cited_by_count`` to narrow the result set.

    Returns structured result dict with keys: ``summary`` (str), ``works`` (list[dict]).

    Args:
        client: OpenAlexClient instance.
        query: Search text (optional — omit to list all works matching filters).
            Supports Boolean operators (AND, OR, NOT), quoted phrases ("..."),
            and wildcards (machin*).
        publication_year: Year or range (default ``">2021"``).
        cited_by_count: Citation count threshold, e.g. ``">50"``.
        cites: Find works that cite these OpenAlex IDs.
        cited_by: Find works cited by these OpenAlex IDs.
        related_to: Find works related to these OpenAlex IDs.
        source_id: Source (journal) OpenAlex ID, e.g. ``"S4210208519"``.
        institution_id: Institution OpenAlex ID, e.g. ``"I129432676"``.
        author_id: Author OpenAlex ID, e.g. ``"A5023888391"``.
        publisher_id: Publisher OpenAlex ID, e.g. ``"P4310319901"``.
        funder_id: Funder OpenAlex ID, e.g. ``"F4320306076"``.
        page: Page number (1-based).
        fetch_fulltext: Extract OA PDF text into the local database. Enable only
            when the user asks for methods, parameters, or other paper details.
        fulltext_limit: Maximum papers whose text is extracted (default 2).
        use_openalex_content_api: Allow paid OpenAlex Content API fallback when
            no direct OA PDF URL is available.
    """
    filters = build_filter_string(
        publication_year=publication_year,
        cited_by_count=cited_by_count,
        cites=cites,
        cited_by=cited_by,
        related_to=related_to,
        source_id=source_id,
        institution_id=institution_id,
        author_id=author_id,
        publisher_id=publisher_id,
        funder_id=funder_id,
    )

    return await _search_and_upsert(
        client=client,
        query=query,
        mode="keyword",
        filters=filters,
        per_page=100,
        page=page,
        fetch_fulltext=fetch_fulltext,
        fulltext_limit=fulltext_limit,
        use_openalex_content_api=use_openalex_content_api,
    )


async def search_semantic(
    client: OpenAlexClient,
    query: str,
    publication_year: str = ">2021",
    cited_by_count: str | None = None,
    cites: str | None = None,
    cited_by: str | None = None,
    related_to: str | None = None,
    source_id: str | None = None,
    institution_id: str | None = None,
    author_id: str | None = None,
    publisher_id: str | None = None,
    funder_id: str | None = None,
    page: int = 1,
    fetch_fulltext: bool = False,
    fulltext_limit: int = 2,
    use_openalex_content_api: bool = False,
) -> dict:
    """Search OpenAlex works with AI-powered semantic search.

    Best for paragraph-length queries (research descriptions, abstracts).
    Max 50 results per page, rate-limited to 1 req/sec.

    Returns structured result dict with keys: ``summary`` (str), ``works`` (list[dict]).

    Args:
        client: OpenAlexClient instance.
        query: Natural-language query describing your research topic.
        publication_year: Year or range (default ``">2021"``).
        cited_by_count: Citation count threshold, e.g. ``">50"``.
        cites: Find works that cite these OpenAlex IDs.
        cited_by: Find works cited by these OpenAlex IDs.
        related_to: Find works related to these OpenAlex IDs.
        source_id: Source (journal) OpenAlex ID, e.g. ``"S4210208519"``.
        institution_id: Institution OpenAlex ID, e.g. ``"I129432676"``.
        author_id: Author OpenAlex ID, e.g. ``"A5023888391"``.
        publisher_id: Publisher OpenAlex ID, e.g. ``"P4310319901"``.
        funder_id: Funder OpenAlex ID, e.g. ``"F4320306076"``.
        page: Page number (1-based).
        fetch_fulltext: Extract OA PDF text into the local database. Enable only
            when the user asks for methods, parameters, or other paper details.
        fulltext_limit: Maximum papers whose text is extracted (default 2).
        use_openalex_content_api: Allow paid OpenAlex Content API fallback when
            no direct OA PDF URL is available.
    """
    filters = build_filter_string(
        publication_year=publication_year,
        cited_by_count=cited_by_count,
        cites=cites,
        cited_by=cited_by,
        related_to=related_to,
        source_id=source_id,
        institution_id=institution_id,
        author_id=author_id,
        publisher_id=publisher_id,
        funder_id=funder_id,
    )

    return await _search_and_upsert(
        client=client,
        query=query,
        mode="semantic",
        filters=filters,
        per_page=50,
        page=page,
        fetch_fulltext=fetch_fulltext,
        fulltext_limit=fulltext_limit,
        use_openalex_content_api=use_openalex_content_api,
    )


async def search_ids(
    client: OpenAlexClient,
    query: str,
    fetch_fulltext: bool = False,
    fulltext_limit: int = 2,
    use_openalex_content_api: bool = False,
) -> dict:
    """Fetch specific works by OpenAlex ID or DOI.

    ``fetch_fulltext`` is intended for detail questions about the named papers;
    it extracts text into the library and does not keep the temporary PDF.
    """
    return await _search_and_upsert(
        client=client,
        query=query,
        mode="ids",
        filters=None,
        per_page=100,
        page=1,
        fetch_fulltext=fetch_fulltext,
        fulltext_limit=fulltext_limit,
        use_openalex_content_api=use_openalex_content_api,
    )
