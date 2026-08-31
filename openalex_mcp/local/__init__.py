"""Local SQLite literature library — public API layer.

Import from this module (not its submodules) to access local library functions.
Singleton management for LibraryManager is provided so other layers can
obtain the shared instance without importing internal submodules.
"""

from .manager import LibraryManager
from .manage import library_delete, library_export, library_generate_embeddings, library_stats
from .upsert import upsert_works

__all__ = [
    "LibraryManager",
    "library_export",
    "library_stats",
    "library_delete",
    "library_generate_embeddings",
    "upsert_works",
    "get_library",
    "set_library",
]

# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_library: LibraryManager | None = None


def get_library() -> LibraryManager:
    """Return the shared LibraryManager singleton (may be None before init)."""
    assert _library is not None, (
        "LibraryManager not initialized — call set_library() first"
    )
    return _library


def set_library(lib: LibraryManager) -> None:
    """Register the shared LibraryManager singleton."""
    global _library
    _library = lib
