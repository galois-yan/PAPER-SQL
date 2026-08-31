"""Elsevier Abstract Retrieval API client and parsers."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any
from urllib.parse import quote, unquote

import httpx

logger = logging.getLogger(__name__)

ELSEVIER_BASE_URL = "https://api.elsevier.com"
ELSEVIER_MAX_RETRIES = 4
ELSEVIER_RETRY_BACKOFF_BASE = 2

_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
)

_TAG_RE = re.compile(r"<[^>]+>")
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)


def normalize_doi(value: str | None) -> str:
    """Return a normalized DOI string without URL or doi: prefix."""
    if not value:
        return ""
    doi = unquote(str(value).strip())
    doi = _DOI_PREFIX_RE.sub("", doi).strip()
    return doi


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    return isinstance(exc, httpx.RequestError) and not isinstance(
        exc, httpx.HTTPStatusError
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _clean_text(value: str) -> str:
    text = html.unescape(value)
    text = _TAG_RE.sub(" ", text)
    return " ".join(text.split())


def _textify(value: Any) -> str:
    """Turn Elsevier's XML-shaped JSON fragments into readable text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [_textify(item) for item in value]
        return " ".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("$", "_", "text", "ce:para", "para"):
            if key in value:
                text = _textify(value[key])
                if text:
                    return text
        parts: list[str] = []
        for key, item in value.items():
            if key.startswith("@"):  # XML attributes in Elsevier JSON.
                continue
            text = _textify(item)
            if text:
                parts.append(text)
        return " ".join(parts)
    return ""


def _find_first_text(obj: Any, keys: tuple[str, ...]) -> str:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj:
                text = _textify(obj[key])
                if text:
                    return text
        for value in obj.values():
            text = _find_first_text(value, keys)
            if text:
                return text
    elif isinstance(obj, list):
        for value in obj:
            text = _find_first_text(value, keys)
            if text:
                return text
    return ""


def parse_elsevier_abstract(
    requested_doi: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Extract normalized fields from an Elsevier abstract response."""
    root = data.get("abstracts-retrieval-response") or data
    core = root.get("coredata") if isinstance(root, dict) else {}
    core = core if isinstance(core, dict) else {}

    abstract = _find_first_text(
        data,
        (
            "dc:description",
            "description",
            "abstract",
            "abstracts",
        ),
    )
    title = _find_first_text(core, ("dc:title", "title")) or _find_first_text(
        data, ("dc:title",)
    )
    doi = normalize_doi(
        _find_first_text(core, ("prism:doi", "doi")) or requested_doi
    )
    source = _find_first_text(core, ("prism:publicationName",))
    scopus_id = _find_first_text(core, ("dc:identifier", "eid"))

    return {
        "doi": doi,
        "title": title,
        "publication_name": source,
        "scopus_id": scopus_id,
        "abstract": abstract,
        "raw": data,
    }


class ElsevierClient:
    """Async client for Elsevier Abstract Retrieval API."""

    def __init__(self, api_key: str, inst_token: str | None = None):
        if not api_key:
            raise ValueError("ELSEVIER_API_KEY is required for ElsevierClient")
        headers = {
            "Accept": "application/json",
            "X-ELS-APIKey": api_key,
        }
        if inst_token:
            headers["X-ELS-Insttoken"] = inst_token

        self._http = httpx.AsyncClient(
            base_url=ELSEVIER_BASE_URL,
            timeout=30.0,
            headers=headers,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._http.aclose()

    async def fetch_abstract_by_doi(self, doi: str) -> dict[str, Any]:
        """Fetch one abstract by DOI and return normalized fields."""
        normalized = normalize_doi(doi)
        if not normalized:
            raise ValueError("DOI is required")

        endpoint = f"/content/abstract/doi/{quote(normalized, safe='')}"
        data = await self._request(endpoint, params={"view": "FULL"})
        return parse_elsevier_abstract(normalized, data)

    async def find_scopus_eid_by_doi(self, doi: str) -> str | None:
        """Resolve a DOI to a Scopus EID through Scopus Search."""
        normalized = normalize_doi(doi)
        if not normalized:
            return None
        data = await self._request(
            "/content/search/scopus",
            params={
                "query": f"DOI({normalized})",
                "count": 1,
                "view": "STANDARD",
            },
        )
        entries = ((data.get("search-results") or {}).get("entry") or [])
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            eid = entry.get("eid") or entry.get("dc:identifier")
            if eid:
                return str(eid).replace("SCOPUS_ID:", "", 1)
        return None

    async def fetch_abstract_via_scopus(self, doi: str) -> dict[str, Any]:
        """Resolve DOI in Scopus, then retrieve the abstract by EID."""
        normalized = normalize_doi(doi)
        eid = await self.find_scopus_eid_by_doi(normalized)
        if not eid:
            raise FileNotFoundError("Scopus record not found")
        data = await self._request(
            f"/content/abstract/eid/{quote(eid, safe='')}",
            params={"view": "FULL"},
        )
        return parse_elsevier_abstract(normalized, data)

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        last_exc: Exception | None = None

        for attempt in range(ELSEVIER_MAX_RETRIES):
            try:
                response = await self._http.get(endpoint, params=params)
                if response.status_code == 200:
                    return response.json()
                if response.status_code == 404:
                    raise FileNotFoundError("Elsevier abstract not found")
                if response.status_code in (429, 500, 502, 503, 504):
                    wait = _retry_after_seconds(response)
                    if wait is None:
                        wait = ELSEVIER_RETRY_BACKOFF_BASE**attempt
                    last_exc = RuntimeError(
                        f"Elsevier API returned HTTP {response.status_code}"
                    )
                    logger.warning(
                        "Elsevier API %d, retry %d/%d in %.1fs",
                        response.status_code,
                        attempt + 1,
                        ELSEVIER_MAX_RETRIES,
                        wait,
                    )
                    if attempt < ELSEVIER_MAX_RETRIES - 1:
                        await asyncio.sleep(wait)
                        continue
                response.raise_for_status()
            except FileNotFoundError:
                raise
            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                if not _is_retryable(exc):
                    raise
                last_exc = exc
                if attempt < ELSEVIER_MAX_RETRIES - 1:
                    wait = ELSEVIER_RETRY_BACKOFF_BASE**attempt
                    logger.warning(
                        "Elsevier API network error (%s), retry %d/%d in %.1fs",
                        type(exc).__name__,
                        attempt + 1,
                        ELSEVIER_MAX_RETRIES,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    continue

        raise RuntimeError(
            f"Elsevier API failed after {ELSEVIER_MAX_RETRIES} retries: "
            f"{last_exc or 'unknown error'}"
        )
