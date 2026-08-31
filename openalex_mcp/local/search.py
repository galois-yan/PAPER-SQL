"""Library embedding helpers — ensure works have vectors for semantic search.

Called by library_generate_embeddings (batch) and library_query (auto-top-up).

Restructured to minimise lock duration: all embedding API calls (network)
happen BEFORE acquiring the asyncio.Lock for DB writes.
"""

from __future__ import annotations

import logging
from typing import Any

from .embedding import build_embedding_text
from .manager import LibraryManager

logger = logging.getLogger(__name__)

EMBED_CHUNK_SIZE = 256  # works per embed+store round (API batches at 64 internally)


# ---------------------------------------------------------------------------
# Embedding coverage
# ---------------------------------------------------------------------------


async def ensure_library_embeddings(
    library: LibraryManager,
    embedding_client: Any,
) -> tuple[int, int]:
    """Embed ALL works in the library that lack a vector.

    Phase 1 (read, under lock): discover works missing embeddings.
    Phase 2 (network, no lock): call the embedding API for all missing works.
    Phase 3 (write, under lock): bulk-store embeddings.

    Returns:
        (total_works, newly_embedded)
    """
    lock = library._get_lock()

    # ---- Phase 1: read (under lock) ----------------------------------------
    async with lock:
        all_ids = library.get_all_work_ids()
        if not all_ids:
            return (0, 0)

        missing = library.get_work_ids_without_embeddings(all_ids)
        if not missing:
            return (len(all_ids), 0)

        works_map = {w["id"]: w for w in library.get_works_batch(missing)}

    # ---- Phase 2: network (no lock held) -----------------------------------
    all_items: list[tuple[str, list[float]]] = []

    for i in range(0, len(missing), EMBED_CHUNK_SIZE):
        chunk_ids = missing[i : i + EMBED_CHUNK_SIZE]
        texts: list[str] = []
        ids: list[str] = []
        for wid in chunk_ids:
            work = works_map.get(wid, {})
            text = build_embedding_text(work)
            if text:
                texts.append(text)
                ids.append(wid)

        if not texts:
            continue

        try:
            doc_vecs = await embedding_client.embed_batch(texts)
        except Exception as e:
            logger.warning("Embedding batch failed at chunk %d: %s", i, e)
            break

        all_items.extend(
            (wid, vec) for wid, vec in zip(ids, doc_vecs)
        )

    if not all_items:
        return (len(all_ids), 0)

    # ---- Phase 3: write (under lock) ---------------------------------------
    async with lock:
        library.store_embeddings_batch(all_items)

    return (len(all_ids), len(all_items))
