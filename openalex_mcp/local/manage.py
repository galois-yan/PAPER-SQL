"""Library management tools — export, stats, delete."""

from __future__ import annotations

from pathlib import Path

from .manager import LibraryManager


def _export_target_path(target: str) -> Path:
    """Resolve a safe single-file BibTeX target under collections/."""
    target_name = (target or "").strip()
    target_part = Path(target_name)
    if (
        not target_name
        or target_part.is_absolute()
        or target_part.name != target_name
        or target_part.suffix.lower() != ".bib"
        or target_name in (".", "..")
    ):
        raise ValueError("target must be a single .bib filename")

    coll_dir = Path.home() / ".AI-CACHE" / "openalex" / "collections"
    coll_dir.mkdir(parents=True, exist_ok=True)
    resolved_dir = coll_dir.resolve()
    resolved_target = (coll_dir / target_name).resolve(strict=False)
    if resolved_target.parent != resolved_dir:
        raise ValueError("target must stay inside the collections directory")
    return resolved_target


def library_export(
    library: LibraryManager,
    work_ids: str = "*",
    target: str = "export.bib",
    sort: str | None = None,
    cite_key_style: str = "author_year",
) -> str:
    """Export works from the library to a BibTeX file.

    调用方（registry.py）需持锁。
    """
    ids: list[str] | None = None
    if work_ids.strip() != "*":
        ids = [i.strip() for i in work_ids.split(",") if i.strip()]
        if not ids:
            return "Error: no valid work IDs provided"

    try:
        target_path = _export_target_path(target)
    except ValueError as exc:
        return f"Error: {exc}"

    try:
        bibtex_text = library.export_to_bibtex(
            work_ids=ids,
            sort=sort,
            cite_key_style=cite_key_style,
        )
    except ValueError as exc:
        return f"Error: {exc}"

    if not bibtex_text:
        return "No works to export — library is empty or IDs not found."

    target_path.write_text(bibtex_text.rstrip() + "\n", encoding="utf-8")

    entry_count = bibtex_text.count("@")
    return f"Exported {entry_count} works to `{target_path}`"


def library_stats(library: LibraryManager) -> str:
    """Return a Markdown overview of the library.

    调用方（registry.py）需持锁。
    """
    stats = library.get_stats()

    total = stats["total"]
    if total == 0:
        return "Library is empty. Use `search_keyword` or `search_ids` to add papers."

    year_min = stats["year_min"] or "?"
    year_max = stats["year_max"] or "?"

    with_abs = stats["with_abstract"]
    abs_pct = round(with_abs / total * 100, 1) if total else 0

    with_emb = stats["with_embeddings"]
    emb_pct = round(with_emb / total * 100, 1) if total else 0

    oa_pct = round(stats["oa_count"] / total * 100, 1) if total else 0

    lines = [
        "## Library Overview",
        "",
        f"- **Total works:** {total:,}",
        f"- **Year range:** {year_min} – {year_max}",
        f"- **With abstracts:** {with_abs:,} ({abs_pct}%)",
        f"- **With extracted full text:** {stats['with_fulltext']:,}",
        f"- **With embeddings:** {with_emb:,} ({emb_pct}%)",
        f"- **Pending embeddings:** {stats['pending_embeddings']:,}",
        f"- **OA rate:** {oa_pct}%",
        "",
    ]

    top_sources = stats.get("top_sources", [])
    if top_sources:
        lines.append("### Top 10 Sources")
        lines.append("")
        lines.append("| Source | Papers | Type | OA% |")
        lines.append("|--------|--------|------|-----|")
        for s in top_sources:
            lines.append(
                f"| {s['name']} | {s['count']} | {s['type']} | {s['oa_pct']}% |"
            )
        lines.append("")

    top_concepts = stats.get("top_concepts", [])
    if top_concepts:
        lines.append("### Top 10 Concepts")
        lines.append("")
        lines.append("| Concept | Papers | Avg Score |")
        lines.append("|---------|--------|-----------|")
        for c in top_concepts:
            lines.append(
                f"| {c['name']} | {c['count']} | {c['avg_score']} |"
            )
        lines.append("")

    return "\n".join(lines)


def library_delete(
    library: LibraryManager,
    work_ids: str,
) -> str:
    """Delete works from the library.

    调用方（registry.py）需持锁。

    ⚠️ 谨慎使用:删除不可恢复。没有用户明确的删库指令时,AI 不应调用本函数;
    尤其是 ``work_ids="*"`` 会清空整个库(含论文、来源、全部嵌入向量)。
    """
    if work_ids.strip() == "*":
        total = library.delete_all()
        return (
            f"Library cleared: all {total} works deleted "
            f"(sources and embeddings also cleared)."
        )

    ids = [i.strip() for i in work_ids.split(",") if i.strip()]
    if not ids:
        return "Error: no valid work IDs provided"

    deleted = library.delete_works(ids)
    remaining = library.get_work_count()
    return f"Deleted {deleted} work(s) from library. Remaining: {remaining:,}"


async def library_generate_embeddings(library: LibraryManager, embedding_client) -> str:
    """Batch-generate Embedding-3 vectors for all works missing one.

    Useful after a large bulk import (before the first semantic search).
    Subsequent ``library_query`` calls auto-top-up any new works, so
    this tool is optional once the library is seeded.

    持锁通过 ensure_library_embeddings 内部控制。
    """
    from .search import ensure_library_embeddings

    total, embedded = await ensure_library_embeddings(library, embedding_client)
    if total == 0:
        return "Library is empty. Nothing to embed."
    if embedded == 0:
        return f"All {total:,} works already have embeddings. Nothing to do."
    return f"Generated embeddings for {embedded:,} of {total:,} works."
