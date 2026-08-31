"""Upsert orchestration — normalize abstract + upsert works + lazy fetch sources.

Called by remote/search.py via lazy import of get_library.

Restructured to minimise lock duration: network calls (source fetch) happen
BEFORE the asyncio.Lock is acquired, so we don't hold the DB lock during
slow HTTP requests.

Embedding is generated lazily for newly-added works (duplicates keep their
existing vector, if any) so semantic search works right after an import.

The "actually added" count is computed by re-querying ``count_new_ids``
AFTER the write — so it reflects real additions (and thus whether the DB
write succeeded or was locked), not just a pre-write estimate.
"""

import logging

from openalex_mcp.common import extract_source_id, short_openalex_id

from .bibtex import normalize_abstract
from .embedding import build_embedding_text
from .manager import LibraryManager

logger = logging.getLogger(__name__)


async def upsert_works(
    lib: LibraryManager,
    works: list[dict],
) -> tuple[int, int, int]:
    """搜索结果入库编排：normalize abstract + upsert work + lazy fetch source。

    Phase 1 (read, under lock): count new IDs (expected), discover missing sources.
    Phase 2 (network, no lock): fetch missing sources + embed new works.
    Phase 3 (write, under lock): upsert works + sources, store new vectors,
        re-count to get actual additions.

    Returns:
        (actually_added, failed_count, total_after)
        - actually_added: 实际新增进库的条数（写后重统计，反映写入是否成功）
        - failed_count:   upsert 抛异常的条数（诊断用；actually_added 已反映失败）
        - total_after:    操作后库中总条数
    """
    lock = lib._get_lock()
    work_ids = [short_openalex_id(w.get("id", "")) for w in works]

    # ---- Phase 1: read (under lock) ----------------------------------------
    async with lock:
        expected_new = lib.count_new_ids(work_ids)

        # Normalize abstracts inline (no DB).
        for work in works:
            if not work.get("abstract") and work.get("abstract_inverted_index"):
                work["abstract"] = normalize_abstract(work) or ""

        # Discover source IDs we'll need to fetch.
        source_ids: set[str] = set()
        for work in works:
            source_id = extract_source_id(work)
            if source_id:
                source_ids.add(source_id)
        existing_source_ids = lib.get_existing_source_ids(list(source_ids))
        source_ids_to_fetch = source_ids - existing_source_ids

    # ---- Phase 2: network (no lock held) ------------------------------------
    source_data_list: list[dict] = []
    if source_ids_to_fetch:
        from openalex_mcp.remote import get_client  # lazy import

        client = get_client()
        source_ids_to_fetch_sorted = sorted(source_ids_to_fetch)
        try:
            source_data_list = await client.get_sources_batch(source_ids_to_fetch_sorted)
        except Exception:
            for source_id in source_ids_to_fetch_sorted:
                try:
                    source_data = await client.get_source(source_id)
                    if source_data:
                        source_data_list.append(source_data)
                except Exception:
                    pass

    # Embed only the works expected to be new (dedup against existing
    # vectors below). Failures are non-fatal — the vector stays NULL and
    # semantic search / library_generate_embeddings can top it up later.
    new_embeddings: list[tuple[str, list[float]]] = []
    if expected_new > 0:
        from openalex_mcp.remote import get_embed  # lazy import

        embedding_client = get_embed()
        if embedding_client is not None:
            texts: list[str] = []
            ids: list[str] = []
            for work in works:
                wid = short_openalex_id(work.get("id", ""))
                text = build_embedding_text(work)
                if text:
                    texts.append(text)
                    ids.append(wid)
            if texts:
                try:
                    doc_vecs = await embedding_client.embed_batch(texts)
                    new_embeddings = list(zip(ids, doc_vecs))
                except Exception as e:
                    logger.warning(
                        "Embedding of new works failed (vectors left NULL): %s", e
                    )

    # ---- Phase 3: write (under lock) ----------------------------------------
    failed_count = 0
    async with lock:
        for work in works:
            try:
                lib.upsert_work(work)
            except Exception as e:
                failed_count += 1
                logger.warning(
                    "upsert_work failed for %s: %s",
                    short_openalex_id(work.get("id", "")),
                    e,
                )

        for source_data in source_data_list:
            try:
                lib.upsert_source(source_data)
            except Exception as e:
                failed_count += 1
                logger.warning("upsert_source failed: %s", e)

        # Store vectors only for works actually written this round and still
        # lacking one (duplicates keep their existing vector). Re-check via
        # `vec IS NULL` so a concurrently-embedded work isn't overwritten.
        if new_embeddings:
            embed_ids = lib.get_work_ids_without_embeddings(
                [wid for wid, _ in new_embeddings]
            )
            embed_set = set(embed_ids)
            items = [
                (wid, vec) for wid, vec in new_embeddings if wid in embed_set
            ]
            if items:
                lib.store_embeddings_batch(items)

        total_after = lib.get_work_count()
        # Re-count how many of this search's works are still absent — the
        # difference from expected_new is the real number added.
        still_missing = lib.count_new_ids(work_ids)

    actually_added = max(0, expected_new - still_missing)
    return actually_added, failed_count, total_after
