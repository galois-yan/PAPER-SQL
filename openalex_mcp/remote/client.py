"""OpenAlex API client with rate limiting and retry logic."""

import asyncio
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx

from openalex_mcp.common import short_openalex_id

from .filters import validate_filter_string

logger = logging.getLogger(__name__)

# Search mode to API parameter mapping.
SEARCH_MODES: dict[str, str] = {
    "keyword": "search",
    "exact": "search.exact",
    "semantic": "search.semantic",
    "ids": "filter",  # special case: uses filter=openalex_id:W123|W456
}

# Rate limiting constants
SEMANTIC_RATE_LIMIT = 1.0  # seconds between semantic requests
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 2  # exponential backoff base
SOURCE_BATCH_SIZE = 100
SOURCE_SELECT_FIELDS = (
    "id,display_name,abbreviated_title,alternate_titles,"
    "type,works_count,cited_by_count,is_oa,is_in_doaj,"
    "homepage_url,host_organization,country_code,issn_l"
)

_WORK_ID_RE = re.compile(r"^W\d+$", re.IGNORECASE)
_WORK_URL_RE = re.compile(
    r"^https?://(?:www\.)?openalex\.org/(W\d+)/?$",
    re.IGNORECASE,
)
_DOI_PREFIX_RE = re.compile(
    r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
    re.IGNORECASE,
)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)

# Network errors that warrant a retry (transient connectivity problems).
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
)


def _is_retryable(exc: Exception) -> bool:
    """True if the exception is a transient network/connectivity failure."""
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    # httpx wraps some cases in RequestError (e.g. ConnectionReset) — treat
    # all transport-level RequestErrors as retryable.
    return isinstance(exc, httpx.RequestError) and not isinstance(
        exc, httpx.HTTPStatusError
    )


def _parse_work_identifiers(query: str) -> tuple[list[str], list[str]]:
    """Normalize and validate a comma-separated work ID / DOI query."""
    values = [value.strip() for value in query.split(",") if value.strip()]
    if not values:
        raise ValueError("No work IDs or DOIs provided")

    work_ids: list[str] = []
    dois: list[str] = []
    seen_work_ids: set[str] = set()
    seen_dois: set[str] = set()
    invalid: list[str] = []

    for value in values:
        decoded = unquote(value)
        url_match = _WORK_URL_RE.fullmatch(decoded)
        work_id = url_match.group(1) if url_match else decoded
        if _WORK_ID_RE.fullmatch(work_id):
            normalized_id = work_id.upper()
            if normalized_id not in seen_work_ids:
                seen_work_ids.add(normalized_id)
                work_ids.append(normalized_id)
            continue

        doi = _DOI_PREFIX_RE.sub("", decoded).strip()
        if _DOI_RE.fullmatch(doi):
            doi_key = doi.lower()
            if doi_key not in seen_dois:
                seen_dois.add(doi_key)
                dois.append(doi)
            continue

        invalid.append(value)

    if invalid:
        raise ValueError(
            "Invalid work ID or DOI value(s): " + ", ".join(invalid)
        )
    return work_ids, dois


