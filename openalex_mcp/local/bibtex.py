"""BibTeX constants, formatting, and abstract reconstruction for local library."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

# ============================================================================
# Type & language maps
# ============================================================================

_ENTRY_TYPE_MAP: dict[str, str] = {
    # Journal articles ("article" is the post-2023 OpenAlex type;
    # "journal-article" kept for rows stored before the rename).
    "article": "article",
    "journal-article": "article",
    "review": "article",
    "data-paper": "article",
    "letter": "article",
    "editorial": "article",
    "newspaper-article": "article",
    # Books & chapters
    "book": "book",
    "book-chapter": "incollection",
    "book-part": "incollection",
    "book-section": "incollection",
    "book-track": "incollection",
    "book-series": "book",
    "book-set": "book",
    "reference-book": "book",
    "reference-entry": "incollection",
    # Conference proceedings
    "proceedings": "proceedings",
    "proceedings-article": "inproceedings",
    "conference-paper": "inproceedings",
    "conference-abstract": "misc",
    "conference-poster": "misc",
    # Theses & reports
    "dissertation": "phdthesis",
    "report": "techreport",
    "report-component": "techreport",
    "standard": "techreport",
    # Preprints
    "preprint": "misc",
    "pre-print": "misc",
    "article-version": "misc",
    # Software & datasets
    "software": "misc",
    "dataset": "misc",
    # Everything else
    "erratum": "misc",
    "retraction": "misc",
    "news": "misc",
    "grant": "misc",
    "paratext": "misc",
    "peer-review": "misc",
    "other": "misc",
}

_LANGUAGE_MAP: dict[str, str] = {
    "en": "english", "zh": "chinese", "ja": "japanese", "ko": "korean",
    "fr": "french", "de": "german", "es": "spanish", "ru": "russian",
    "pt": "portuguese", "it": "italian", "ar": "arabic", "nl": "dutch",
    "sv": "swedish", "no": "norwegian", "da": "danish", "fi": "finnish",
    "pl": "polish", "tr": "turkish", "cs": "czech", "hu": "hungarian",
    "ro": "romanian", "el": "greek", "he": "hebrew", "th": "thai",
    "vi": "vietnamese", "id": "indonesian", "ms": "malay", "hi": "hindi",
    "bn": "bengali", "fa": "persian", "uk": "ukrainian", "ca": "catalan",
    "eu": "basque", "gl": "galician", "sr": "serbian", "hr": "croatian",
    "sk": "slovak", "sl": "slovene", "lt": "lithuanian", "lv": "latvian",
    "et": "estonian", "bg": "bulgarian",
}


# ============================================================================
# Abstract reconstruction
# ============================================================================


def _reconstruct_inverted_index(inverted_index: dict[str, list[int]]) -> str:
    """Reconstruct an abstract from OpenAlex inverted-index format."""
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""

    positions: dict[int, str] = {}
    for word, pos_list in inverted_index.items():
        for pos in pos_list:
            positions[pos] = word

    sorted_words = [positions[k] for k in sorted(positions.keys())]
    return " ".join(sorted_words)


def normalize_abstract(work: dict[str, Any]) -> str | None:
    """Return a normalized abstract string from a work object."""
    inverted = work.get("abstract_inverted_index")
    if inverted and isinstance(inverted, dict):
        return _reconstruct_inverted_index(inverted)

    abstract = work.get("abstract")
    if abstract is None:
        return None
    if isinstance(abstract, str):
        return abstract
    if isinstance(abstract, dict):
        return _reconstruct_inverted_index(abstract)

    return None


# ============================================================================
# BibTeX formatting helpers
# ============================================================================


def parse_author_name(display_name: str) -> str:
    """Convert "GivenName FamilyName" to "FamilyName, GivenName"."""
    name = display_name.strip()
    if not name:
        return name
    if "," in name:
        return name
    parts = name.rsplit(None, 1)
    if len(parts) == 1:
        return parts[0]
    return f"{parts[1]}, {parts[0]}"


_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_LATEX_SPECIAL = re.compile(r"[\\&%$#_{}~^]")


def escape_bibtex(value: str) -> str:
    """Escape LaTeX special characters in BibTeX text fields."""
    return _LATEX_SPECIAL.sub(
        lambda match: _LATEX_ESCAPES[match.group()], str(value or "")
    )


def author_display_names(authors_json: str | None) -> list[str]:
    """Extract author display names from stored OpenAlex authorship JSON."""
    if not authors_json:
        return []
    try:
        authorships = json.loads(authors_json)
    except json.JSONDecodeError:
        return []

    names: list[str] = []
    for authorship in authorships:
        if isinstance(authorship, dict):
            author = authorship.get("author") or {}
            name = author.get("display_name") or authorship.get("display_name") or authorship.get("name")
        else:
            name = str(authorship)
        if name and str(name).strip():
            names.append(str(name).strip())
    return names


def citation_key_base(
    authors_json: str | None,
    publication_year: int | str | None,
    fallback: str,
) -> str:
    """Build a ScholarAgent-style first-author-surname plus year key."""
    names = author_display_names(authors_json)
    if names and publication_year is not None:
        first = names[0]
        surname = first.split(",", 1)[0].strip() if "," in first else first.split()[-1]
        normalized = unicodedata.normalize("NFKD", surname)
        ascii_surname = normalized.encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^A-Za-z0-9]+", "", ascii_surname).lower()
        if slug:
            return f"{slug}{publication_year}"
    return fallback


def citation_key_suffix(index: int) -> str:
    """Return a, b, ..., z, aa, ab for duplicate citation keys."""
    suffix = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        suffix = chr(ord("a") + remainder) + suffix
    return suffix
