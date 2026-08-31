"""On-demand OA PDF extraction into the local SQLite library."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import httpx
from pypdf import PdfReader

from openalex_mcp.common import short_openalex_id

DEFAULT_MAX_FULLTEXT_CHARS = 300_000
DEFAULT_MAX_FULLTEXT_PAGES = 500
MAX_FULLTEXT_WORKS = 10


def _pdf_candidates(
    work: dict[str, Any],
    *,
    use_openalex_content_api: bool,
    api_key: str,
) -> list[tuple[str, str, dict[str, str] | None]]:
    """Return direct OA PDF candidates, then optional Content API candidate."""
    candidates: list[tuple[str, str, dict[str, str] | None]] = []
    seen: set[str] = set()

    locations = [
        work.get("best_oa_location") or {},
        work.get("primary_location") or {},
        *(work.get("locations") or []),
    ]
    for location in locations:
        url = location.get("pdf_url")
        if url and url not in seen:
            seen.add(url)
            candidates.append(("open_access_pdf", url, None))

    if use_openalex_content_api and api_key:
        work_id = short_openalex_id(work.get("id"))
        has_content = work.get("has_content") or {}
        if work_id and has_content.get("pdf"):
            candidates.append(
                (
                    "openalex_content_pdf",
                    f"https://content.openalex.org/works/{work_id}.pdf",
                    {"api_key": api_key},
                )
            )
    return candidates


def _extract_pdf_text(
    pdf_path: Path,
    *,
    max_pages: int,
    max_chars: int,
) -> str:
    reader = PdfReader(str(pdf_path))
    chunks: list[str] = []
    for page in reader.pages[:max_pages]:
        chunks.append(page.extract_text() or "")
        if sum(len(chunk) for chunk in chunks) >= max_chars:
            break
    text = "\n\n".join(chunks).strip()
    return text[:max_chars]


async def _fetch_candidate_text(
    http: httpx.AsyncClient,
    source: str,
    url: str,
    params: dict[str, str] | None,
    *,
    max_pages: int,
    max_chars: int,
) -> tuple[str, str, int]:
    response = await http.get(url, params=params)
    response.raise_for_status()
    content = response.content
    content_type = response.headers.get("content-type", "")
    if "pdf" not in content_type.lower() and not content.startswith(b"%PDF"):
        raise ValueError(f"response is not a PDF ({content_type or 'unknown content type'})")

    # The PDF is deliberately temporary: only extracted text is persisted.
    with tempfile.TemporaryDirectory(prefix="openalex-fulltext-") as temp_dir:
        pdf_path = Path(temp_dir) / "paper.pdf"
        pdf_path.write_bytes(content)
        text = _extract_pdf_text(
            pdf_path,
            max_pages=max_pages,
            max_chars=max_chars,
        )
    if not text:
        raise ValueError("PDF contained no extractable text")
    return text, source, len(content)


async def fetch_and_store_fulltext(
    client,
    library,
    works: list[dict[str, Any]],
    *,
    limit: int = 2,
    use_openalex_content_api: bool = False,
    max_pages: int = DEFAULT_MAX_FULLTEXT_PAGES,
    max_chars: int = DEFAULT_MAX_FULLTEXT_CHARS,
) -> dict[str, Any]:
    """Fetch selected OA PDFs, extract text, and store text in ``works.fulltext``.

    Network and PDF parsing happen without the library lock. Only the final text
    update acquires the lock. The original PDF is kept only in a temporary
    directory and is deleted as soon as extraction finishes. The explicit
    ``download_pdf`` tool remains the permanent-PDF path.
    """
    api_key = getattr(client, "api_key", "") or ""
    eligible = [
        work
        for work in works
        if _pdf_candidates(
            work,
            use_openalex_content_api=use_openalex_content_api,
            api_key=api_key,
        )
    ]
    selected = eligible[: min(MAX_FULLTEXT_WORKS, max(0, int(limit)))]
    report: dict[str, Any] = {
        "requested": bool(selected),
        "eligible": len(eligible),
        "requested_count": len(selected),
        "stored": 0,
        "failed": 0,
        "unavailable": max(0, len(works) - len(eligible)),
        "papers": [],
    }
    if not selected:
        return report

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=60.0,
        headers={"User-Agent": "openalex-mcp-fulltext/0.1"},
    ) as http:
        for work in selected:
            work_id = short_openalex_id(work.get("id")) or ""
            title = work.get("title") or work.get("display_name") or work_id
            candidates = _pdf_candidates(
                work,
                use_openalex_content_api=use_openalex_content_api,
                api_key=api_key,
            )
            if not candidates:
                report["unavailable"] += 1
                report["papers"].append(
                    {"id": work_id, "title": title, "status": "unavailable"}
                )
                continue

            errors: list[str] = []
            stored = False
            for source, url, params in candidates:
                try:
                    text, used_source, byte_count = await _fetch_candidate_text(
                        http,
                        source,
                        url,
                        params,
                        max_pages=max_pages,
                        max_chars=max_chars,
                    )
                    async with library._get_lock():
                        updated = library.update_work_fulltext(work_id, text)
                    if not updated:
                        raise ValueError("work was not found in the local library")
                    report["stored"] += 1
                    report["papers"].append(
                        {
                            "id": work_id,
                            "title": title,
                            "status": "stored",
                            "source": used_source,
                            "chars": len(text),
                            "bytes": byte_count,
                        }
                    )
                    stored = True
                    break
                except Exception as exc:  # one source failing should not stop the batch
                    errors.append(f"{source}: {exc}")

            if not stored:
                report["failed"] += 1
                report["papers"].append(
                    {
                        "id": work_id,
                        "title": title,
                        "status": "failed",
                        "errors": errors,
                    }
                )
    return report
