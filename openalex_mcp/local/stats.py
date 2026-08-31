"""Library statistics — standalone function.

Caller must hold the asyncio.Lock (via library._get_lock()).
"""

from __future__ import annotations

import json
from typing import Any


def get_library_stats(library) -> dict[str, Any]:
    """Return a dictionary of library statistics."""
    c = library.conn

    total = c.execute("SELECT COUNT(*) FROM works").fetchone()[0]

    year_range = c.execute(
        "SELECT MIN(publication_year), MAX(publication_year) FROM works"
    ).fetchone()

    with_abstract = c.execute(
        "SELECT COUNT(*) FROM works WHERE abstract IS NOT NULL AND abstract != ''"
    ).fetchone()[0]

    with_embeddings = c.execute(
        "SELECT COUNT(*) FROM works WHERE vec IS NOT NULL"
    ).fetchone()[0]

    with_fulltext = c.execute(
        "SELECT COUNT(*) FROM works WHERE fulltext IS NOT NULL AND fulltext != ''"
    ).fetchone()[0]

    oa_count = c.execute(
        "SELECT COUNT(*) FROM works WHERE is_oa = 1"
    ).fetchone()[0]

    # Top 10 sources
    top_sources = c.execute(
        """
        SELECT s.display_name, s.type, COUNT(*) AS cnt,
               SUM(CASE WHEN w.is_oa = 1 THEN 1 ELSE 0 END) AS oa_cnt
        FROM works w
        LEFT JOIN sources s ON w.source_id = s.id
        WHERE w.source_id IS NOT NULL
        GROUP BY w.source_id, s.display_name, s.type
        ORDER BY cnt DESC
        LIMIT 10
        """
    ).fetchall()

    # Top 10 concepts
    concept_counts: dict[str, list[float]] = {}
    for (concepts_json,) in c.execute(
        "SELECT concepts_json FROM works WHERE concepts_json IS NOT NULL"
    ).fetchall():
        try:
            concepts = json.loads(concepts_json) if concepts_json else []
        except json.JSONDecodeError:
            continue
        for item in concepts:
            if isinstance(item, dict):
                name = item.get("display_name", "")
                score = item.get("score", 0)
            else:
                name = str(item)
                score = 0
            if name:
                concept_counts.setdefault(name, []).append(score)

    top_concepts = sorted(
        concept_counts.items(),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )[:10]

    return {
        "total": total,
        "year_min": year_range[0] if year_range else None,
        "year_max": year_range[1] if year_range else None,
        "with_abstract": with_abstract,
        "with_fulltext": with_fulltext,
        "with_embeddings": with_embeddings,
        "oa_count": oa_count,
        "pending_embeddings": total - with_embeddings,
        "top_sources": [
            {
                "name": r[0] or "(unknown)",
                "type": r[1] or "?",
                "count": r[2],
                "oa_pct": round(r[3] / r[2] * 100, 1) if r[2] else 0,
            }
            for r in top_sources
        ],
        "top_concepts": [
            {
                "name": name,
                "count": len(scores),
                "avg_score": round(sum(scores) / len(scores), 3) if scores else 0,
            }
            for name, scores in top_concepts
        ],
    }
