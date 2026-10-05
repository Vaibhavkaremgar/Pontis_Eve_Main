"""Conservative normalization for public ATS job-board payloads."""
import html
import re
from datetime import date, datetime, time, timezone
from typing import Any
from urllib.parse import urlsplit


def _valid_http_url(value: Any) -> str | None:
    if not isinstance(value, str): return None
    url = value.strip(); parsed = urlsplit(url)
    return url if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else None

def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None

UNKNOWN_EXPERIENCE_LEVEL = "Not specified"

def _json_safe(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return value

def _skill_list(value: Any) -> list[str]:
    """Return JSON-safe skill strings without treating arbitrary objects as skills."""
    if isinstance(value, str):
        value = re.split(r"[,;/|]", value)
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]

_KNOWN_JOB_SKILLS = (
    "AWS CDK", "CloudFormation", "GitHub Actions", "Spring Boot", "PostgreSQL",
    "MongoDB", "Postgres", "Kubernetes", "Terraform", "Prometheus", "Grafana", "Docker",
    "Python", "Java", "JavaScript", "TypeScript", "Golang", "Go", "Linux",
    "Jenkins", "REST", "REST APIs", "gRPC", "GraphQL", "Redis", "MySQL",
    "SQL", "Git", "Azure", "GCP", "AWS", "Kafka", "FastAPI", "Django",
    "Flask", "React", "Node.js", "Ruby", "C#", ".NET", "PHP", "Shell scripting",
    "Salesforce", "Workday", "Excel",
    "Infrastructure as Code", "CI/CD",
)
_SKILL_ALIASES = {"postgres": "PostgreSQL", "postgresql": "PostgreSQL"}

def _normalize_skill_values(values: Any) -> list[str]:
    """Return concise, case-insensitively deduplicated professional skills."""
    result: list[str] = []
    seen: set[str] = set()
    for value in _skill_list(values):
        clean = re.sub(r"\s+", " ", value).strip(" .;:-")
        if not clean or len(clean.split()) > 5 or len(clean) > 60:
            continue
        clean = _SKILL_ALIASES.get(clean.casefold(), clean)
        key = clean.casefold()
        if key not in seen:
            seen.add(key); result.append(clean)
    return result

def _extract_jd_skills(description: Any) -> list[str]:
    """Extract known technical skills from labelled sections and JD prose."""
    text = _html_text(description)
    found: list[str] = []
    for skill in sorted(_KNOWN_JOB_SKILLS, key=len, reverse=True):
        if skill == "Go" and not _mentions_go_language(text):
            continue
        # The newly added business tools use ordinary whole-word boundaries so
        # punctuation such as ``Excel.`` is accepted while variants such as
        # ``excelled`` are rejected.  Keep the established stricter pattern
        # for the pre-existing vocabulary (notably C#/.NET handling).
        pattern = r"\bGo\b" if skill == "Go" else (
            rf"(?<!\w){re.escape(skill)}(?!\w)"
            if skill in {"Salesforce", "Workday", "Excel"}
            else rf"(?<![\w+#.]){re.escape(skill)}(?![\w+#.])"
        )
        if re.search(pattern, text, re.I):
            found.append(skill)
    return _normalize_skill_values(found)


_GO_LANGUAGE_CONTEXT = re.compile(
    r"(?:"
    r"\bgo\s*/\s*golang\b|"
    r"\bgo\s+(?:programming|language|development|developer|services|applications)\b|"
    r"\b(?:written|write|writing)\s+(?:in\s+)?go\b|"
    r"\b(?:experience|proficiency)\s+(?:with|in)\s+go\b|"
    r"\bgo\s*(?:,|and|or)\s*(?:golang|python|java|javascript|typescript|ruby|c#|php)\b|"
    r"\b(?:golang|python|java|javascript|typescript|ruby|c#|php)\s*(?:,|and|or)\s*go\b"
    r")",
    re.I,
)


def _mentions_go_language(text: str) -> bool:
    """Return whether ``Go`` is used as the programming language, not a verb."""
    return bool(_GO_LANGUAGE_CONTEXT.search(text))

