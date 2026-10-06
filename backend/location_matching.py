"""Country and location eligibility helpers for job recommendations."""
import re
from typing import Any

COUNTRY_CODES = {
    "india": "IN", "ind": "IN", "in": "IN",
    "united states": "US", "usa": "US", "us": "US", "u.s.": "US",
    "united kingdom": "GB", "uk": "GB", "gb": "GB",
    "canada": "CA", "ca": "CA",
}
CITY_ALIASES = {"bangalore": "bengaluru", "gurgaon": "gurugram", "bombay": "mumbai"}

def _norm(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    return CITY_ALIASES.get(text, text)

def country_code(value: Any) -> str:
    text = _norm(value)
    return COUNTRY_CODES.get(text, text.upper() if len(text) == 2 else "")

def _parts(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    return [str(value).strip()] if str(value or "").strip() else []

def candidate_location(candidate: dict[str, Any]) -> dict[str, str]:
    raw = candidate.get("raw_data") or {}
    if isinstance(raw, str):
        try:
            import json
            raw = json.loads(raw)
        except Exception:
            raw = {}
    prefs = candidate.get("_prefs_row") or {}
    preferred = prefs.get("preferred_locations") if isinstance(prefs, dict) else None
    if isinstance(preferred, str):
        try:
            import json
            preferred = json.loads(preferred)
        except Exception:
            preferred = [preferred]
    preferred = _parts(preferred or (raw.get("preferred_locations") if isinstance(raw, dict) else None))
    explicit_country = candidate.get("country_code") or candidate.get("country") or (raw.get("country_code") or raw.get("country") if isinstance(raw, dict) else "")
    explicit_city = candidate.get("city") or (raw.get("city") if isinstance(raw, dict) else "")
    explicit_state = candidate.get("state") or (raw.get("state") if isinstance(raw, dict) else "")
    location = candidate.get("location") or (raw.get("location") if isinstance(raw, dict) else "")
    text = preferred[0] if preferred else str(location or "")
    pieces = [p.strip() for p in str(text).split(",") if p.strip()]
    if not explicit_country and pieces:
        explicit_country = pieces[-1]
    if not explicit_city and pieces:
        explicit_city = pieces[0]
    if not explicit_state and len(pieces) >= 3:
        explicit_state = pieces[-2]
    return {"country_code": country_code(explicit_country), "city": _norm(explicit_city), "state": _norm(explicit_state), "preferred": " | ".join(preferred)}

def job_location(job: dict[str, Any]) -> dict[str, str]:
    raw = job.get("location") or ""
    pieces = [p.strip() for p in str(raw).split(",") if p.strip()]
    country = job.get("country") or (pieces[-1] if len(pieces) >= 2 else "")
    city = job.get("city") or (pieces[0] if pieces else "")
    state = job.get("state") or (pieces[-2] if len(pieces) >= 3 else "")
    policy = _norm(job.get("remote_policy"))
    scope = country_code(country)
    if "worldwide" in policy or "global" in policy:
        scope = "WORLDWIDE"
    return {"country_code": scope, "city": _norm(city), "state": _norm(state), "remote": "true" if job.get("remote") is True or "remote" in policy else "false", "policy": policy}

def country_eligible(candidate: dict[str, Any], job: dict[str, Any]) -> bool:
    c, j = candidate_location(candidate), job_location(job)
    if not c["country_code"] or not j["country_code"]:
        return False
    if j["country_code"] == "WORLDWIDE":
        return True
    return c["country_code"] == j["country_code"]

def location_preference_score(candidate: dict[str, Any], job: dict[str, Any]) -> float:
    c, j = candidate_location(candidate), job_location(job)
    if not country_eligible(candidate, job):
        return 0.0
    if c["city"] and c["city"] == j["city"]:
        return 1.0
    if c["state"] and c["state"] == j["state"]:
        return 0.8
    return 0.5
