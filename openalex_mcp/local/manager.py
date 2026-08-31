"""SQLite-based literature library with vector search support."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import sqlite_vec

from openalex_mcp.common import (
    extract_source_id,
    short_openalex_id,
)

logger = logging.getLogger(__name__)

LIBRARY_DB_NAME = "library.db"
DEFAULT_LIBRARY_PATH = Path.home() / ".AI-CACHE" / "openalex" / LIBRARY_DB_NAME

_READONLY_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)
_READONLY_PRAGMAS = frozenset(
    {
        "compile_options",
        "database_list",
        "foreign_key_list",
        "index_info",
        "index_list",
        "index_xinfo",
        "table_info",
        "table_xinfo",
    }
)


def _readonly_authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    _database: str | None,
    _trigger: str | None,
) -> int:
    """Allow only query operations on the dedicated read connection."""
    if action in _READONLY_ACTIONS:
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() == "load_extension":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA:
        pragma = (arg1 or "").lower()
        if pragma in _READONLY_PRAGMAS:
            return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

WORKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    id               TEXT PRIMARY KEY,
    title            TEXT,
    publication_year INTEGER,
    publication_date TEXT,
    type             TEXT,
    cited_by_count   INTEGER DEFAULT 0,
    is_oa            INTEGER DEFAULT 0,
    language         TEXT,
    doi              TEXT,
    abstract         TEXT,
    fulltext         TEXT,
    authors_json     TEXT,
    concepts_json    TEXT,
    keywords_json    TEXT,
    referenced_works TEXT,
    related_works    TEXT,
    source_id        TEXT,
    source_name      TEXT,
    has_content      TEXT,
    oa_status        TEXT,
    oa_url           TEXT,
    raw_json         TEXT,
    vec              BLOB
)
"""

SOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id                TEXT PRIMARY KEY,
    display_name      TEXT,
    abbreviated_title TEXT,
    alternate_titles  TEXT,
    type              TEXT,
    works_count       INTEGER,
    cited_by_count    INTEGER,
    is_oa             INTEGER DEFAULT 0,
    is_in_doaj        INTEGER DEFAULT 0,
    homepage_url      TEXT,
    host_organization TEXT,
    issn_l            TEXT,
    country_code      TEXT,
    raw_json          TEXT,
    created_at        TEXT
)
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_list_str(val: Any) -> str | None:
    """Serialize a value to JSON string, returning None for empty."""
    if val is None:
        return None
    s = json.dumps(val, ensure_ascii=False)
    if s == "[]" or s == "{}" or s == '""':
        return None
    return s


def _short_id_list_str(val: Any) -> str | None:
    """Serialize a list of OpenAlex IDs to short-form JSON (None for empty).

    Turns ``["https://openalex.org/W123", ...]`` into ``["W123", ...]`` so the
    stored values join directly against ``works.id``. Idempotent for values
    that are already short.
    """
    if not val:
        return None
    return _json_list_str([short_openalex_id(x) for x in val])


# ============================================================================
# LibraryManager
# ============================================================================


