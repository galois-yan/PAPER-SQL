"""Remote OpenAlex, Elsevier, and ZhipuAI clients — public API layer.

Import from this module (not its submodules) to access remote API functions.
Singleton management for remote clients is provided so other layers can obtain
shared instances without importing internal submodules.
"""

from .autocomplete import autocomplete
from .client import OpenAlexClient
from .download import download_pdf
from .elsevier import ElsevierClient, normalize_doi
from .embeddings import EmbeddingClient
from .search import search_keyword, search_semantic, search_ids

__all__ = [
    "OpenAlexClient",
    "EmbeddingClient",
    "ElsevierClient",
    "autocomplete",
    "search_keyword",
    "search_semantic",
    "search_ids",
    "download_pdf",
    "normalize_doi",
    "get_client",
    "set_client",
    "get_embed",
    "set_embed",
    "get_elsevier",
    "set_elsevier",
]

# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_client: OpenAlexClient | None = None
_embed: EmbeddingClient | None = None
_elsevier: ElsevierClient | None = None


def get_client() -> OpenAlexClient:
    """Return the shared OpenAlexClient singleton."""
    assert _client is not None, (
        "OpenAlexClient not initialized — call set_client() first"
    )
    return _client


def set_client(client: OpenAlexClient) -> None:
    """Register the shared OpenAlexClient singleton."""
    global _client
    _client = client


def get_embed() -> EmbeddingClient | None:
    """Return the shared EmbeddingClient, or None if no ZHIPUAI_API_KEY configured."""
    return _embed


def set_embed(embed: EmbeddingClient | None) -> None:
    """Register the shared EmbeddingClient singleton."""
    global _embed
    _embed = embed


def get_elsevier() -> ElsevierClient | None:
    """Return the shared ElsevierClient, or None if no key is configured."""
    return _elsevier


def set_elsevier(elsevier: ElsevierClient | None) -> None:
    """Register the shared ElsevierClient singleton."""
    global _elsevier
    _elsevier = elsevier