def _ats_id(value: Any) -> str:
    """Do not turn a missing provider ID into the literal string ``'None'``."""
    return str(value).strip() if value is not None else ""

def _first(*values: Any) -> Any:
    return next((v for v in values if v not in (None, "", [], {})), None)

def parse_ats_datetime(value: Any) -> datetime | None:
    """Return an UTC-aware datetime from public ATS date representations.

    PostgreSQL's asyncpg driver validates Python bind values before applying
    SQL casts.  Keep dates as datetime objects here rather than serializing
    them to ISO strings, so the value is safe to bind to ``timestamptz``.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and value > 946684800000:
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            # ``fromisoformat`` accepts offsets and fractional seconds.  Python
            # versions before 3.11 do not consistently accept a trailing Z.
            parsed = datetime.fromisoformat(
                f"{raw[:-1]}+00:00" if raw.endswith(("Z", "z")) else raw
            )
        except (TypeError, ValueError, OverflowError):
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    return None


# Kept as a local compatibility alias for callers that imported the former
# helper.  New code should use the explicit, shared parser above.
_date = parse_ats_datetime

def _html_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"</?(?:p|li|ul|ol|h[1-6]|br)\b[^>]*>", "\n", text, flags=re.I)
    return re.sub(r"<[^>]+>", "", text)

def _location_parts(value: Any) -> dict[str, Any]:
    """Extract reliable location components while retaining the raw value."""
    raw = value
    if isinstance(value, dict):
        raw = _first(value.get("name"), value.get("location"), value.get("city"), value.get("raw"))
        city, state, country = value.get("city"), value.get("state"), value.get("country")
    else:
        city = state = country = None
    text = str(raw).strip() if raw not in (None, "") else None
    parts = [p.strip() for p in text.split(",")] if text else []
    if text and not city and len(parts) >= 2: city = parts[0]
    if text and not state and len(parts) >= 3: state = parts[-2]
    if text and not country and len(parts) >= 2: country = parts[-1]
    return {"location": text, "city": city, "state": state, "country": country,
            "raw": raw}

def _section_fields(description: Any) -> tuple[str | None, str | None]:
    text = _html_text(description).strip()
    labels = r"Requirements|Required Qualifications|Qualifications|Skills|Required Skills|What You Bring|Must Have|Responsibilities|What You'll Do|Duties|Key Responsibilities|Your Responsibilities"
    matches = list(re.finditer(rf"(?im)^\s*(?P<label>{labels})\s*:?[ \t]*$", text))
    values = {"requirements": [], "responsibilities": []}
    req = {"requirements", "required qualifications", "qualifications", "skills", "required skills", "what you bring", "must have"}
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[match.end():end].strip(" \n:-")
        key = "requirements" if match.group("label").casefold() in req else "responsibilities"
        if body: values[key].append(body)
    return ("\n\n".join(values["requirements"]) or None, "\n\n".join(values["responsibilities"]) or None)

def _experience_evidence(description: Any) -> list[dict[str, Any]]:
    text = _html_text(description)
    pattern = re.compile(r"(?<!\w)(?P<value>(?:(?:minimum|at\s+least)\s+)?\d+(?:\.\d+)?\s*(?:\+|[-\u2013\u2014]\s*\d+(?:\.\d+)?|to\s+\d+(?:\.\d+)?)?\s*(?:years?|yrs?)\b)", re.I)
    result = []
    for match in pattern.finditer(text):
        value = re.sub(r"\s+", " ", match.group("value")).strip()
        nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", value)]
        result.append({"text": value, "minimum_years": nums[0] if nums else None,
                       "maximum_years": nums[1] if len(nums) > 1 else None,
                       "section": text[max(0, text.rfind("\n", 0, match.start()) + 1):match.start()].strip() or None,
                       "source": text[max(0, match.start()-80):match.end()+80]})
    return result

def _jd_evidence(description: Any) -> dict[str, Any]:
    """Extract only values stated under an explicit label in the JD."""
    text, found = _html_text(description), {}
    patterns = {
        "employment_type": r"(?im)^\s*(?:employment|job)\s*type\s*[:\-]\s*(full[ -]?time|part[ -]?time|contract|temporary|internship)\b",
        "remote_policy": r"(?im)^\s*(?:workplace|work location|remote policy)\s*[:\-]\s*(remote|hybrid|on[ -]?site)\b",
        "experience_level": r"(?im)^\s*(?:experience level|seniority|senior level)\s*[:\-]\s*([^\n]{2,80})",
        "experience_required": r"(?im)^\s*(?:experience required|required experience)\s*[:\-]\s*([^\n]{2,100})",
        "salary_range": r"(?im)^\s*(?:salary range|salary|compensation)\s*[:\-]\s*([^\n]{3,100})",
        "skills_required": r"(?im)^\s*(?:required skills|technical skills|skills required)\s*[:\-]\s*([^\n]{2,300})",
    }
    for key, pattern in patterns.items():
        if match := re.search(pattern, text):
            value = match.group(1).strip(" .;")
            found[key] = [v.strip() for v in re.split(r"[,;/|]", value) if v.strip()] if key == "skills_required" else value
    return found

def _experience_from_text(description: Any) -> str | None:
    """Return the strongest candidate-experience requirement in a JD.

    All matches are scored: explicit ``of experience`` language wins, while
    company-history phrases (``for over N years``, ``founded N years ago``)
    are rejected.  Ties prefer the general requirement over a specialized
    ``including ...`` clause, making the result independent of prose order.
    """
    text = _html_text(description)
    written_numbers = {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    }
    written_number = r"(?:" + "|".join(written_numbers) + r")"
    pattern = re.compile(
        r"(?<!\w)(?P<value>(?:(?:minimum(?:\s+of)?|at\s+least|over|mindestens)\s+)?"
        rf"(?:\d+(?:\.\d+)?|(?<!\w){written_number}(?!\w))\s*(?:\+|[-\u2013\u2014]\s*(?:\d+(?:\.\d+)?|(?<!\w){written_number}(?!\w))|to\s+(?:\d+(?:\.\d+)?|(?<!\w){written_number}(?!\w)))?\s*"
        r"(?:years?|yrs?|jahre)\b)(?P<qualifier>\s+(?:of\s+(?:relevant\s+)?(?:hands[- ]on\s+)?experience|(?:relevante\s+)?erfahrung))?",
        re.I,
    )
    candidates = []
    for match in pattern.finditer(text):
        value = re.sub(r"\s+", " ", match.group("value")).strip()
        qualifier = re.sub(r"\s+", " ", match.group("qualifier") or "").strip()
        context = text[max(0, match.start() - 45):match.end() + 65]
        before = text[max(0, match.start() - 35):match.start()]
        if re.search(r"(?:for\s+over)\s*$|(?:founded|established)\s*$", before, re.I):
            continue
        if re.search(r"(?:in business|serving clients|serving generations|providing services|years ago|years of (?:company\s+)?history|jahre der unternehmensgeschichte)", context, re.I):
            continue
        after = text[match.end():match.end() + 90]
        score = 0
        if re.match(r"\s+(?:of\s+(?:relevant\s+)?(?:hands[- ]on\s+)?experience|(?:relevante\s+)?erfahrung)\b", after, re.I): score += 100
        if re.search(r"\b(?:candidate|applicant|ideal candidate|looking for|you|kandidat(?:en|in)?)\b", before, re.I): score += 20
        if re.search(r"\bincluding\b", before, re.I): score -= 15
        # Store one stable numeric representation, never the surrounding
        # qualification paragraph.  Ranges use a simple hyphen and minimum
        # requirements use a plus sign.
        numbers = re.findall(rf"\d+(?:\.\d+)?|(?<!\w){written_number}(?!\w)", value, re.I)
        numbers = [written_numbers.get(number.casefold(), number) for number in numbers]
        if len(numbers) > 1:
            normalized = f"{numbers[0]}-{numbers[1]} years"
        elif numbers:
            minimum_suffix = "+" if re.match(r"(?:minimum|at\s+least|over|mindestens)\b", value, re.I) or "+" in value else ""
            normalized = f"{numbers[0]}{minimum_suffix} years"
        else:
            continue
        candidates.append((score, match.start(), normalized))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], -item[1]))[2]

def _lever_required_skills(job: dict[str, Any], description: Any) -> list[str]:
    """Extract explicit skills from Lever's required-qualification lists."""
    skills: list[str] = []
    for section in job.get("lists") or []:
        if not isinstance(section, dict):
            continue
        heading = _html_text(section.get("text"))
        if not re.search(r"required|qualification|must have|skill", heading, re.I):
            continue
        content = str(section.get("content") or "")
        for item in re.findall(r"<li\b[^>]*>(.*?)</li>", content, re.I | re.S):
            value = re.sub(r"\s+", " ", _html_text(item)).strip(" .;:-")
            if value and not re.search(r"\b\d+\s*\+?\s*(?:years?|yrs?)\b|degree|bachelor|master|location", value, re.I):
                skills.append(value)
    if not skills:
        skills = _skill_list(_jd_evidence(description).get("skills_required"))
    return list(dict.fromkeys(skills))

