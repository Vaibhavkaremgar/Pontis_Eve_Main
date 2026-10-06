"""Deterministic, conservative skill aliases shared by ingestion and matching."""
import re
from typing import Any, Iterable

_ALIASES = {
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "postgresql database": "PostgreSQL",
    "gcp": "Google Cloud Platform", "google cloud": "Google Cloud Platform",
    "google cloud platform": "Google Cloud Platform",
    "js": "JavaScript", "javascript": "JavaScript",
    "ts": "TypeScript", "typescript": "TypeScript", "type script": "TypeScript",
    "react": "React", "react.js": "React", "reactjs": "React", "react js": "React",
    "node": "Node.js", "node.js": "Node.js", "nodejs": "Node.js", "node js": "Node.js",
}

def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())

def canonical_skill(value: Any) -> str:
    """Return a display-safe canonical label only for explicitly safe aliases."""
    text = _text(value)
    return _ALIASES.get(text.casefold(), text)

def canonical_skill_key(value: Any) -> str:
    canonical = canonical_skill(value)
    return re.sub(r"[\s._-]+", "", canonical.casefold())

def skill_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        value = value.get("name") or value.get("skill") or value.get("title")
    if isinstance(value, (list, tuple, set)):
        return [part for item in value for part in skill_values(item)]
    return [part.strip() for part in re.split(r"[,;|\n\r\u2022]+", _text(value)) if part.strip()]

def merge_skills(*sources: Iterable[Any] | Any, canonical_display: bool = False) -> list[str]:
    """Monotonically merge skill sources, retaining the first display spelling."""
    result: list[str] = []
    seen: set[str] = set()
    for source in sources:
        for value in skill_values(source):
            key = canonical_skill_key(value)
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(canonical_skill(value) if canonical_display else _text(value))
    return result

def canonical_skill_set(value: Any) -> set[str]:
    return {canonical_skill_key(item) for item in skill_values(value) if canonical_skill_key(item)}
