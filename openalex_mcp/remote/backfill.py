"""Automatic abstract backfill for OpenAlex works missing abstracts."""

from __future__ import annotations

import asyncio
import datetime
import html
import os
import re
from typing import Any
from urllib.parse import quote

import httpx

from .elsevier import normalize_doi

_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")
_WHITESPACE_RE = re.compile(r"\s+")
BACKFILL_PRIORITY_LABEL = "top-cited Elsevier journal works"


def _clean_text(value: Any) -> str:
    if not value:
        return ""
    text = html.unescape(str(value))
    text = _TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _reconstruct_abstract(inverted_index: Any) -> str:
    if not isinstance(inverted_index, dict):
        return ""
    positions: dict[int, str] = {}
    for word, indexes in inverted_index.items():
        for index in indexes or []:
            positions[int(index)] = str(word)
    return " ".join(positions[index] for index in sorted(positions))


def _doi(work: dict[str, Any]) -> str:
    value = work.get("doi") or (work.get("open_access") or {}).get("doi") or ""
    return normalize_doi(str(value))


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _source_dicts(work: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for location_key in ("primary_location", "best_oa_location"):
        location = work.get(location_key) or {}
        if isinstance(location, dict):
            source = location.get("source") or {}
            if isinstance(source, dict):
                sources.append(source)
    for location in work.get("locations") or []:
        if not isinstance(location, dict):
            continue
        source = location.get("source") or {}
        if isinstance(source, dict):
            sources.append(source)
    return sources


def _text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item]
    return [str(value)] if value else []


def _is_elsevier_journal_work(work: dict[str, Any]) -> bool:
    texts = _text_values(work.get("source_name")) + _text_values(work.get("publisher"))
    source_types: set[str] = set()
    for source in _source_dicts(work):
        source_types.add(str(source.get("type") or "").strip().lower())
        for key in (
            "display_name",
            "host_organization_name",
            "host_organization_lineage_names",
            "publisher",
        ):
            texts.extend(_text_values(source.get(key)))

    if "elsevier" not in " ".join(texts).lower():
        return False

    work_type = str(work.get("type") or "").strip().lower()
    if work_type and work_type not in {"article", "review"}:
        return False
    return not source_types or "journal" in source_types


def _priority_key(work: dict[str, Any]) -> tuple[int, int, str]:
    year = _int_value(work.get("publication_year"))
    age = datetime.date.today().year - year if year > 0 else 0
    return (
        _int_value(work.get("cited_by_count")),
        age,
        str(work.get("id") or ""),
    )


def _priority_elsevier_journal_targets(
    targets: list[dict[str, Any]],
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    candidates = [
        work
        for work in targets
        if _doi(work) and _is_elsevier_journal_work(work)
    ]
    ranked = sorted(candidates, key=_priority_key, reverse=True)
    return ranked[:limit], len(candidates)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


async def _json_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    try:
        response = await client.get(url, params=params)
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = min(10.0, max(1.0, float(retry_after or "2")))
            except ValueError:
                delay = 2.0
            await asyncio.sleep(delay)
            response = await client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


async def _fetch_crossref(client: httpx.AsyncClient, doi: str) -> str:
    email = os.getenv("OPENALEX_EMAIL", "").strip()
    payload = await _json_get(
        client,
        f"https://api.crossref.org/works/{quote(doi, safe='')}",
        params={"mailto": email} if email else None,
    )
    if not payload:
        return ""
    return _clean_text((payload.get("message") or {}).get("abstract"))


async def _resolve_one(
    client: httpx.AsyncClient,
    doi: str,
    elsevier_client,
) -> tuple[str, str]:
    abstract = await _fetch_crossref(client, doi)
    if abstract:
        return abstract, "crossref"

    if elsevier_client is not None:
        try:
            record = await elsevier_client.fetch_abstract_by_doi(doi)
            abstract = _clean_text(record.get("abstract"))
            if abstract:
                return abstract, "elsevier"
        except Exception:
            pass
        try:
            record = await elsevier_client.fetch_abstract_via_scopus(doi)
            abstract = _clean_text(record.get("abstract"))
            if abstract:
                return abstract, "scopus"
        except Exception:
            pass
    return "", ""


async def backfill_missing_abstracts(works: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill missing abstracts in place without ever dropping a work."""
    stats: dict[str, Any] = {
        "enabled": _bool_env("BACKFILL_ABSTRACTS", True),
        "targets": 0,
        "attempted": 0,
        "filled": 0,
        "failed": 0,
        "no_doi": 0,
        "skipped": False,
        "limited": False,
        "selected": 0,
        "priority_candidates": 0,
        "priority_selected": 0,
        "reason": "",
        "sources": {
            "crossref": 0,
            "elsevier": 0,
            "scopus": 0,
        },
    }
    if not stats["enabled"]:
        return stats

    for work in works:
        if not work.get("abstract") and work.get("abstract_inverted_index"):
            work["abstract"] = _reconstruct_abstract(work["abstract_inverted_index"])

    targets = [work for work in works if not str(work.get("abstract") or "").strip()]
    stats["targets"] = len(targets)
    selected_targets = targets
    max_targets = _int_env("BACKFILL_MAX_TARGETS", 25, 0)
    if max_targets and len(targets) > max_targets:
        selected_targets, candidate_count = _priority_elsevier_journal_targets(
            targets,
            max_targets,
        )
        stats["limited"] = True
        stats["priority_candidates"] = candidate_count
        stats["priority_selected"] = len(selected_targets)
        if not selected_targets:
            stats["skipped"] = True
            stats["reason"] = (
                f"{len(targets)} missing-abstract works exceed "
                f"BACKFILL_MAX_TARGETS={max_targets}; no "
                f"{BACKFILL_PRIORITY_LABEL} found"
            )
            return stats
        stats["reason"] = (
            f"{len(targets)} missing-abstract works exceed "
            f"BACKFILL_MAX_TARGETS={max_targets}; selected "
            f"{len(selected_targets)} {BACKFILL_PRIORITY_LABEL}"
        )

    doi_by_work: list[tuple[dict[str, Any], str]] = []
    for work in selected_targets:
        doi = _doi(work)
        if doi:
            doi_by_work.append((work, doi))
        else:
            stats["no_doi"] += 1
    if not doi_by_work:
        return stats

    stats["selected"] = len(doi_by_work)
    unique_dois = list(dict.fromkeys(doi for _, doi in doi_by_work))
    concurrency = _int_env("BACKFILL_CONCURRENCY", 4, 1, 8)
    semaphore = asyncio.Semaphore(concurrency)
    elsevier_client = None
    try:
        from . import get_elsevier

        elsevier_client = get_elsevier()
    except Exception:
        pass

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        async def resolve(doi: str) -> tuple[str, str, str]:
            async with semaphore:
                abstract, source = await _resolve_one(client, doi, elsevier_client)
                return doi, abstract, source

        resolved = await asyncio.gather(*(resolve(doi) for doi in unique_dois))

    by_doi = {doi: (abstract, source) for doi, abstract, source in resolved}
    for work, doi in doi_by_work:
        stats["attempted"] += 1
        abstract, source = by_doi.get(doi, ("", ""))
        if abstract:
            work["abstract"] = abstract
            stats["filled"] += 1
            stats["sources"][source] += 1
        else:
            stats["failed"] += 1
    return stats