def _metadata(job: dict[str, Any], source: str, *, description: Any, **explicit: Any) -> dict[str, Any]:
    evidence = _jd_evidence(description)
    metadata_keys = ("employment_type", "remote_policy", "experience_level", "experience_required", "salary_range", "skills_required", "created_at")
    result = {key: _first(explicit.get(key), evidence.get(key)) for key in metadata_keys}
    # Preserve an explicit years requirement even when a provider omits its
    # dedicated field. This applies uniformly to all normalized sources.
    result["experience_required"] = _first(_experience_from_text(description), result["experience_required"])
    # ``skills_required`` is a required JSON column.  Preserve explicitly
    # supplied ATS skills first, then the conservative labelled-JD extraction,
    # and represent a genuinely unknown skill set as an empty JSON list rather
    # than SQL NULL.  This is intentionally not a guessed skill list.
    result["skills_required"] = _normalize_skill_values([
        *_skill_list(result["skills_required"]),
        *_skill_list(evidence.get("skills_required")),
        *_extract_jd_skills(description),
    ])
    result["skills"] = result.get("skills_required")
    # This column is NOT NULL.  A stated requirement (for example, "5+ years")
    # is useful evidence but is deliberately not converted into a guessed
    # seniority bucket.  When neither source states experience, retain an
    # explicit unknown representation rather than SQL NULL.
    result["experience_level"] = _text(result["experience_level"]) or _text(result["experience_required"]) or UNKNOWN_EXPERIENCE_LEVEL
    categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
    source_data = {k: v for k, v in {
        "requisition_id": _first(job.get("requisition_id"), job.get("requisitionId"), job.get("requisition")),
        "workplace_type": _first(job.get("workplaceType"), job.get("workplace_type")),
        "all_locations": categories.get("allLocations"),
        "departments": job.get("departments"), "offices": job.get("offices"),
        "team": job.get("team"), "compensation": job.get("compensation"),
        "metadata": job.get("metadata"), "custom_fields": job.get("customFields"),
        "location_details": job.get("location") if isinstance(job.get("location"), dict) else None,
        "workable_code": _first(job.get("shortcode"), job.get("code")),
    }.items() if v not in (None, "", [], {})}
    result["structured_data"] = {"source": source, "raw_source": {"provider": source, "schema_version": 1, "payload": _json_safe(job)},
                                  "experience_requirements": _experience_evidence(description), **source_data}
    result["requirements"], result["responsibilities"] = _section_fields(description)
    return result

