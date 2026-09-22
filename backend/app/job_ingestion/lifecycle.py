"""Single source of truth for whether a job may be shown to candidates."""

# Kept as SQL because callers use raw SQL rather than ORM models.  ``~`` makes
# the URL check case-insensitive and permits only real HTTP(S) URLs.
CANDIDATE_VISIBLE_WHERE = """
    jd.is_active IS TRUE
    AND LOWER(COALESCE(jd.status, '')) IN ('active', 'open', 'published')
    AND LOWER(COALESCE(jd.job_status, '')) IN ('active', 'open', 'published')
    AND NULLIF(BTRIM(jd.job_url), '') IS NOT NULL
    AND BTRIM(jd.job_url) ~* '^https?://[^[:space:]]+$'
"""


def candidate_visible_where(alias: str = "jd") -> str:
    """Return the candidate-visible predicate with the requested table alias."""
    return CANDIDATE_VISIBLE_WHERE.replace("jd.", f"{alias}.")
