"""Tool registration + MCP entry point.

Registers all MCP tools on a FastMCP instance.  Each tool delegates to a
standalone function in ``local/`` or ``remote/``, wired through the singletons
managed by ``local/__init__.py`` and ``remote/__init__.py``.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import sys

from dotenv import load_dotenv
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "openalex",
    instructions=(
        "Literature research server over OpenAlex (250M+ academic papers). "
        "TRIGGER this server automatically whenever the task involves finding, "
        "searching, screening, or citing academic papers / literature / "
        "references, building a bibliography or related-work section, checking "
        "research progress or novelty, or analyzing citation networks — "
        "文献检索、找论文、查文献资料、配参考文献、写综述、调研研究进展、引文分析。\n"
        "OpenAlex 学术文献检索 MCP 服务器。\n"
        "工作流：\n"
        "1. search_keyword / search_semantic / search_ids → 搜索文献，结果自动入库 SQLite\n"
        "   用户询问具体操作、方法参数或正文细节时，设置 fetch_fulltext=true；"
        "默认不要抓全文。随后用 library_query 的 substr(fulltext, 1, N) 取适量证据。\n"
        "2. library_query → 通用 SQLite SQL 查询（SELECT name FROM sqlite_master 探索结构；"
        "PRAGMA table_info(table) 查看列）。\n"
        "   语义检索：SQL 中用 ``{query_vec}`` 占位，传入 ``semantic_query`` 自动嵌入替换。\n"
        "   示例：``SELECT *, vec_distance_cosine(vec, {query_vec}) AS score "
        "FROM works WHERE vec IS NOT NULL ORDER BY score LIMIT 30``\n"
        "3. 迭代 library_query → 根据结果调整 SQL/filters/LIMIT\n"
        "4. library_export → 将精选论文导出为 BibTeX 文件\n"
        "5. library_stats → 查看库统计概览\n"
        "6. library_delete → 从库中删除论文\n"
        "7. download_pdf → 下载 OA PDF 论文\n"
        "   严格规则：除非用户明确要求“保存到项目目录”，否则必须保持 "
        "save_to_project=false，PDF 只能写入 ~/.AI-CACHE/openalex/pdfs/。"
        "不得根据上下文、Agent 偏好或任意路径推断项目目录需求。\n"
        "8. autocomplete → 快速获取作者/期刊/机构/出版商/资助方 ID\n"
        "9. graph_analyze / graph_neighbors / graph_visualize → 对选中的一批论文\n"
        "   做引文网络分析(work_ids 必填,先用 library_query 语义检索选出论文)\n"
        "10. fetch_elsevier_abstracts → 用 Elsevier API 按 DOI/库内 ID 获取并回填摘要\n"
        "11. literature_review_prompt → 在用户已于会话提供 BibTeX，或外层模型已读取\n"
        "    本地 .bib 文件时，取得文献综述写作提示词，并由外层模型完成综述"
    ),
)


# ---------------------------------------------------------------------------
# Helpers (ex server.py)
# ---------------------------------------------------------------------------


def _async_close(coro) -> None:
    """Run an async close coroutine synchronously (best-effort)."""
    try:
        import asyncio

        loop = asyncio.new_event_loop()
        loop.run_until_complete(coro)
        loop.close()
    except Exception:
        pass


def _cleanup():
    """Close HTTP clients and SQLite connection on exit."""
    try:
        from openalex_mcp.remote import get_client, get_embed, get_elsevier

        client = get_client()
        if client is not None:
            _async_close(client.close())
        embed = get_embed()
        if embed is not None:
            _async_close(embed.close())
        elsevier = get_elsevier()
        if elsevier is not None:
            _async_close(elsevier.close())
    except Exception:
        pass

    try:
        from openalex_mcp.local import get_library

        lib = get_library()
        if lib is not None:
            lib.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_all(mcp: FastMCP) -> None:
    """Register all MCP tools on the given FastMCP instance."""

    from openalex_mcp.local import (
        get_library,
        library_delete,
        library_export,
        library_generate_embeddings,
        library_stats,
    )
    from openalex_mcp.remote import (
        autocomplete,
        download_pdf,
        get_client,
        get_embed,
        get_elsevier,
        normalize_doi,
        search_keyword,
        search_semantic,
        search_ids,
    )
    from openalex_mcp.review import literature_review_prompt

    # ------------------------------------------------------------------
    # search_keyword
    # ------------------------------------------------------------------

    @mcp.tool(name="search_keyword")
    async def _search_keyword(
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
    ) -> str:
        """Search academic papers by keyword across 250M+ OpenAlex works.

        TRIGGER automatically whenever the user asks to 检索/查找/搜集相关文献
        或资料, find/search/review papers, references, related work, or surveys
        by topic, keyword, author, journal, institution, or funder. Prefer this
        over generic web search when scholarly sources are needed, and call it
        before answering the research question. For paragraph-length research
        ideas or conceptual matching prefer ``search_semantic``; when the user
        already gives DOIs or OpenAlex IDs use ``search_ids`` instead.

        Results are automatically added to the local library (UPSERT by work ID).
        Use ``library_query`` to explore results with SQL.

        The ``query`` parameter supports Boolean operators (AND, OR, NOT —
        UPPERCASE), quoted phrases ("..."), and wildcards (machin*).
        Search targets title, abstract, and fulltext.  Defaults to
        ``publication_year ``">2021"``.

        **Leave ``query`` empty** to fetch ALL works matching only the ID
        filters — no full-text keyword needed.  Combine with
        ``publication_year`` or ``cited_by_count`` to narrow the result set.

        Use ``autocomplete`` first to resolve author / source / institution /
        publisher / funder names to their OpenAlex IDs, then pass those IDs
        to the corresponding filter parameters below.

        Args:
            query: Search text (optional — omit to list all works matching
                only the filters below). Supports Boolean, phrases, wildcards.
            publication_year: Year or range (default ">2021"). An empty
                value means no year filter.
            cited_by_count: Citation count threshold, e.g. ">50".
            cites: Find works that cite these OpenAlex IDs.
            cited_by: Find works cited by these OpenAlex IDs.
            related_to: Find works related to these OpenAlex IDs.
            source_id: Source (journal) OpenAlex ID, e.g. "S4210208519".
            institution_id: Institution OpenAlex ID, e.g. "I129432676".
            author_id: Author OpenAlex ID, e.g. "A5023888391".
            publisher_id: Publisher OpenAlex ID, e.g. "P4310319901".
            funder_id: Funder OpenAlex ID, e.g. "F4320306076".
            page: Page number (1-based, 100 results per page).
            fetch_fulltext: Enable only for questions requiring methods,
                parameters, operational details, or other full-text evidence.
            fulltext_limit: Maximum PDFs converted to database text (default 2).
            use_openalex_content_api: Allow the paid Content API fallback.

        Returns:
            Summary string with result count and preview.
        """
        try:
            result = await search_keyword(
                get_client(),
                query=query,
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
                page=page,
                fetch_fulltext=fetch_fulltext,
                fulltext_limit=fulltext_limit,
                use_openalex_content_api=use_openalex_content_api,
            )
        except Exception as e:
            logger.warning("search_keyword failed: %s", e)
            return (
                f"搜索失败: OpenAlex API 调用出错 ({type(e).__name__}: {e})。"
                "这通常是网络波动或服务临时不可用,**不是永久失效**。"
                "建议稍后重试,或先用 library_query 查看已有库内结果。"
            )
        return result["summary"]

    # ------------------------------------------------------------------
    # search_semantic
    # ------------------------------------------------------------------

    @mcp.tool(name="search_semantic")
    async def _search_semantic(
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
    ) -> str:
        """Search academic papers by meaning — AI-powered semantic search.

        TRIGGER when the user describes a research idea, problem, draft
        paragraph, or abstract and wants conceptually related papers
        (按研究思路/想法/主题找语义相关文献, find papers similar to this
        idea/abstract), even without exact keywords. For exact terms, author
        names, or venue strings prefer ``search_keyword``; for known DOIs or
        IDs use ``search_ids``.

        Results are automatically added to the local library (UPSERT by work ID).
        Use ``library_query`` to explore results with SQL.

        This uses OpenAlex's server-side semantic search. It is separate from
        local vector search in ``library_query``, which requires ZhipuAI.

        Best for paragraph-length queries (research descriptions, abstracts).
        Matches by conceptual meaning, not exact keywords.  Max **50 results**
        per page, rate-limited to 1 request per second.  Defaults to
        ``publication_year ``">2021"``.

        Use ``autocomplete`` first to resolve author / source / institution /
        publisher / funder names to their OpenAlex IDs, then pass those IDs
        to the corresponding filter parameters below.

        Args:
            query: Natural-language query describing your research topic
                (up to 2,000 characters).
            publication_year: Year or range (default ">2021"). An empty
                value means no year filter.
            cited_by_count: Citation count threshold, e.g. ">50".
            cites: Find works that cite these OpenAlex IDs.
            cited_by: Find works cited by these OpenAlex IDs.
            related_to: Find works related to these OpenAlex IDs.
            source_id: Source (journal) OpenAlex ID, e.g. "S4210208519".
            institution_id: Institution OpenAlex ID, e.g. "I129432676".
            author_id: Author OpenAlex ID, e.g. "A5023888391".
            publisher_id: Publisher OpenAlex ID, e.g. "P4310319901".
            funder_id: Funder OpenAlex ID, e.g. "F4320306076".
            page: Page number (1-based, 50 results per page).
            fetch_fulltext: Enable only for questions requiring methods,
                parameters, operational details, or other full-text evidence.
            fulltext_limit: Maximum PDFs converted to database text (default 2).
            use_openalex_content_api: Allow the paid Content API fallback.

        Returns:
            Summary string with result count and preview.
        """
        try:
            result = await search_semantic(
                get_client(),
                query=query,
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
                page=page,
                fetch_fulltext=fetch_fulltext,
                fulltext_limit=fulltext_limit,
                use_openalex_content_api=use_openalex_content_api,
            )
        except Exception as e:
            logger.warning("search_semantic failed: %s", e)
            return (
                f"语义搜索失败: OpenAlex API 调用出错 ({type(e).__name__}: {e})。"
                "这通常是网络波动或服务临时不可用,**不是永久失效**。"
                "建议稍后重试,或先用 search_keyword / library_query 继续工作。"
            )
        return result["summary"]

    # ------------------------------------------------------------------
    # search_ids
    # ------------------------------------------------------------------

    @mcp.tool(name="search_ids")
    async def _search_ids(
        query: str,
        fetch_fulltext: bool = False,
        fulltext_limit: int = 2,
        use_openalex_content_api: bool = False,
    ) -> str:
        """Fetch specific papers by OpenAlex ID or DOI.

        TRIGGER when the user provides one or more DOIs / OpenAlex IDs, or
        points at specific known papers (按 DOI 获取论文、查指定的某几篇文献)
        — no filters, no pagination. For topic-based discovery use
        ``search_keyword`` / ``search_semantic`` instead.

        Results are automatically added to the local library (UPSERT by work ID).
        Use ``library_query`` to explore results with SQL.

        Args:
            query: Comma-separated OpenAlex IDs and/or DOIs.
                e.g. ``"W2741809807,W2100837269"`` or
                ``"10.1016/xxx,10.1038/yyy"``. OpenAlex and DOI URLs,
                plus ``doi:`` prefixes, are also accepted.
            fetch_fulltext: Enable for a named paper when the user asks for
                methodological or operational details beyond its abstract.
            fulltext_limit: Maximum PDFs converted to database text (default 2).
            use_openalex_content_api: Allow the paid Content API fallback.

        Returns:
            Summary string with result count and preview.
        """
        try:
            result = await search_ids(
                get_client(),
                query=query,
                fetch_fulltext=fetch_fulltext,
                fulltext_limit=fulltext_limit,
                use_openalex_content_api=use_openalex_content_api,
            )
        except ValueError as e:
            return f"Error: {e}"
        return result["summary"]

    # ------------------------------------------------------------------
    # autocomplete
    # ------------------------------------------------------------------

    @mcp.tool(name="autocomplete")
    async def _autocomplete(
        entity_type: str,
        query: str,
    ) -> str:
        """Resolve names to OpenAlex entity IDs — fast typeahead (~200ms).

        TRIGGER whenever the user names a specific author, journal,
        institution, publisher, or funder (指定作者/期刊/机构/出版商/资助方)
        and you need its OpenAlex ID for search filters.

        Use this **before** ``search_keyword`` / ``search_semantic`` to resolve
        author, journal, institution, publisher, or funder names to their
        OpenAlex IDs.  Then pass the ID to the corresponding filter parameter
        of the search tool.

        Returns up to 10 results.  Autocomplete is free (not billed as a search).

        Args:
            entity_type: One of ``"authors"``, ``"sources"``, ``"institutions"``,
                ``"publishers"``, ``"funders"``.
            query: Name fragment to search for (e.g. ``"Northwestern"``,
                ``"MSSP"``, ``"Gates"``, ``Yinzhong Yan``).

        Returns:
            Compact list of matching entities with IDs and metadata.
        """
        return await autocomplete(
            get_client(),
            entity_type=entity_type,
            query=query,
        )

    # ------------------------------------------------------------------
    # download_pdf
    # ------------------------------------------------------------------

    @mcp.tool(name="download_pdf")
    async def _download_pdf(
        work_ids: str,
        save_to_project: bool = False,
    ) -> str:
        """Download open-access paper PDFs for given OpenAlex work IDs.

        TRIGGER when the user asks to 下载论文/PDF/全文, download or save
        paper PDF files. Uses the OpenAlex Content API.

        Uses ``content.openalex.org/works/{id}.pdf`` — the official OpenAlex
        content endpoint.  Each download costs **$0.01**; free tier allows
        ~100 PDFs per day.

        **STRICT RULE:** PDFs must be saved to
        ``~/.AI-CACHE/openalex/pdfs/`` by default. Leave
        ``save_to_project=false`` unless the user explicitly asks to save
        PDFs in the project directory. Never infer this from context, an
        Agent preference, or a requested arbitrary path. When explicitly
        requested, ``save_to_project=true`` saves only to the fixed
        ``<project>/pdfs/`` directory; arbitrary output paths are impossible.
        Already-downloaded files are skipped to avoid unnecessary charges.

        **Pre-requisite:** Configure an OpenAlex API key via the
        ``OPENALEX_API_KEY`` env var (or ``.env`` file).  Get a free key at
        https://openalex.org/settings/api.

        Args:
            work_ids: Comma-separated OpenAlex work IDs, e.g.
                ``"W2741809807,W3038568908"``.
            save_to_project: Set to ``True`` only after the user explicitly
                requests saving PDFs in the project directory.

        Returns:
            Markdown summary table with status (downloaded / skipped /
            no-content / failed), title, and file path for each work.
        """
        return await download_pdf(
            get_client(),
            work_ids=work_ids,
            save_to_project=save_to_project,
        )

    # ------------------------------------------------------------------
    # fetch_elsevier_abstracts
    # ------------------------------------------------------------------

    @mcp.tool(name="fetch_elsevier_abstracts")
    async def _fetch_elsevier_abstracts(
        query: str,
        input_type: str = "doi",
        update_library: bool = True,
        overwrite: bool = False,
    ) -> str:
        """Fetch missing paper abstracts from Elsevier by DOI or local work ID.

        TRIGGER when retrieved papers lack abstracts and the user wants fuller
        summaries (补全/回填文献摘要).

        Requires ``ELSEVIER_API_KEY``. Uses Elsevier's Abstract Retrieval API
        and returns the full abstract text when available. If ``update_library``
        is true, fetched abstracts are written back into ``works.abstract``.
        Existing local abstracts are preserved unless ``overwrite`` is true.

        Args:
            query: Comma-separated DOI values or local OpenAlex work IDs.
            input_type: ``"doi"`` (default) or ``"work_id"``.
            update_library: Fill matching empty local abstracts (default true).
            overwrite: Replace existing local abstracts (default false).

        Returns:
            Markdown report with each fetched abstract and update status.
        """
        elsevier = get_elsevier()
        if elsevier is None:
            return (
                "ELSEVIER_API_KEY not configured. Set it to enable "
                "Elsevier Abstract Retrieval. Optional: set ELSEVIER_INST_TOKEN "
                "if your institution requires one."
            )

        mode = (input_type or "doi").strip().lower()
        raw_values = [v.strip() for v in query.split(",") if v.strip()]
        if not raw_values:
            return "Error: query is empty. Provide comma-separated DOIs or work IDs."
        if len(raw_values) > 25:
            return "Error: fetch at most 25 Elsevier abstracts per tool call."

        library = get_library()
        items: list[dict] = []
        skipped: list[str] = []

        if mode in ("work_id", "work_ids", "id", "ids"):
            from openalex_mcp.common import short_openalex_id

            work_ids = [short_openalex_id(v) for v in raw_values]
            rows = library.get_works_batch(work_ids)
            by_id = {row.get("id"): row for row in rows}
            for wid in work_ids:
                row = by_id.get(wid)
                if row is None:
                    skipped.append(f"`{wid}`: not found in local library")
                    continue
                doi = normalize_doi(row.get("doi"))
                if not doi:
                    skipped.append(f"`{wid}`: no DOI stored in local library")
                    continue
                items.append(
                    {
                        "label": wid,
                        "work_id": wid,
                        "doi": doi,
                        "had_abstract": bool(row.get("abstract")),
                    }
                )
        elif mode in ("doi", "dois"):
            for value in raw_values:
                doi = normalize_doi(value)
                if not doi:
                    skipped.append(f"`{value}`: invalid DOI")
                    continue
                items.append(
                    {
                        "label": doi,
                        "work_id": None,
                        "doi": doi,
                        "had_abstract": None,
                    }
                )
        else:
            return 'Error: input_type must be "doi" or "work_id".'

        if not items:
            lines = ["No Elsevier abstracts fetched."]
            if skipped:
                lines.append("")
                lines.append("Skipped:")
                lines.extend(f"- {msg}" for msg in skipped)
            return "\n".join(lines)

        rows_out: list[dict] = []
        updated_total = 0
        for item in items:
            try:
                record = await elsevier.fetch_abstract_by_doi(item["doi"])
                abstract = record.get("abstract") or ""
                update_note = "not requested"
                if update_library and abstract:
                    async with library._get_lock():
                        if item["work_id"]:
                            updated = library.update_work_abstract(
                                item["work_id"], abstract, overwrite=overwrite
                            )
                            updated_total += 1 if updated else 0
                            if updated:
                                update_note = "updated local abstract"
                            elif item["had_abstract"] and not overwrite:
                                update_note = "skipped: local abstract already exists"
                            else:
                                update_note = "skipped: no matching empty local row"
                        else:
                            updated_count = library.update_work_abstract_by_doi(
                                item["doi"], abstract, overwrite=overwrite
                            )
                            updated_total += updated_count
                            update_note = (
                                f"updated {updated_count} local row(s)"
                                if updated_count
                                else "skipped: no matching empty local row"
                            )
                elif update_library and not abstract:
                    update_note = "skipped: Elsevier returned no abstract"

                rows_out.append(
                    {
                        "label": item["label"],
                        "doi": record.get("doi") or item["doi"],
                        "title": record.get("title") or "",
                        "source": record.get("publication_name") or "",
                        "abstract": abstract,
                        "status": "found" if abstract else "no abstract returned",
                        "update": update_note,
                    }
                )
            except FileNotFoundError:
                rows_out.append(
                    {
                        "label": item["label"],
                        "doi": item["doi"],
                        "title": "",
                        "source": "",
                        "abstract": "",
                        "status": "not found in Elsevier",
                        "update": "not updated",
                    }
                )
            except Exception as e:
                logger.warning(
                    "fetch_elsevier_abstracts failed for %s: %s",
                    item["doi"],
                    e,
                )
                rows_out.append(
                    {
                        "label": item["label"],
                        "doi": item["doi"],
                        "title": "",
                        "source": "",
                        "abstract": "",
                        "status": f"error: {type(e).__name__}: {e}",
                        "update": "not updated",
                    }
                )

        found = sum(1 for row in rows_out if row["abstract"])
        lines = [
            "## Elsevier Abstract Results",
            "",
            f"Fetched abstracts: **{found}** of **{len(items)}**",
            f"Local rows updated: **{updated_total}**",
        ]
        if skipped:
            lines.append("")
            lines.append("Skipped:")
            lines.extend(f"- {msg}" for msg in skipped)

        for row in rows_out:
            lines.append("")
            lines.append(f"### {row['label']}")
            lines.append(f"- DOI: `{row['doi']}`")
            lines.append(f"- Status: {row['status']}")
            lines.append(f"- Library: {row['update']}")
            if row["title"]:
                lines.append(f"- Title: {row['title']}")
            if row["source"]:
                lines.append(f"- Source: {row['source']}")
            if row["abstract"]:
                lines.append("")
                lines.append(row["abstract"])

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # library_generate_embeddings
    # ------------------------------------------------------------------

    @mcp.tool(name="library_generate_embeddings")
    async def _library_generate_embeddings() -> str:
        """Batch-generate embedding vectors for all library works missing one.

        TRIGGER after a large search/import batch to enable full-library
        local semantic search (为库内文献生成向量、启用语义检索).

        Call this once after importing a large batch of works to enable
        full-library semantic search.  Requires ``ZHIPUAI_API_KEY``.
        Subsequent ``library_query`` calls auto-top-up new works, so
        this tool is optional once the library is seeded.

        **Tip:** use ``library_stats`` before and after to see the
        embedding coverage improvement.

        Returns:
            Summary with total works and how many were newly embedded.
        """
        embed = get_embed()
        if embed is None:
            return (
                "ZHIPUAI_API_KEY not configured. "
                "Set it to enable Embedding-3 semantic search."
            )
        return await library_generate_embeddings(get_library(), embed)

    # ------------------------------------------------------------------
    # library_export
    # ------------------------------------------------------------------

    @mcp.tool(name="library_export")
    async def _library_export(
        work_ids: str = "*",
        target: str = "export.bib",
        sort: str | None = None,
        cite_key_style: str = "author_year",
    ) -> str:
        """Export screened papers from the library to a BibTeX file.

        TRIGGER when the user wants a reference list, citation export, or a
        .bib file for Zotero / EndNote / JabRef (导出参考文献、生成 BibTeX).

        Generates BibTeX entries directly from library columns and saves
        them to ``~/.AI-CACHE/openalex/collections/{target}``.

        Use this as the FINAL step — after iteratively screening with
        ``library_query`` and confirming the papers you want.

        Args:
            work_ids: Comma-separated work IDs (e.g.
                ``"W123,W456,W789"``) or ``"*"`` to export all works.
            target: Target .bib filename (default ``"export.bib"``).
                Must be a single filename and is always stored in the
                ``collections/`` directory.
            sort: Single ``column[:asc|desc]`` expression. Allowed columns:
                id, title, publication_year, publication_date,
                cited_by_count, source_name, is_oa.
            cite_key_style: ``"author_year"`` (default, e.g. ``liu2024``)
                or ``"openalex_id"`` for legacy keys.

        Returns:
            Confirmation message with export path and entry count.
        """
        lib = get_library()
        return library_export(
            lib,
            work_ids=work_ids,
            target=target,
            sort=sort,
            cite_key_style=cite_key_style,
        )

    # ------------------------------------------------------------------
    # literature_review_prompt
    # ------------------------------------------------------------------

    @mcp.tool(name="literature_review_prompt")
    async def _literature_review_prompt() -> str:
        """Return the system prompt for a BibTeX-grounded literature review.

        TRIGGER when the user asks to write a literature review / related-work
        section from BibTeX (根据 BibTeX 写综述、文献回顾、研究现状).

        This tool deliberately has no BibTeX argument and does not call an LLM.
        The outer model must read the BibTeX supplied in the conversation or a
        local .bib export, then follow the returned prompt to write the review.
        This preserves a single source of truth for citation keys and keeps the
        final prose generation under the outer model's control.

        Returns:
            Chinese system prompt that specifies the review structure, method
            progression, citation format, and evidence boundary.
        """
        return literature_review_prompt()

    # ------------------------------------------------------------------
    # library_stats
    # ------------------------------------------------------------------

    @mcp.tool(name="library_stats")
    async def _library_stats() -> str:
        """Show an overview of the local literature library.

        TRIGGER when the user asks what has been collected so far
        (库里有哪些文献/文献库概况), or before screening to plan queries.

        Returns Markdown with total works, year range, abstract and
        embedding coverage, open-access rate, top 10 sources, and
        top 10 concepts.

        Use this to understand what's in your library before searching.

        Returns:
            Markdown with library statistics and top sources/concepts.
        """
        lib = get_library()
        return library_stats(lib)

    # ------------------------------------------------------------------
    # library_delete
    # ------------------------------------------------------------------

    @mcp.tool(name="library_delete")
    async def _library_delete(
        work_ids: str,
    ) -> str:
        """Delete works from the local library.

        DESTRUCTIVE — call ONLY on an explicit user delete request; never
        call proactively during a literature workflow.

        Removes works by ID from the library.  Embeddings are stored
        in the works table (vec column) and are cleaned up automatically.

        **⚠️ 谨慎使用**:删除不可恢复。除非用户**明确要求**删除,否则 AI 不应
        调用本工具——尤其是 ``work_ids="*"`` 会清空整个库(含论文、来源、
        全部嵌入向量)。没有用户明确的删库指令时,AI 不要自行调用。

        Args:
            work_ids: Comma-separated work IDs (e.g.
                ``"W123,W456,W789"``) or ``"*"`` to delete ALL works
                (clears the entire library including sources and
                embeddings).

        Returns:
            Confirmation message with deletion count and remaining
            total.
        """
        lib = get_library()
        async with lib._get_lock():
            return library_delete(lib, work_ids=work_ids)

    # ------------------------------------------------------------------
    # library_close
    # ------------------------------------------------------------------

    @mcp.tool(name="library_close")
    async def _library_close() -> str:
        """Close the local library connection and release the file lock.

        Maintenance only — call when another program needs the database
        file; not part of a normal literature-search workflow.

        After closing, any subsequent tool that accesses the library will
        automatically reconnect.  Use this when you need another program
        to access the library database file without lock conflicts.

        Returns:
            Confirmation that the connection has been closed.
        """
        lib = get_library()
        if lib is None:
            return "Library was not open."
        # Acquire the lock so we don't close the connection while another
        # tool is mid-operation.
        async with lib._get_lock():
            lib.close()
        return "Library connection closed. File lock released."

    # ------------------------------------------------------------------
    # graph_analyze
    # ------------------------------------------------------------------

    @mcp.tool(name="graph_analyze")
    async def _graph_analyze(
        work_ids: str,
    ) -> str:
        """Analyze the citation network among a selected set of papers.

        TRIGGER when the user asks about citation relationships, who cites
        whom, core/classic papers, or research clusters among collected
        literature (引文网络分析、引用关系、找关键文献/文献簇).

        Builds a **directed citation graph** over the given works — node =
        paper, edge ``A → B`` means A cites B.  Only works in ``work_ids``
        become nodes; only citations where BOTH papers are in the list are
        kept (induced subgraph).

        **work_ids 必填**:不填/为空会报错。请先用 ``library_query`` 做本地
        语义检索选出目标论文,再把它们的 ID 传进来。例如:

        ``SELECT id FROM works WHERE vec IS NOT NULL
        ORDER BY vec_distance_cosine(vec, {query_vec}) LIMIT 100``

        返回 PageRank、被引最多(入度)、引用最多(出度)、中介中心度、
        社区划分(密集互引的论文簇)等结构化分析。

        Args:
            work_ids: Comma-separated OpenAlex work IDs (required),
                e.g. ``"W123,W456,W789"``.

        Returns:
            Markdown analysis of the citation network.
        """
        from openalex_mcp.graph import graph_analyze

        return graph_analyze(get_library(), work_ids=work_ids)

    # ------------------------------------------------------------------
    # graph_neighbors
    # ------------------------------------------------------------------

    @mcp.tool(name="graph_neighbors")
    async def _graph_neighbors(
        work_ids: str,
        direction: str = "both",
    ) -> str:
        """Show the citation neighbors (who cites it / whom it cites) of papers.

        TRIGGER when the user asks 某篇文献被谁引用、它引用了谁, or wants the
        local citation context of specific papers.

        For each requested work, lists who cites it (in / predecessors) and
        whom it cites (out / successors), within the selected set.

        **work_ids 必填**:同 ``graph_analyze``,先用 ``library_query`` 语义
        检索选论文。方向语义:``in`` = 谁引用了它;``out`` = 它引用了谁;
        ``both`` = 两者都列(默认)。

        Args:
            work_ids: Comma-separated OpenAlex work IDs (required).
            direction: ``"in"`` | ``"out"`` | ``"both"`` (default ``"both"``).

        Returns:
            Markdown listing each work's citation neighbors.
        """
        from openalex_mcp.graph import graph_neighbors

        return graph_neighbors(
            get_library(), work_ids=work_ids, direction=direction
        )

    # ------------------------------------------------------------------
    # graph_visualize
    # ------------------------------------------------------------------

    @mcp.tool(name="graph_visualize")
    async def _graph_visualize(
        work_ids: str,
        output_dir: str | None = None,
    ) -> str:
        """Generate an interactive HTML visualization of the citation network.

        TRIGGER when the user asks to 可视化/画出引文网络图, or wants a visual
        map of how collected papers cite each other.

        Renders the selected works as an interactive graph (vis.js) saved to
        ``~/.AI-CACHE/openalex/graphs/`` — open the returned path in a browser.

        Visual encoding:
        - **color** = publication year (light = older, dark = newer)
        - **label** = first-author surname + year on prominent/hovered nodes
        - **size** = cited-by count (robust log-scaled)
        - **edge brightness** = local citation-structure strength
        - short force-directed settling animation, then physics auto-freezes
        - click for amber highlight; hover syncs the right detail panel

        **work_ids 必填**:同 ``graph_analyze``。建议 30~120 篇。超过 120 篇时，
        可视化会按被引量、PageRank、集合内被引用次数和奠基性年份自动截取重要论文；
        完整集合分析仍可用 ``graph_analyze``。

        Args:
            work_ids: Comma-separated OpenAlex work IDs (required).
            output_dir: Optional output directory.  Defaults to
                ``~/.AI-CACHE/openalex/graphs/``.

        Returns:
            Absolute path of the generated HTML file.
        """
        from openalex_mcp.graph import graph_visualize

        return graph_visualize(
            get_library(), work_ids=work_ids, output_dir=output_dir
        )

    # ------------------------------------------------------------------
    # library_query
    # ------------------------------------------------------------------

    @mcp.tool(name="library_query")
    async def _library_query(
        sql: str,
        semantic_query: str | None = None,
    ) -> str:
        """Query the local literature library with read-only SQL.

        TRIGGER after any search to screen, filter, rank, or semantically
        search the collected papers (从已检索文献库中筛选/排序/语义检索, e.g.
        top-N by citations, year/journal/author filters, substr(fulltext,1,N)
        evidence extraction).

        This is the **universal query tool** — freely explore schema and
        run ad-hoc queries.  Use it for BOTH plain SQL and semantic search:

        - **Explore schema**: ``SELECT name FROM sqlite_master WHERE type='table'``;
          ``PRAGMA table_info(<table>)``
        - **Count / filter**: ``SELECT count(*) FROM works WHERE ...``
        - **Plain queries**: read-only SQLite SELECT / WITH / EXPLAIN queries —
          joins, aggregations, window functions, etc.
        - **Semantic search**: write SQL with ``{query_vec}`` placeholder,
          pass ``semantic_query`` to auto-embed and inject the vector as
          a float32 hex BLOB literal (``X'...'``).

        **Semantic search example** (full-library):
        ``SELECT *, vec_distance_cosine(vec, {query_vec}) AS score
        FROM works WHERE vec IS NOT NULL ORDER BY score LIMIT 30``

        **WHERE-filtered semantic search**:
        ``SELECT *, vec_distance_cosine(vec, {query_vec}) AS score
        FROM works WHERE publication_year > 2020 AND is_oa = true
        AND vec IS NOT NULL ORDER BY score LIMIT 50``

        **Schema**:
        ``works`` — id, doi, title, publication_year,
        publication_date, type, cited_by_count, is_oa, oa_status,
        has_content, abstract, language, source_id, source_name,
        authors_json, concepts_json, keywords_json, referenced_works,
        related_works, raw_json, vec (BLOB, float32[1024]).
        ``sources`` — id, display_name, host_organization, issn_l,
        issn, type, alternate_titles, abbreviated_title, homepage,
        works_count, cited_by_count, is_oa, is_in_doaj, topic.

        WAL mode allows concurrent reads + writes — ``library_query``
        does not block ``search_keyword`` / ``search_ids`` and vice versa.
        Database-level read-only enforcement rejects mutations even when they
        are hidden inside a CTE. Use LIMIT and bounded ``substr()`` expressions
        when selecting large columns such as fulltext or raw_json.

        Args:
            sql: SQLite SQL (read-only). Use ``{query_vec}`` placeholder
                for semantic embedding vector.
            semantic_query: Natural-language text to embed and inject
                at ``{query_vec}``. Requires ``ZHIPUAI_API_KEY``.

        Returns:
            JSON array of row objects.
        """
        library = get_library()

        # --- Reject non-read-only SQL ---
        sql_stripped = sql.strip().upper()
        if not any(sql_stripped.startswith(kw) for kw in
                   ("SELECT", "PRAGMA", "WITH", "EXPLAIN")):
            return json.dumps(
                {
                    "error": (
                        "Only read-only queries are allowed "
                        "(SELECT, PRAGMA, WITH, EXPLAIN). "
                        "Use library_delete, search_keyword, search_ids, or "
                        "library_generate_embeddings to modify the library."
                    )
                },
                ensure_ascii=False,
            )

        # --- Semantic vector injection (embedding happens outside lock) ---
        if semantic_query is not None:
            if "{query_vec}" not in sql:
                return json.dumps(
                    {
                        "error": (
                            "semantic_query was provided but sql has no "
                            "{query_vec} placeholder. Add {query_vec} "
                            "to the SQL where the embedding vector should go."
                        )
                    },
                    ensure_ascii=False,
                )

            from openalex_mcp.remote import get_embed

            embedding_client = get_embed()
            if embedding_client is None:
                return json.dumps(
                    {
                        "error": (
                            "ZHIPUAI_API_KEY not configured. "
                            "Set it to enable Embedding-3 semantic search."
                        )
                    },
                    ensure_ascii=False,
                )

            from openalex_mcp.local.search import ensure_library_embeddings

            # Top-up missing embeddings (non-fatal for reads).
            try:
                await ensure_library_embeddings(library, embedding_client)
            except Exception as e:
                logger.warning(
                    "Embedding top-up failed (proceeding without it): %s", e
                )

            try:
                query_vec = await embedding_client.embed(semantic_query[:3000])
            except Exception as e:
                logger.warning("Semantic embedding failed: %s", e)
                return json.dumps(
                    {
                        "error": (
                            "语义检索暂时失败: 嵌入服务(智谱 Embedding API)调用出错 "
                            f"({type(e).__name__}: {e})。这通常是网络波动或服务临时不可用,"
                            "**不是永久失效**。建议稍后重试,或先用普通 SQL 查询"
                            "(`library_query` 不带 semantic_query)继续工作。"
                        )
                    },
                    ensure_ascii=False,
                )
            # Inject as float32 hex BLOB literal compatible with
            # sqlite-vec scalar functions (vec_distance_cosine, etc.)
            import numpy as np

            query_vec_bytes = np.array(query_vec, dtype=np.float32).tobytes()
            hex_blob = "X'" + query_vec_bytes.hex() + "'"
            sql = sql.replace("{query_vec}", hex_blob)

        # No asyncio.Lock needed for reads — WAL allows concurrent readers
        try:
            result = library.execute_readonly(sql)
        except Exception as e:
            return json.dumps(
                {"error": f"Read-only query rejected: {e}"},
                ensure_ascii=False,
            )
        if result.description is None:
            return json.dumps(
                {"message": "Query executed successfully."},
                ensure_ascii=False,
            )
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        return json.dumps(
            [dict(zip(columns, row)) for row in rows],
            ensure_ascii=False,
            default=str,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Start the MCP server."""
    from openalex_mcp.local import LibraryManager, set_library
    from openalex_mcp.remote import (
        ElsevierClient,
        EmbeddingClient,
        OpenAlexClient,
        set_client,
        set_elsevier,
        set_embed,
    )

    env_file = os.environ.get("OPENALEX_MCP_ENV_FILE", "").strip()
    load_dotenv(dotenv_path=env_file or None)

    # --- Validate API key ---
    api_key = os.environ.get("OPENALEX_API_KEY", "")
    email = os.environ.get("OPENALEX_EMAIL", "").strip()
    if not api_key or api_key == "your-api-key-here":
        print(
            "警告: OPENALEX_API_KEY 未设置。请设置环境变量或在 .env 文件中配置。\n"
            "获取免费 API key: https://openalex.org/settings/api",
            file=sys.stderr,
        )

    zhipu_key = os.environ.get("ZHIPUAI_API_KEY", "")
    if not zhipu_key or zhipu_key == "your-zhipuai-key-here":
        print(
            "提示: ZHIPUAI_API_KEY 未设置。library_query 语义检索不可用，SQL 查询仍可正常使用。\n"
            "设置后可使用 Embedding-3 语义搜索。获取 key: https://bigmodel.cn",
            file=sys.stderr,
        )

    elsevier_key = os.environ.get("ELSEVIER_API_KEY", "")
    elsevier_inst_token = os.environ.get("ELSEVIER_INST_TOKEN", "")
    if not elsevier_key or elsevier_key == "your-elsevier-api-key-here":
        print(
            "提示: ELSEVIER_API_KEY 未设置。fetch_elsevier_abstracts 不可用。\n"
            "设置后可用 Elsevier Abstract Retrieval API 按 DOI 获取摘要。",
            file=sys.stderr,
        )

    # --- Create singletons ---
    # Remote
    client = OpenAlexClient(api_key, email=email)
    set_client(client)
    if zhipu_key and zhipu_key != "your-zhipuai-key-here":
        set_embed(EmbeddingClient(zhipu_key))
    if elsevier_key and elsevier_key != "your-elsevier-api-key-here":
        set_elsevier(
            ElsevierClient(
                elsevier_key,
                elsevier_inst_token or None,
            )
        )

    # Local
    lib = LibraryManager()
    set_library(lib)

    # --- Register cleanup, tools, and run ---
    atexit.register(_cleanup)
    register_all(mcp)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