class LibraryManager:
    """Manages the SQLite literature library.

    WAL mode + sqlite-vec for concurrent reads/writes.

    A persistent WAL connection handles writes and internal reads. Ad-hoc
    ``library_query`` SQL uses a separate persistent mode=ro connection.
    WAL allows readers and writers to proceed without blocking each other;
    the ``asyncio.Lock`` only serializes multiple writers.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            self.db_path = DEFAULT_LIBRARY_PATH
        elif isinstance(db_path, str):
            self.db_path = Path(db_path)
        else:
            self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._read_conn: sqlite3.Connection | None = None
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Lazily create and return the asyncio lock.

        ``asyncio.Lock()`` must be created inside a running event loop.
        We delay creation so LibraryManager can be instantiated at import
        time (before the event loop starts).
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def conn(self) -> sqlite3.Connection:
        """Persistent WAL connection for writes and internal trusted reads.

        WAL mode allows concurrent reads + writes without blocking.
        The sqlite-vec extension provides ``vec_distance_cosine()`` and
        related scalar functions for semantic search directly in SQL.
        """
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            self._conn.execute(WORKS_SCHEMA)
            self._conn.execute(SOURCES_SCHEMA)
            self._migrate_works_schema(self._conn)
            self._conn.commit()
        return self._conn

    @property
    def read_conn(self) -> sqlite3.Connection:
        """Persistent database-level read-only connection for ad-hoc SQL."""
        if self._read_conn is None:
            # Ensure the database and current schema exist before opening mode=ro.
            _ = self.conn
            uri = self.db_path.resolve().as_uri() + "?mode=ro"
            self._read_conn = sqlite3.connect(uri, uri=True)
            self._read_conn.execute("PRAGMA busy_timeout=5000")
            self._read_conn.enable_load_extension(True)
            sqlite_vec.load(self._read_conn)
            self._read_conn.enable_load_extension(False)
            self._read_conn.execute("PRAGMA query_only=ON")
            self._read_conn.set_authorizer(_readonly_authorizer)
        return self._read_conn

    def _migrate_works_schema(self, conn: sqlite3.Connection) -> None:
        """Idempotently add columns introduced after the original schema.

        Safe to call on every connect: ALTER TABLE only runs for missing
        columns, so it is a no-op on existing and fresh databases alike.
        """
        columns = {row[1] for row in conn.execute("PRAGMA table_info(works)")}
        if "referenced_works" not in columns:
            conn.execute("ALTER TABLE works ADD COLUMN referenced_works TEXT")
        if "related_works" not in columns:
            conn.execute("ALTER TABLE works ADD COLUMN related_works TEXT")
        if "fulltext" not in columns:
            conn.execute("ALTER TABLE works ADD COLUMN fulltext TEXT")

    def close(self) -> None:
        """Close the persistent read and write connections."""
        if self._read_conn is not None:
            self._read_conn.close()
            self._read_conn = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ==================================================================
    # Read helpers
    # ==================================================================

    def _fetch_dicts(
        self, sql: str, params: list | dict | None = None
    ) -> list[dict[str, Any]]:
        """Execute a query and return list of dicts."""
        params = params or []
        result = self.conn.execute(sql, params)
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def execute_readonly(self, sql: str) -> sqlite3.Cursor:
        """Execute SQL on the database-level read-only connection."""
        return self.read_conn.execute(sql)

    # ==================================================================
    # Works CRUD
    # ==================================================================

    def upsert_work(self, work: dict[str, Any]) -> bool:
        """Insert or update a work.

        Returns True if the work was newly inserted.
        """
        abstract = work.get("abstract", "")
        if isinstance(abstract, dict):
            abstract = ""

        authors_json = _json_list_str(
            work.get("authorships") or work.get("authors")
        )
        concepts_json = _json_list_str(work.get("concepts"))
        keywords_json = _json_list_str(work.get("keywords"))
        referenced_works = _short_id_list_str(work.get("referenced_works"))
        related_works = _short_id_list_str(work.get("related_works"))

        source_id = extract_source_id(work)

        # Extract source_name from primary_location.source.display_name
        source = work.get("primary_location") or {}
        source_disp = source.get("source") or {}
        source_name = source_disp.get("display_name", "")

        # Extract has_content (e.g. {"pdf": true})
        has_content_val = work.get("has_content")
        has_content = _json_list_str(has_content_val) if has_content_val else None

        oa_info = work.get("open_access") or {}
        oa_status = oa_info.get("oa_status") or work.get("oa_status", "")
        oa_url_val = oa_info.get("oa_url") or work.get("oa_url", "")
        if (
            oa_url_val
            and isinstance(oa_url_val, str)
            and oa_url_val.startswith("http")
        ):
            oa_url = oa_url_val
        else:
            oa_url = None

        is_oa = work.get("is_oa")
        if is_oa is None:
            is_oa = int(bool(oa_info.get("is_oa", False)))
        else:
            is_oa = int(bool(is_oa))

        doi = work.get("doi") or ""
        if doi:
            doi = doi.replace("https://doi.org/", "")

        wid = short_openalex_id(work.get("id", ""))
        raw_json_str = json.dumps(work, ensure_ascii=False)
        title = work.get("title") or work.get("display_name", "")

        conn = self.conn
        # Check if work already exists
        existing = conn.execute(
            "SELECT 1 FROM works WHERE id = :id", {"id": wid}
        ).fetchone()
        is_new = existing is None

        conn.execute(
            """
            INSERT INTO works (
                id, title, publication_year, publication_date, type,
                cited_by_count, is_oa, language, doi, abstract,
                authors_json, concepts_json, keywords_json,
                referenced_works, related_works,
                source_id, source_name, has_content,
                oa_status, oa_url, raw_json
            ) VALUES (
                :id, :title, :publication_year, :publication_date, :type,
                :cited_by_count, :is_oa, :language, :doi, :abstract,
                :authors_json, :concepts_json, :keywords_json,
                :referenced_works, :related_works,
                :source_id, :source_name, :has_content,
                :oa_status, :oa_url, :raw_json
            )
            ON CONFLICT(id) DO UPDATE SET
                title = COALESCE(NULLIF(excluded.title, ''), works.title),
                publication_year = COALESCE(
                    excluded.publication_year, works.publication_year
                ),
                publication_date = COALESCE(
                    NULLIF(excluded.publication_date, ''), works.publication_date
                ),
                type = COALESCE(NULLIF(excluded.type, ''), works.type),
                cited_by_count = excluded.cited_by_count,
                is_oa = excluded.is_oa,
                language = COALESCE(NULLIF(excluded.language, ''), works.language),
                doi = COALESCE(NULLIF(excluded.doi, ''), works.doi),
                abstract = CASE
                    WHEN works.abstract IS NULL OR works.abstract = ''
                    THEN COALESCE(NULLIF(excluded.abstract, ''), works.abstract)
                    ELSE works.abstract
                END,
                authors_json = COALESCE(excluded.authors_json, works.authors_json),
                concepts_json = COALESCE(excluded.concepts_json, works.concepts_json),
                keywords_json = COALESCE(excluded.keywords_json, works.keywords_json),
                source_id = COALESCE(excluded.source_id, works.source_id),
                oa_status = excluded.oa_status,
                oa_url = COALESCE(excluded.oa_url, works.oa_url),
                source_name = COALESCE(
                    NULLIF(excluded.source_name, ''), works.source_name
                ),
                has_content = excluded.has_content,
                referenced_works = COALESCE(
                    excluded.referenced_works, works.referenced_works
                ),
                related_works = COALESCE(
                    excluded.related_works, works.related_works
                ),
                raw_json = excluded.raw_json
            """,
            {
                "id": wid,
                "title": title,
                "publication_year": work.get("publication_year"),
                "publication_date": work.get("publication_date"),
                "type": work.get("type", ""),
                "cited_by_count": work.get("cited_by_count", 0) or 0,
                "is_oa": is_oa,
                "language": work.get("language", ""),
                "doi": doi,
                "abstract": abstract,
                "authors_json": authors_json,
                "concepts_json": concepts_json,
                "keywords_json": keywords_json,
                "referenced_works": referenced_works,
                "related_works": related_works,
                "source_id": source_id,
                "source_name": source_name,
                "has_content": has_content,
                "oa_status": oa_status,
                "oa_url": oa_url,
                "raw_json": raw_json_str,
            },
        )
        conn.commit()

        return is_new

    # ==================================================================
    # Sources CRUD
    # ==================================================================

    def upsert_source(self, source: dict[str, Any]) -> bool:
        """Insert or update a source record."""
        alternate_titles = _json_list_str(source.get("alternate_titles"))
        raw_json_str = json.dumps(source, ensure_ascii=False)
        sid = short_openalex_id(source.get("id", ""))

        conn = self.conn
        existing = conn.execute(
            "SELECT 1 FROM sources WHERE id = :id", {"id": sid}
        ).fetchone()
        is_new = existing is None

        conn.execute(
            """
            INSERT INTO sources (
                id, display_name, abbreviated_title, alternate_titles, type,
                works_count, cited_by_count, is_oa, is_in_doaj,
                homepage_url, host_organization, issn_l, country_code,
                raw_json, created_at
            ) VALUES (
                :id, :display_name, :abbreviated_title, :alternate_titles, :type,
                :works_count, :cited_by_count, :is_oa, :is_in_doaj,
                :homepage_url, :host_organization, :issn_l, :country_code,
                :raw_json, :created_at
            )
            ON CONFLICT (id) DO NOTHING
            """,
            {
                "id": sid,
                "display_name": source.get("display_name", ""),
                "abbreviated_title": source.get("abbreviated_title", ""),
                "alternate_titles": alternate_titles,
                "type": source.get("type", ""),
                "works_count": source.get("works_count", 0) or 0,
                "cited_by_count": source.get("cited_by_count", 0) or 0,
                "is_oa": int(bool(source.get("is_oa", False))),
                "is_in_doaj": int(bool(source.get("is_in_doaj", False))),
                "homepage_url": source.get("homepage_url", ""),
                "host_organization": source.get("host_organization", ""),
                "issn_l": source.get("issn_l", ""),
                "country_code": source.get("country_code", ""),
                "raw_json": raw_json_str,
                "created_at": datetime.datetime.now(
                    datetime.timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        conn.commit()

        return is_new

    def has_source(self, source_id: str) -> bool:
        """Check whether a source exists in the library."""
        row = self.conn.execute(
            "SELECT 1 FROM sources WHERE id = ?", [source_id]
        ).fetchone()
        return row is not None

    def get_existing_source_ids(self, source_ids: list[str]) -> set[str]:
        """Return source IDs already present in the library."""
        if not source_ids:
            return set()
        placeholders = ",".join("?" for _ in source_ids)
        rows = self.conn.execute(
            f"SELECT id FROM sources WHERE id IN ({placeholders})",
            source_ids,
        ).fetchall()
        return {row[0] for row in rows}

    # ==================================================================
    # Embedding storage (in-works vec BLOB column)
    # ==================================================================

    def store_embeddings_batch(
        self,
        items: list[tuple[str, list[float]]],
    ) -> None:
        """Update works.vec for multiple works.

        Stores vectors as float32 BLOB via sqlite-vec compatible format.

        Args:
            items: List of (work_id, embedding_vector).
        """
        if not items:
            return

        import numpy as np

        data = [
            (np.array(vec, dtype=np.float32).tobytes(), rid)
            for rid, vec in items
        ]

        conn = self.conn
        with conn:
            conn.executemany(
                "UPDATE works SET vec = ? WHERE id = ?", data
            )

    def get_embeddings(
        self, work_ids: list[str]
    ) -> dict[str, list[float]]:
        """Get stored embedding vectors for the given work IDs.

        Returns dict mapping work_id -> list[float].
        """
        if not work_ids:
            return {}
        placeholders = ",".join("?" for _ in work_ids)
        rows = self.conn.execute(
            f"SELECT id, vec FROM works "
            f"WHERE id IN ({placeholders}) AND vec IS NOT NULL",
            work_ids,
        ).fetchall()

        import numpy as np

        return {
            row[0]: np.frombuffer(row[1], dtype=np.float32).tolist()
            for row in rows
        }

    def get_work_ids_without_embeddings(
        self, work_ids: list[str]
    ) -> list[str]:
        """Return the subset of work_ids that have vec IS NULL."""
        if not work_ids:
            return []
        placeholders = ",".join("?" for _ in work_ids)
        existing = self.conn.execute(
            f"SELECT id FROM works "
            f"WHERE id IN ({placeholders}) AND vec IS NOT NULL",
            work_ids,
        ).fetchall()
        existing_ids = {r[0] for r in existing}
        return [wid for wid in work_ids if wid not in existing_ids]

    def get_embedding_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM works WHERE vec IS NOT NULL"
        ).fetchone()
        return row[0] if row else 0

    # ==================================================================
    # Query helpers (all read-only)
    # ==================================================================

    def get_all_work_ids(self) -> list[str]:
        """Return all work IDs in the library."""
        rows = self.conn.execute("SELECT id FROM works").fetchall()
        return [r[0] for r in rows]

    def get_works_batch(self, work_ids: list[str]) -> list[dict[str, Any]]:
        """Get full work dicts for a list of work IDs."""
        if not work_ids:
            return []
        placeholders = ",".join("?" for _ in work_ids)
        return self._fetch_dicts(
            f"SELECT * FROM works WHERE id IN ({placeholders})", work_ids
        )

    def update_work_abstract(
        self,
        work_id: str,
        abstract: str,
        overwrite: bool = False,
    ) -> bool:
        """Update a work abstract by ID.

        By default, only fills an empty abstract. Set overwrite=True to
        replace an existing abstract.
        """
        if not work_id or not abstract:
            return False
        if overwrite:
            cursor = self.conn.execute(
                "UPDATE works SET abstract = ? WHERE id = ?",
                [abstract, work_id],
            )
        else:
            cursor = self.conn.execute(
                """
                UPDATE works SET abstract = ?
                WHERE id = ? AND (abstract IS NULL OR abstract = '')
                """,
                [abstract, work_id],
            )
        self.conn.commit()
        return cursor.rowcount > 0

    def update_work_abstract_by_doi(
        self,
        doi: str,
        abstract: str,
        overwrite: bool = False,
    ) -> int:
        """Update abstracts for library rows matching a DOI."""
        if not doi or not abstract:
            return 0
        doi_expr = (
            "LOWER(REPLACE(REPLACE(doi, 'https://doi.org/', ''), "
            "'http://dx.doi.org/', '')) = LOWER(?)"
        )
        if overwrite:
            cursor = self.conn.execute(
                f"UPDATE works SET abstract = ? WHERE {doi_expr}",
                [abstract, doi],
            )
        else:
            cursor = self.conn.execute(
                f"""
                UPDATE works SET abstract = ?
                WHERE {doi_expr} AND (abstract IS NULL OR abstract = '')
                """,
                [abstract, doi],
            )
        self.conn.commit()
        return max(0, cursor.rowcount)

    def update_work_fulltext(self, work_id: str, fulltext: str) -> bool:
        """Store extracted full text for a work without changing metadata."""
        if not work_id or not fulltext:
            return False
        cursor = self.conn.execute(
            "UPDATE works SET fulltext = ? WHERE id = ?",
            [fulltext, short_openalex_id(work_id)],
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def get_work_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM works"
        ).fetchone()
        return row[0] if row else 0

    def count_new_ids(self, work_ids: list[str]) -> int:
        """Return how many of the given IDs are NOT yet in the library."""
        if not work_ids:
            return 0
        placeholders = ",".join("?" for _ in work_ids)
        row = self.conn.execute(
            f"SELECT COUNT(*) FROM works WHERE id IN ({placeholders})",
            work_ids,
        ).fetchone()
        existing = row[0] if row else 0
        return len(work_ids) - existing

    # ==================================================================
    # Joins
    # ==================================================================

    def get_source_name(self, source_id: str) -> str | None:
        """Get the display_name for a source ID, or None."""
        row = self.conn.execute(
            "SELECT display_name FROM sources WHERE id = ?", [source_id]
        ).fetchone()
        return row[0] if row else None

    # ==================================================================
    # Delete
    # ==================================================================

    def delete_works(self, work_ids: list[str]) -> int:
        """Delete works by ID."""
        if not work_ids:
            return 0
        placeholders = ",".join("?" for _ in work_ids)
        cursor = self.conn.execute(
            f"DELETE FROM works WHERE id IN ({placeholders})", work_ids
        )
        self.conn.commit()
        return max(0, cursor.rowcount)

    def delete_all(self) -> int:
        """Delete ALL works and sources."""
        count = self.get_work_count()
        with self.conn:
            self.conn.execute("DELETE FROM works")
            self.conn.execute("DELETE FROM sources")
        return count

    # ==================================================================
    # Statistics
    # ==================================================================

    def get_stats(self) -> dict[str, Any]:
        from .stats import get_library_stats

        return get_library_stats(self)

    # ==================================================================
    # Export to BibTeX
    # ==================================================================

    def export_to_bibtex(
        self,
        work_ids: list[str] | None = None,
        sort: str | None = None,
        cite_key_style: str = "author_year",
    ) -> str:
        from .export import export_library_bibtex

        return export_library_bibtex(
            self,
            work_ids=work_ids,
            sort=sort,
            cite_key_style=cite_key_style,
        )
