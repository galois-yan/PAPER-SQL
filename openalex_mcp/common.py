"""Shared helpers — zero external dependencies, used by both remote/ and local/."""


def short_openalex_id(full_id: str) -> str:
    """Extract short OpenAlex ID from a full URL.

    Example: "https://openalex.org/W2741809807" -> "W2741809807"
    """
    if "/" in full_id:
        return full_id.rsplit("/", 1)[-1]
    return full_id


def extract_concept_names(work: dict) -> list[str]:
    """Extract concept display names from a work dict.

    Handles both ``concepts`` (list of dicts) and ``concepts_json``
    (JSON string) fields.
    """
    concepts = work.get("concepts") or []
    if not concepts:
        import json

        try:
            concepts = json.loads(work.get("concepts_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            concepts = []
    names: list[str] = []
    for c in concepts:
        if isinstance(c, dict):
            name = c.get("display_name", "")
        else:
            name = str(c)
        if name:
            names.append(name)
    return names


def extract_keyword_names(work: dict) -> list[str]:
    """Extract keyword strings from a work dict.

    Handles both ``keywords`` (list of dicts) and ``keywords_json``
    (JSON string) fields.
    """
    keywords = work.get("keywords") or []
    if not keywords:
        import json

        try:
            keywords = json.loads(work.get("keywords_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            keywords = []
    names: list[str] = []
    for kw in keywords:
        if isinstance(kw, dict):
            names.append(kw.get("keyword", str(kw)))
        else:
            names.append(str(kw))
    return names


def extract_source_id(work: dict) -> str | None:
    """Extract the short source ID from a work dict.

    Example: extracts "S4210208519" from ``primary_location.source.id``.
    """
    loc = work.get("primary_location") or {}
    source = loc.get("source") or {}
    source_full_id = source.get("id", "")
    if source_full_id:
        return short_openalex_id(source_full_id)
    return None