def extract_lever_job_url(job: dict[str, Any]) -> str | None:
    urls = job.get("urls") if isinstance(job.get("urls"), dict) else {}
    return next((url for value in (urls.get("show"), job.get("hostedUrl"), urls.get("apply")) if (url := _valid_http_url(value))), None)

def extract_ashby_job_url(job: dict[str, Any]) -> str | None:
    return next((url for value in (job.get("jobUrl"), job.get("applyUrl")) if (url := _valid_http_url(value))), None)

def _lever_description(job: dict[str, Any]) -> str | None:
    parts = [_text(job.get(key)) for key in ("descriptionPlain", "description", "descriptionHtml")]
    for section in job.get("lists") or []:
        if isinstance(section, dict): parts.extend(filter(None, [_text(section.get("text")), _text(_html_text(section.get("content")))]))
    unique = list(dict.fromkeys(part for part in parts if part))
    return "\n".join(unique) or None

def normalize_greenhouse(job: dict[str, Any], company_name: str) -> dict[str, Any]:
    description = job.get("content")
    # Greenhouse's public board response supplies ``updated_at`` but no
    # original posted/created timestamp.  It must not be treated as a posting
    # date: leaving this as None lets persistence retain an existing source
    # timestamp or use its established NOW() fallback for a new row.
    meta = _metadata(job, "greenhouse", description=description, experience_level=_first(job.get("experience_level"), job.get("seniority")), experience_required=job.get("experience_required"), remote_policy=_first(job.get("remote_policy"), job.get("workplace_type")), skills_required=_first(job.get("skills_required"), job.get("skills")), created_at=None)
    departments = job.get("departments") or []
    loc = _location_parts(job.get("location")); meta["locations"] = [loc] if loc.get("location") else []
    return {"ats_job_id": _ats_id(job.get("id")), "company_name": company_name, "title": job.get("title"), "description": description, "department": ", ".join(d.get("name") for d in departments if isinstance(d, dict) and d.get("name")) or None, **loc, "remote": bool(job.get("remote")) if isinstance(job.get("remote"), bool) else None, "employment_type": meta.get("employment_type"), "salary_range": meta.get("salary_range"), "job_url": job.get("absolute_url"), "ats_type": "greenhouse", **meta}

