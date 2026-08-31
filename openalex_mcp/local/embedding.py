"""Local embedding helpers — pure functions, zero external dependencies.

Supports local/search.py for embedding text construction and cosine similarity.
"""

from __future__ import annotations

import math
from typing import Any

from openalex_mcp.common import extract_concept_names, extract_keyword_names

DEFAULT_EMBEDDING_MAX_CHARS = 3000


def build_embedding_text(work: dict[str, Any]) -> str:
    """Build the text to be embedded for a work.

    Uses title + concepts + keywords (NOT abstract, NOT id).
    """
    parts: list[str] = []

    title = work.get("title") or work.get("display_name", "")
    if title:
        parts.append(title)

    concept_names = extract_concept_names(work)
    if concept_names:
        parts.append("Concepts: " + ", ".join(concept_names))

    kw_names = extract_keyword_names(work)
    if kw_names:
        parts.append("Keywords: " + ", ".join(kw_names))

    return ". ".join(parts)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two float vectors (pure Python)."""
    if len(a) != len(b):
        raise ValueError(f"Vector dimension mismatch: {len(a)} vs {len(b)}")

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0

    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def truncate_text(text: str, max_chars: int = DEFAULT_EMBEDDING_MAX_CHARS) -> str:
    """Truncate text to stay under token limits."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars]
