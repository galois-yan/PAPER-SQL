"""download_pdf tool — download OA PDFs via OpenAlex Content API."""

from __future__ import annotations

from pathlib import Path

from .client import OpenAlexClient

DEFAULT_PDF_DIR = Path.home() / ".AI-CACHE" / "openalex" / "pdfs"
PROJECT_PDF_DIR = Path.cwd() / "pdfs"


def _human_size(nbytes: int) -> str:
    """Convert a byte count to a human-readable size string."""
    if nbytes < 1024:
        return f"{nbytes} B"
    if nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.1f} KB"
    return f"{nbytes / (1024 * 1024):.1f} MB"


async def download_pdf(
    client: OpenAlexClient,
    work_ids: str,
    save_to_project: bool = False,
) -> str:
    """Download OA PDFs for one or more OpenAlex work IDs via the Content API.

    Args:
        client: OpenAlexClient instance.
        work_ids: Comma-separated OpenAlex work IDs, e.g.
            ``"W2741809807,W3038568908"``.
        save_to_project: Set to ``True`` only when the user explicitly asks
            for PDFs in the project directory. Otherwise it must remain
            ``False`` and PDFs are saved to ``~/.AI-CACHE/openalex/pdfs/``.
            This tool never accepts an arbitrary output path.

    Returns:
        Markdown summary table with status, title, and file path.
    """
    ids_raw = [v.strip() for v in work_ids.split(",") if v.strip()]
    if not ids_raw:
        return (
            'Error: No work IDs provided. Use comma-separated OpenAlex IDs, '
            'e.g. "W2741809807,W3038568908".'
        )

    bad = [i for i in ids_raw if not i.upper().startswith("W")]
    if bad:
        return (
            f"Error: {len(bad)} invalid work ID(s): {', '.join(bad)}. "
            "All IDs must start with 'W'."
        )

    pdf_dir = PROJECT_PDF_DIR if save_to_project else DEFAULT_PDF_DIR
    pdf_dir.mkdir(parents=True, exist_ok=True)

    # Batch-fetch metadata
    try:
        data = await client.search(
            query=work_ids,
            mode="ids",
            per_page=min(len(ids_raw), 100),
        )
        results: list[dict] = data.get("results", [])
    except (ValueError, RuntimeError) as e:
        return f"Error fetching work metadata: {e}"

    # Build lookup
    meta_lookup: dict[str, tuple[str, bool]] = {}
    for r in results:
        wid = r.get("id", "")
        if "/" in wid:
            wid = wid.rsplit("/", 1)[-1]
        title = r.get("title") or "(no title)"
        has_content = r.get("has_content") or {}
        has_pdf = bool(has_content.get("pdf", False))
        meta_lookup[wid.upper()] = (title, has_pdf)

    rows: list[dict] = []
    success = 0
    skipped = 0
    failed = 0

    for wid in ids_raw:
        wid_upper = wid.upper()
        meta = meta_lookup.get(wid_upper)

        if meta is None:
            rows.append(
                {
                    "work_id": wid_upper,
                    "title": "? (not found)",
                    "status": "❌ Not found in OpenAlex",
                    "file": "-",
                }
            )
            failed += 1
            continue

        title, has_pdf = meta

        if not has_pdf:
            rows.append(
                {
                    "work_id": wid_upper,
                    "title": title,
                    "status": "⚠️ No cached PDF",
                    "file": "-",
                }
            )
            skipped += 1
            continue

        output_path = pdf_dir / f"{wid_upper}.pdf"

        if output_path.exists():
            size = _human_size(output_path.stat().st_size)
            rows.append(
                {
                    "work_id": wid_upper,
                    "title": title,
                    "status": f"✅ Already downloaded ({size})",
                    "file": str(output_path),
                }
            )
            skipped += 1
            continue

        try:
            saved_path, file_size = await client.download_pdf(wid_upper, output_path)
            size_str = _human_size(file_size)
            rows.append(
                {
                    "work_id": wid_upper,
                    "title": title,
                    "status": f"✅ Downloaded ({size_str})",
                    "file": str(saved_path),
                }
            )
            success += 1
        except FileNotFoundError:
            rows.append(
                {
                    "work_id": wid_upper,
                    "title": title,
                    "status": "⚠️ Not available (404)",
                    "file": "-",
                }
            )
            failed += 1
        except (ValueError, RuntimeError) as e:
            rows.append(
                {
                    "work_id": wid_upper,
                    "title": title,
                    "status": f"❌ {e}",
                    "file": "-",
                }
            )
            failed += 1

    total = len(ids_raw)
    lines = [
        "## PDF Download Results",
        "",
        f"**{success}** downloaded, **{skipped}** skipped, "
        f"**{failed}** failed (of {total} total)",
        "",
        "| Work ID | Title | Status | File |",
        "|---------|-------|--------|------|",
    ]

    for r in rows:
        title_escaped = r["title"].replace("|", "\\|")
        file_escaped = r["file"].replace("|", "\\|")
        lines.append(
            f"| {r['work_id']} | {title_escaped} | {r['status']} | {file_escaped} |"
        )

    if success > 0:
        lines.append("")
        lines.append(f"📁 Output directory: `{pdf_dir}`")

    if success > 0:
        lines.append("")
        lines.append(
            f"💰 Content API cost: ~${success * 0.01:.2f} "
            f"({success} file{'s' if success > 1 else ''} × $0.01)"
        )

    return "\n".join(lines)