def normalize_lever(job: dict[str, Any], company_name: str) -> dict[str, Any]:
    description = _lever_description(job); categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
    experience = _first(job.get("experience_required"), _jd_evidence(description).get("experience_required"), _experience_from_text(description))
    skills = _first(job.get("skills"), job.get("skillsRequired"), _lever_required_skills(job, description))
    meta = _metadata(job, "lever", description=description, employment_type=categories.get("commitment"), experience_level=_first(job.get("experience_level"), job.get("seniority"), categories.get("seniority")), experience_required=experience, remote_policy=_first(job.get("workplaceType"), categories.get("workplace")), created_at=parse_ats_datetime(_first(job.get("createdAt"), job.get("created_at"))), skills_required=skills)
    loc = _location_parts(categories.get("location")); meta["locations"] = [_location_parts(v) for v in categories.get("allLocations", [])] if isinstance(categories.get("allLocations"), list) else ([loc] if loc.get("location") else [])
    return {"ats_job_id": _ats_id(job.get("id")), "company_name": company_name, "title": job.get("text"), "description": description, **loc, "remote": _first(job.get("isRemote"), str(meta.get("remote_policy", "")).casefold() == "remote"), "department": categories.get("team"), "salary_range": meta.get("salary_range"), "job_url": extract_lever_job_url(job), "ats_type": "lever", **meta}