class OpenAlexClient:
    """Async HTTP client for the OpenAlex API."""

    BASE_URL = "https://api.openalex.org"

    CONTENT_BASE_URL = "https://content.openalex.org"

    def __init__(self, api_key: str, email: str = ""):
        self.api_key = api_key
        self.email = email.strip()
        self._http = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=30.0,
            headers={"Accept": "application/json"},
        )
        self._content_http = httpx.AsyncClient(
            base_url=self.CONTENT_BASE_URL,
            timeout=60.0,
            follow_redirects=True,
        )
        self._last_semantic_ts: float = 0.0
        self._semaphore = asyncio.Semaphore(10)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._http.aclose()
        await self._content_http.aclose()

    # ------------------------------------------------------------------
    # Core request with retry
    # ------------------------------------------------------------------

    async def _request(
        self, params: dict[str, Any], endpoint: str = "/works"
    ) -> dict[str, Any]:
        """Make a GET request with retry and rate limiting."""
        if self.api_key:
            params["api_key"] = self.api_key
        if self.email:
            params["mailto"] = self.email

        # Semantic search: enforce 1 req/sec
        search_param = params.get("search.semantic")
        if search_param is not None:
            now = asyncio.get_running_loop().time()
            elapsed = now - self._last_semantic_ts
            if elapsed < SEMANTIC_RATE_LIMIT:
                await asyncio.sleep(SEMANTIC_RATE_LIMIT - elapsed)
            self._last_semantic_ts = asyncio.get_running_loop().time()

        for attempt in range(MAX_RETRIES):
            wait_seconds: float | None = None
            last_exc: Exception | None = None

            async with self._semaphore:
                try:
                    response = await self._http.get(endpoint, params=params)

                    if response.status_code == 200:
                        return response.json()

                    if response.status_code in (429, 500, 502, 503, 504):
                        wait_seconds = RETRY_BACKOFF_BASE**attempt
                        last_exc = RuntimeError(
                            f"OpenAlex API returned HTTP {response.status_code}"
                        )
                        logger.warning(
                            "OpenAlex API %d, retry %d/%d in %ds",
                            response.status_code,
                            attempt + 1,
                            MAX_RETRIES,
                            wait_seconds,
                        )
                    else:
                        response.raise_for_status()

                except httpx.HTTPStatusError as e:
                    # Non-2xx that isn't 429/5xx (those were handled above by
                    # setting wait_seconds) — not transient, fail immediately.
                    raise
                except Exception as exc:
                    # Network errors (ConnectError, TimeoutException, ReadError,
                    # RemoteProtocolError, etc.) — transient, retry with backoff.
                    if _is_retryable(exc):
                        wait_seconds = RETRY_BACKOFF_BASE**attempt
                        last_exc = exc
                        logger.warning(
                            "Network error (%s), retry %d/%d in %ds",
                            type(exc).__name__,
                            attempt + 1,
                            MAX_RETRIES,
                            wait_seconds,
                        )
                    else:
                        raise

            if wait_seconds is not None and attempt < MAX_RETRIES - 1:
                await asyncio.sleep(wait_seconds)

        raise RuntimeError(
            f"OpenAlex API failed after {MAX_RETRIES} retries: "
            f"{last_exc or 'unknown error'}"
        )

    # ------------------------------------------------------------------
    # High-level search methods
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        mode: str = "keyword",
        filters: str | None = None,
        per_page: int = 100,
        page: int = 1,
        cursor: str | None = None,
        sort: str | None = None,
    ) -> dict[str, Any]:
        """Search works on OpenAlex."""
        if mode not in SEARCH_MODES:
            raise ValueError(
                f"Invalid search_mode: {mode}. Must be one of {list(SEARCH_MODES.keys())}"
            )

        if filters:
            validate_filter_string(filters)

        params: dict[str, Any] = {"per_page": min(per_page, 100)}

        if mode == "semantic":
            params["per_page"] = min(per_page, 50)

        if mode == "ids":
            openalex_ids, dois = _parse_work_identifiers(query)

            filter_parts: list[str] = []
            if openalex_ids:
                filter_parts.append("openalex_id:" + "|".join(openalex_ids))
            if dois:
                filter_parts.append("doi:" + "|".join(dois))

            params["filter"] = ",".join(filter_parts)
        else:
            if query.strip():
                param_name = SEARCH_MODES[mode]
                params[param_name] = query
            if filters:
                params["filter"] = filters

        if mode != "ids":
            _filter = params.get("filter", "")
            has_search_term = any(
                k in params for k in ("search", "search.exact", "search.semantic")
            )
            if sort is not None:
                params["sort"] = sort
            elif not has_search_term:
                # No search term — sort by citations (relevance_score
                # requires a search, so skip it to avoid 400 errors).
                params["sort"] = "cited_by_count:desc"
            elif any(f in _filter for f in ("cites:", "cited_by:", "related_to:")):
                params["sort"] = "cited_by_count:desc"
            else:
                params["sort"] = "relevance_score:desc"

        if mode != "ids":
            if cursor:
                params["cursor"] = cursor
            else:
                params["page"] = page

        return await self._request(params)

    # ------------------------------------------------------------------
    # Autocomplete
    # ------------------------------------------------------------------

    async def autocomplete(
        self,
        entity_type: str,
        query: str,
    ) -> dict[str, Any]:
        """Call the OpenAlex autocomplete endpoint for typeahead entity search.

        Args:
            entity_type: Entity type. One of ``"authors"``, ``"sources"``,
                ``"institutions"``, ``"publishers"``, ``"funders"``.
            query: Search string (e.g., a name fragment like "flori").
        """
        params: dict[str, Any] = {"q": query}
        endpoint = f"/autocomplete/{entity_type}"
        return await self._request(params, endpoint=endpoint)

    # ------------------------------------------------------------------
    # Source search
    # ------------------------------------------------------------------

    async def search_sources(
        self,
        search: str,
        filters: str | None = None,
        per_page: int = 100,
        page: int = 1,
        sort: str | None = None,
        select: str | None = None,
    ) -> dict[str, Any]:
        """Search sources (journals, conferences, repositories) on OpenAlex."""
        params: dict[str, Any] = {
            "search": search,
            "per_page": min(per_page, 100),
        }

        if filters:
            params["filter"] = filters

        if sort:
            params["sort"] = sort
        else:
            params["sort"] = "works_count:desc"

        if select:
            params["select"] = select
        else:
            params["select"] = (
                "id,display_name,abbreviated_title,alternate_titles,"
                "type,works_count,cited_by_count,is_oa,is_in_doaj,"
                "homepage_url,host_organization,country_code,issn_l"
            )

        if page:
            params["page"] = page

        return await self._request(params, endpoint="/sources")

    # ------------------------------------------------------------------
    # Content download (PDF / TEI XML)
    # ------------------------------------------------------------------

    async def download_pdf(
        self,
        work_id: str,
        output_path: Path,
    ) -> tuple[Path, int]:
        """Download a full-text PDF from content.openalex.org."""
        if not work_id or not work_id.upper().startswith("W"):
            raise ValueError(
                f"Invalid work_id: {work_id!r}. "
                f"Expected an OpenAlex work ID starting with 'W'."
            )

        endpoint = f"/works/{work_id}.pdf"
        params: dict[str, Any] = {"api_key": self.api_key}

        for attempt in range(MAX_RETRIES):
            wait_seconds: float | None = None

            async with self._semaphore:
                try:
                    async with self._content_http.stream(
                        "GET", endpoint, params=params
                    ) as response:
                        if response.status_code == 200:
                            output_path.parent.mkdir(parents=True, exist_ok=True)
                            total = 0
                            with output_path.open("wb") as f:
                                async for chunk in response.aiter_bytes(
                                    chunk_size=65536
                                ):
                                    f.write(chunk)
                                    total += len(chunk)
                            return (output_path, total)

                        if response.status_code == 404:
                            raise FileNotFoundError(
                                f"No content available for {work_id}. "
                                f"Check has_content.pdf in the work metadata."
                            )

                        if response.status_code in (429, 500, 502, 503, 504):
                            wait_seconds = RETRY_BACKOFF_BASE**attempt
                            logger.warning(
                                "Content API %d for %s, retry %d/%d in %ds",
                                response.status_code,
                                work_id,
                                attempt + 1,
                                MAX_RETRIES,
                                wait_seconds,
                            )
                        else:
                            response.raise_for_status()

                except Exception as exc:
                    if _is_retryable(exc):
                        wait_seconds = RETRY_BACKOFF_BASE**attempt
                        logger.warning(
                            "Content API network error (%s) for %s, "
                            "retry %d/%d in %ds",
                            type(exc).__name__,
                            work_id,
                            attempt + 1,
                            MAX_RETRIES,
                            wait_seconds,
                        )
                    else:
                        raise

            if wait_seconds is not None and attempt < MAX_RETRIES - 1:
                await asyncio.sleep(wait_seconds)

        raise RuntimeError(
            f"Content download failed for {work_id} after {MAX_RETRIES} retries"
        )

    # ------------------------------------------------------------------
    # Single source fetch
    # ------------------------------------------------------------------

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        """Fetch a single source by ID."""
        try:
            params: dict[str, Any] = {}
            return await self._request(params, endpoint=f"/sources/{source_id}")
        except Exception:
            return None

    async def get_sources_batch(self, source_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch multiple sources by OpenAlex ID using batched filters."""
        normalized_ids: list[str] = []
        seen: set[str] = set()
        for source_id in source_ids:
            normalized = short_openalex_id(str(source_id or "").strip())
            if normalized and normalized not in seen:
                seen.add(normalized)
                normalized_ids.append(normalized)

        sources: list[dict[str, Any]] = []
        for i in range(0, len(normalized_ids), SOURCE_BATCH_SIZE):
            chunk = normalized_ids[i : i + SOURCE_BATCH_SIZE]
            if not chunk:
                continue
            params: dict[str, Any] = {
                "filter": "openalex_id:" + "|".join(chunk),
                "per_page": len(chunk),
                "select": SOURCE_SELECT_FIELDS,
            }
            data = await self._request(params, endpoint="/sources")
            sources.extend(data.get("results", []))
        return sources