def normalize_ashby(job: dict[str, Any], company_name: str) -> dict[str, Any]:
    description = _first(job.get("descriptionHtml"), job.get("description")); compensation = job.get("compensation") if isinstance(job.get("compensation"), dict) else {}
    salary = _first(job.get("salaryRange"), job.get("salary_range"), compensation.get("summary"))
    if not salary and compensation.get("minValue") is not None and compensation.get("maxValue") is not None: salary = f"{compensation.get('currency') or ''} {compensation['minValue']} - {compensation['maxValue']} {compensation.get('interval') or ''}".strip()
    # ``publishedAt`` is Ashby's public posting timestamp.  Do not substitute
    # other lifecycle timestamps when it is absent or malformed.
    meta = _metadata(job, "ashby", description=description, employment_type=_first(job.get("employmentType"), job.get("employment_type")), experience_level=_first(job.get("experienceLevel"), job.get("experience_level"), job.get("seniority")), experience_required=job.get("experienceRequired"), remote_policy=_first(job.get("workplaceType"), "Remote" if job.get("isRemote") is True else None), salary_range=salary, created_at=parse_ats_datetime(job.get("publishedAt")), skills_required=_first(job.get("skills"), job.get("skillsRequired")))
    department = job.get("department") if isinstance(job.get("department"), dict) else {}
    raw_locations = job.get("locations") or job.get("location")
    loc_values = raw_locations if isinstance(raw_locations, list) else [raw_locations]
    loc = _location_parts(loc_values[0] if loc_values else None); meta["locations"] = [_location_parts(v) for v in loc_values if v not in (None, "", {})]
    meta["salary"] = compensation
    return {"ats_job_id": _ats_id(job.get("id")), "company_name": company_name, "title": job.get("title"), "description": description, **loc, "remote": job.get("isRemote") if isinstance(job.get("isRemote"), bool) else None, "department": department.get("name"), "salary_range": meta.get("salary_range"), "job_url": extract_ashby_job_url(job), "ats_type": "ashby", **meta}

def normalize_workable(job: dict[str, Any], company_name: str) -> dict[str, Any]:
    description = job.get("description"); location = job.get("location")
    meta = _metadata(job, "workable", description=description, employment_type=job.get("employment_type"), experience_level=_first(job.get("experience_level"), job.get("seniority")), experience_required=job.get("experience_required"), remote_policy=_first(job.get("remote_policy"), job.get("workplace_type"), "Remote" if isinstance(location, dict) and location.get("telecommuting") else None), salary_range=_first(job.get("salary_range"), job.get("salary")), created_at=parse_ats_datetime(_first(job.get("published_on"), job.get("published_at"), job.get("created_at"))), skills_required=_first(job.get("skills"), job.get("skills_required")))
    return {"ats_job_id": _ats_id(job.get("id")), "company_name": company_name, "title": job.get("title"), "description": description, "department": job.get("department"), "location": location.get("city") if isinstance(location, dict) else location, "salary_range": meta.get("salary_range"), "job_url": job.get("url"), "ats_type": "workable", **meta}

def normalize_fantastic(job: dict[str, Any]) -> dict[str, Any]:
    """Normalize a global Fantastic record without inventing a registry company."""
    locations = _first(job.get("locations_derived"), job.get("locations_alt"))
    if isinstance(locations, (list, tuple)):
        locations = ", ".join(str(item) for item in locations if item)
    skills = [*_skill_list(job.get("ai_key_skills")), *_skill_list(job.get("ai_keywords"))]
    salary = _first(job.get("salary_range"), job.get("salary"), job.get("ai_salary_range"))
    description = _first(job.get("description_text"), job.get("description"))
    extraction_text = "\n".join(dict.fromkeys(filter(None, (
        description, job.get("ai_requirements_summary"), job.get("requirements"),
    )))) or None
    meta = _metadata(job, "fantastic", description=extraction_text,
        employment_type=_first(job.get("employment_type"), job.get("ai_employment_type")),
        experience_level=job.get("ai_experience_level"),
        experience_required=_experience_from_text(extraction_text),
        remote_policy=job.get("ai_work_arrangement"), salary_range=salary, skills_required=skills,
        # Fantastic preserves the source job's original publication time in
        # ``date_posted``.  ``date_created`` is the Fantastic record's own
        # creation time and must not replace the source posting timestamp.
        created_at=parse_ats_datetime(job.get("date_posted")))
    useful = ("source", "source_type", "source_domain", "source_slug", "domain_derived", "date_posted",
              "date_created", "date_valid_through", "ai_employment_type", "ai_experience_level",
              "ai_requirements_summary", "ai_core_responsibilities", "ai_work_arrangement",
              "ai_key_skills", "ai_keywords", "ai_taxonomies", "ai_education")
    meta["structured_data"].update({key: job[key] for key in useful if job.get(key) not in (None, "", [], {})})
    meta["structured_data"]["fantastic"] = {key: value for key, value in job.items() if key not in {"description_text", "description"}}
    meta["valid_through"] = parse_ats_datetime(job.get("date_valid_through"))
    if not meta.get("responsibilities") and _text(job.get("ai_core_responsibilities")):
        meta["responsibilities"] = _text(job.get("ai_core_responsibilities"))
    meta["locations"] = [_location_parts(v) for v in (job.get("locations_derived") or job.get("locations_alt") or [])] if isinstance((job.get("locations_derived") or job.get("locations_alt")), list) else []
    loc = _location_parts(locations)
    countries = job.get("countries_derived") or job.get("countries")
    if isinstance(countries, (list, tuple)):
        countries = next((value for value in countries if _text(value)), None)
    if _text(countries):
        loc["country"] = _text(countries)
    from location_matching import country_code
    normalized_country_code = country_code(loc.get("country"))
    if normalized_country_code == "IN":
        loc["country"] = "India"
    return {"ats_job_id": _ats_id(job.get("id")), "ats_type": "fantastic",
            "company_name": _text(job.get("organization")) or _text(job.get("organization_name")) or "Unknown organization",
            "title": _text(job.get("title")), "description": description, "department": _text(job.get("department")),
            **loc, "country_code": normalized_country_code,
            "job_url": _valid_http_url(job.get("url")), "salary_range": meta["salary_range"], **meta}

def normalize_theirstack(job: dict[str, Any]) -> dict[str, Any]:
    company_object = job.get("company_object") if isinstance(job.get("company_object"), dict) else {}
    company = job.get("company") if isinstance(job.get("company"), dict) else {}
    company_name = _text(_first(
        job.get("company"),
        job.get("company_name"),
        company_object.get("name"),
        company.get("name"),
        job.get("employer"),
        job.get("organization"),
    )) or "Unknown company"
    location = _first(job.get("location"), job.get("job_location"), job.get("locations"))
    if isinstance(location, list): location = location[0] if location else None
    description = _first(job.get("description"), job.get("description_text"), job.get("job_description"))
    salary = _first(job.get("salary_range"), job.get("salary"), job.get("compensation"))
    meta = _metadata(job, "theirstack", description=description, employment_type=_first(job.get("employment_status"), job.get("employment_type")), experience_level=_first(job.get("job_seniority"), job.get("experience_level")), experience_required=_first(job.get("experience_required"), _experience_from_text(description)), remote_policy=_first(job.get("remote_policy"), job.get("workplace_type")), salary_range=salary, skills_required=_first(job.get("skills"), job.get("technologies")), created_at=parse_ats_datetime(_first(job.get("posted_at"), job.get("discovered_at"))))
    meta["structured_data"]["theirstack"] = _json_safe(job)
    meta["structured_data"]["source"] = _first(job.get("source"), job.get("source_domain"), job.get("job_board"))
    meta["structured_data"]["source_domain"] = _first(job.get("source_domain"), job.get("domain"))
    for key in ("company", "company_domain", "company_object"):
        if job.get(key) not in (None, "", [], {}):
            meta["structured_data"][key] = _json_safe(job[key])
    for key in ("name", "country", "country_code", "linkedin_url"):
        if company_object.get(key) not in (None, "", [], {}):
            meta["structured_data"].setdefault("company_object", {})[key] = _json_safe(company_object[key])
    loc = _location_parts(location)
    if not loc.get("country"):
        loc["country"] = _first(job.get("job_country_code"), job.get("country_code"), job.get("job_country"))
    return {"ats_job_id": _ats_id(_first(job.get("id"), job.get("job_id"))), "ats_type": "theirstack", "company_name": company_name, "title": _first(job.get("job_title"), job.get("title")), "description": description, **loc, "job_url": _valid_http_url(_first(job.get("final_url"), job.get("url"), job.get("job_url"))), "salary_range": salary, **meta}
