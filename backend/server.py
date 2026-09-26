from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Form, Header
from fastapi.responses import FileResponse, RedirectResponse, Response
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request as StarletteRequest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from jose import JWTError, jwt
import os
import json
import logging
import hashlib
import hmac
import base64
import shutil
import re
import secrets
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Any, Dict
import uuid
from datetime import datetime, timezone, timedelta
import httpx
from zoneinfo import ZoneInfo
from openai import AsyncOpenAI, RateLimitError as OpenAIRateLimitError
from groq_client import GroqClientPool, AllKeysRateLimitedError
import pypdf
import io
import asyncio
import html
import textwrap
import mimetypes
from app.job_ingestion.lifecycle import candidate_visible_where

try:  # pragma: no cover - optional dependency
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    REPORTLAB_AVAILABLE = True
except Exception:  # pragma: no cover - fallback path is exercised when reportlab is absent
    colors = None
    letter = (612.0, 792.0)
    ParagraphStyle = getSampleStyleSheet = inch = KeepTogether = ListFlowable = ListItem = Paragraph = SimpleDocTemplate = Spacer = Table = TableStyle = None
    REPORTLAB_AVAILABLE = False

try:
    import resend
except ImportError:  # pragma: no cover - exercised in environments without the SDK installed
    resend = None

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

openai_client = GroqClientPool(base_url="https://api.groq.com/openai/v1")

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# Deployments should set EVE_DOCS_DIR to a mounted/persistent document volume.
# Keep the development default outside the OS temporary directory as well: the
# database stores references to files in this directory for later viewing.
DOCS_DIR = Path(os.environ.get("EVE_DOCS_DIR", str(ROOT_DIR / "data" / "documents")))
DOCS_DIR.mkdir(parents=True, exist_ok=True)
MAX_PROFILE_PHOTO_BYTES = 5 * 1024 * 1024
ALLOWED_PROFILE_PHOTO_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
CANDIDATE_SESSION_SECRET = os.environ.get("CANDIDATE_SESSION_SECRET") or os.environ.get("EVE_INTERNAL_TOKEN") or "dev-candidate-session-secret"
CANDIDATE_SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
SUPPORT_EMAIL_TO = "info@pontis.one"

app = FastAPI()
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------- Pydantic models ----------

class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessageIn]
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    candidate_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    session_id: str
    profile_updates: Optional[Dict[str, Any]] = None


class CandidateAuthPayload(BaseModel):
    candidate_id: str


class CandidateHelpRequest(BaseModel):
    candidate_id: str
    subject: str
    message: str


class JobMatchImprovementRequest(BaseModel):
    """Edits to the uploaded resume's canonical representation for one recommendation."""
    profile_updates: Dict[str, Any] = Field(default_factory=dict)
    fix_credit_claim_id: Optional[str] = None


class RazorpayVerificationRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# ---------- Helpers ----------

def _extract_pdf_text(file_bytes: bytes) -> tuple[str, bool]:
    """Extract text from PDF; fall back to OCR if text layer is thin."""
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    text = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
    if len(text) >= 100:
        return text, False
    # OCR fallback
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(file_bytes, dpi=200)
        text = "\n".join(pytesseract.image_to_string(img) for img in images).strip()
        return text, True
    except Exception as e:
        logger.warning("OCR fallback failed: %s", e)
        return text, False


_EXPERIENCE_OPEN_ENDED_MARKERS = {"present", "current", "ongoing", "now"}


def _normalize_experience_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _is_open_ended_experience_value(value: Any) -> bool:
    normalized = _normalize_experience_text(value).lower()
    if not normalized:
        return False
    if normalized in _EXPERIENCE_OPEN_ENDED_MARKERS:
        return True
    return bool(re.search(r"\b(?:present|current|ongoing|now)\b", normalized, re.I))


def _parse_experience_date(value: Any, role: str = "end") -> Optional[int]:
    text = _normalize_experience_text(value)
    if not text or _is_open_ended_experience_value(text):
        return None

    if m := re.match(r"^(\d{4})$", text):
        year = int(m.group(1))
        month = 12 if role == "end" else 1
        day = 31 if role == "end" else 1
        return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%m-%d-%Y", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return int(parsed.timestamp())
        except ValueError:
            continue

    # Handle "Month YYYY" / "Mon YYYY" (e.g. "January 2025", "Jan 2025")
    for fmt in ("%B %Y", "%b %Y"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            if role == "end":
                import calendar as _cal
                last_day = _cal.monthrange(parsed.year, parsed.month)[1]
                parsed = parsed.replace(day=last_day)
            return int(parsed.timestamp())
        except ValueError:
            continue

    if m := re.match(r"^(\d{4})[./](\d{1,2})[./](\d{1,2})$", text):
        year, month, day = map(int, m.groups())
        return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())

    return None


def _experience_sort_values(item: Any) -> tuple[bool, Optional[int], Optional[int]]:
    if not isinstance(item, dict):
        return False, None, None

    raw_start = item.get("start_date") or item.get("startDate")
    raw_end = item.get("end_date") or item.get("endDate")
    has_explicit_end_field = "end_date" in item or "endDate" in item
    start = _parse_experience_date(raw_start, "start")
    end = _parse_experience_date(raw_end, "end")

    dates_text = _normalize_experience_text(item.get("dates") or item.get("duration") or "")
    open_ended = _is_open_ended_experience_value(raw_end)
    if not open_ended and has_explicit_end_field and not _normalize_experience_text(raw_end):
        open_ended = True

    if dates_text:
        separator = re.search(r"\s+[\u2013\u2014-]\s+", dates_text)
        if separator:
            left, right = [part.strip() for part in re.split(r"\s+[\u2013\u2014-]\s+", dates_text, maxsplit=1)]
            if start is None:
                start = _parse_experience_date(left, "start")
            if right:
                if _is_open_ended_experience_value(right):
                    open_ended = True
                    end = None
                elif end is None:
                    end = _parse_experience_date(right, "end")
        else:
            if start is None:
                start = _parse_experience_date(dates_text, "start")
            if end is None and not open_ended:
                end = _parse_experience_date(dates_text, "end")
            if _is_open_ended_experience_value(dates_text):
                open_ended = True

    return open_ended, start, end


def _sort_experience_for_display(experience: Any) -> list[dict]:
    if not isinstance(experience, list):
        return []

    indexed: list[tuple[bool, Optional[int], Optional[int], int, dict]] = []
    for index, item in enumerate(experience):
        if not isinstance(item, dict):
            continue
        open_ended, start, end = _experience_sort_values(item)
        indexed.append((open_ended, start, end, index, item))

    def sort_key(entry: tuple[bool, Optional[int], Optional[int], int, dict]) -> tuple[int, int, int, int]:
        open_ended, start, end, index, _item = entry
        primary = start if open_ended else (end if end is not None else start)
        secondary = start if start is not None else end
        return (
            0 if open_ended else 1,
            -(primary or 0),
            -(secondary or 0),
            index,
        )

    indexed.sort(key=sort_key)
    return [item for *_rest, item in indexed]


def _format_month_year_from_ts(value: Optional[int]) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%B %Y")


def _employment_gap_key(previous: dict, current: dict, previous_end: Optional[int], current_start: Optional[int]) -> str:
    parts = [
        _normalize_profile_key(previous.get("company") or previous.get("company_name") or ""),
        _normalize_profile_key(previous.get("title") or previous.get("role") or ""),
        str(previous_end or ""),
        _normalize_profile_key(current.get("company") or current.get("company_name") or ""),
        _normalize_profile_key(current.get("title") or current.get("role") or ""),
        str(current_start or ""),
    ]
    return "|".join(parts)


def _describe_experience_item(exp: dict) -> str:
    title = _normalize_profile_text(exp.get("title") or exp.get("role") or "")
    company = _normalize_profile_text(exp.get("company") or exp.get("company_name") or "")
    if title and company:
        return f"{title} at {company}"
    return title or company or "your previous role"


def _detect_employment_gap(profile: dict) -> Optional[dict]:
    experience = profile.get("experience") or profile.get("work_experience") or []
    if not isinstance(experience, list) or len(experience) < 2:
        return None

    unique: list[dict] = []
    seen: set[str] = set()
    for item in _sort_experience_for_display(experience):
        if not isinstance(item, dict):
            continue
        key = _normalize_profile_key(
                f"{item.get('title') or item.get('role') or ''}|"
                f"{item.get('company') or item.get('company_name') or ''}"
            )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    periods: list[dict] = []
    for index, item in enumerate(unique):
        open_ended, start, end = _experience_sort_values(item)
        if start is None:
            continue
        periods.append({
            "item": item,
            "index": index,
            "open_ended": open_ended,
            "start": start,
            "end": end,
        })

    if len(periods) < 2:
        return None

    periods.sort(key=lambda p: (p["start"] or 0, p["end"] or p["start"] or 0))

    raw_data = profile.get("raw_data") or {}
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except Exception:
            raw_data = {}
    voice_intake = _parse_raw_data(raw_data.get("voice_intake"))
    explained = {
        _normalize_profile_key(entry.get("gap_key"))
        for entry in (voice_intake.get("employment_gaps") or [])
        if isinstance(entry, dict) and _clean_str(entry.get("answer"))
    }

    candidates: list[dict] = []
    for prev, curr in zip(periods, periods[1:]):
        prev_end = prev["end"]
        curr_start = curr["start"]
        if prev["open_ended"] or prev_end is None or curr_start is None:
            continue
        if curr_start <= prev_end:
            continue

        gap_days = max(0, int((curr_start - prev_end) / 86400) - 1)
        if gap_days < 30:
            continue

        previous_item = prev["item"]
        current_item = curr["item"]
        gap_key = _employment_gap_key(previous_item, current_item, prev_end, curr_start)
        if _normalize_profile_key(gap_key) in explained:
            continue

        candidates.append({
            "gap_key": gap_key,
            "gap_days": gap_days,
            "previous": previous_item,
            "current": current_item,
            "previous_end": prev_end,
            "current_start": curr_start,
        })

    if not candidates:
        return None

    gap = candidates[-1]
    previous_item = gap["previous"]
    current_item = gap["current"]
    previous_end_label = _format_month_year_from_ts(gap["previous_end"])
    current_start_label = _format_month_year_from_ts(gap["current_start"])
    return {
        "gap_key": gap["gap_key"],
        "gap_days": gap["gap_days"],
        "previous_label": _describe_experience_item(previous_item),
        "current_label": _describe_experience_item(current_item),
        "previous_end_label": previous_end_label,
        "current_start_label": current_start_label,
        "question": (
            f"I noticed a gap between { _describe_experience_item(previous_item) }"
            f" ending in {previous_end_label} and { _describe_experience_item(current_item) }"
            f" starting in {current_start_label}. What should I note for that period?"
        ),
    }


def _employment_gap_answered(profile: dict) -> Optional[dict]:
    gap = _detect_employment_gap(profile)
    if gap:
        return None
    raw_data = profile.get("raw_data") or {}
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except Exception:
            raw_data = {}
    voice_intake = _parse_raw_data(raw_data.get("voice_intake"))
    records = voice_intake.get("employment_gaps") or []
    for entry in reversed(records):
        if not isinstance(entry, dict):
            continue
        answer = _clean_str(entry.get("answer"))
        if not answer:
            continue
        return {
            "gap_key": _clean_str(entry.get("gap_key")),
            "question": _clean_str(entry.get("question")),
            "answer": answer,
        }
    return None


PARSE_SYSTEM = """You are a resume parser. Extract structured data from the resume text and return ONLY valid JSON with these exact keys:
{
  "name": "",
  "email": "",
  "phone": "",
  "headline": "",
  "current_role": "",
  "current_company": "",
  "location": "",
  "bio": "",
  "experience_years": 0,
  "skills": ["skill1"],
  "work_experience": [{"title":"","company":"","start_date":"","end_date":"","description":""}],
  "projects": [{"title":"","description":"","technologies":[]}],
  "education": [{"degree":"","institution":"","start_date":"","end_date":""}],
  "certifications": ["cert1"]
}
For education, extract the actual stated start and completion dates. Do not use "Present" for a completed degree; use it only when the resume explicitly says the course is current or ongoing. If only a completion year is stated, place it in end_date and leave start_date empty.
"current_role" must be an actual job title (for example, "Python Developer"), never a skill, technology, database, company, or date range. "location" must be the candidate's actual geographic location or "Remote", never an education/employment date or timeline.
For projects, extract only explicitly described named projects or products. Keep the candidate's project title, their stated work, and explicitly named technologies. Do not turn a general job responsibility into a project and do not infer a title.
Return only the JSON object, no markdown, no explanation."""


async def _parse_resume_with_llm(resume_text: str) -> dict:
    try:
        resp = await openai_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": PARSE_SYSTEM},
                {"role": "user", "content": resume_text[:12000]},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        return _sanitize_profile_field_mapping(json.loads(raw))
    except (OpenAIRateLimitError, AllKeysRateLimitedError) as exc:
        logger.warning("[parse-resume] Groq rate limit hit: %s", exc)
        raise HTTPException(
            status_code=429,
            detail="Resume parsing is temporarily unavailable due to high demand. Please try again in a moment.",
        ) from exc


def _build_career_gap_vapi_vars(profile: dict) -> dict:
    """
    Derive career-gap VAPI variables from the candidate profile.
    Returns the four variables expected by the VAPI assistant:
      career_gap_detected, career_gap_start, career_gap_end, career_gap_context
    """
    gap = _detect_employment_gap(profile)
    if not gap:
        return {
            "career_gap_detected": False,
            "career_gap_start": "",
            "career_gap_end": "",
            "career_gap_context": "",
        }
    return {
        "career_gap_detected": True,
        "career_gap_start": gap["previous_end_label"],
        "career_gap_end": gap["current_start_label"],
        "career_gap_context": (
            f"Gap between {gap['previous_label']} "
            f"(ended {gap['previous_end_label']}) and "
            f"{gap['current_label']} "
            f"(started {gap['current_start_label']})"
        ),
    }


def _truncate_date_to_month(value) -> str:
    """Return YYYY-MM from a YYYY-MM-DD string; pass other values through."""
    text = str(value).strip() if value is not None else ""
    m = re.match(r"^(\d{4}-\d{2})-\d{2}$", text)
    return m.group(1) if m else text


def _normalize_for_frontend(c: dict) -> dict:
    """Map DB candidate row → Eve frontend profile shape."""
    work_exp = _sort_experience_for_display(c.get("work_experience") or [])
    experience = []
    for i, w in enumerate(work_exp):
        _sd = _truncate_date_to_month(w.get("start_date") or w.get("startDate") or "")
        _ed_raw = w.get("end_date") or w.get("endDate") or ""
        _ed = _truncate_date_to_month(_ed_raw) if _ed_raw else ""
        dates = " — ".join(filter(None, [_sd, _ed or "Present"]))
        experience.append({
            "id": w.get("id", f"exp-{i}"),
            "title": w.get("title", ""),
            "company": w.get("company", ""),
            "start_date": _sd,
            "end_date": _ed,
            "dates": dates,
            "description": w.get("description", ""),
        })

    for i, w in enumerate(work_exp):
        if i >= len(experience):
            break
        experience[i]["start_date"] = _truncate_date_to_month(w.get("start_date") or w.get("startDate") or "")
        experience[i]["end_date"] = _truncate_date_to_month(w.get("end_date") or w.get("endDate") or "")
        if not experience[i].get("dates"):
            start_date = experience[i]["start_date"]
            end_date = experience[i]["end_date"] or ("Present" if start_date else "")
            experience[i]["dates"] = " â€” ".join(filter(None, [start_date, end_date]))

    edu_raw = c.get("education") or []
    education = []
    for i, e in enumerate(edu_raw):
        _esd = _truncate_date_to_month(e.get("start_date") or "")
        _eed = _truncate_date_to_month(e.get("end_date") or "")
        dates = " — ".join(filter(None, [_esd, _eed]))
        # Preserve a parser-supplied education date when separate boundaries
        # are unavailable. Unlike employment, a missing end date is never
        # interpreted as an ongoing course.
        if not dates:
            dates = _normalize_experience_text(e.get("dates") or e.get("duration") or "")
        education.append({
            "id": e.get("id", f"edu-{i}"),
            "degree": e.get("degree", ""),
            "institution": e.get("institution", ""),
            "dates": dates,
        })

    # Pull voice-derived extras from raw_data
    raw_data = c.get("raw_data") or {}
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except Exception:
            raw_data = {}

    voice_intake_resume = raw_data.get("voice_intake")
    if not isinstance(voice_intake_resume, dict):
        voice_intake_resume = _build_voice_intake_resume({**c, "raw_data": raw_data})

    from profile_strength_service import calculate_profile_strength_v2
    _ps_result = calculate_profile_strength_v2(c, raw_data, c.get("_prefs_row"))
    _ps_result = _apply_profile_strength_test_override(c, _ps_result)
    strength_percent = _ps_result["percent"]
    strength_label = _ps_result["label"]
    certifications = _normalize_certifications(raw_data.get("certifications") or [])
    key_skills = _normalize_skills(c.get("skills") or [], certifications=certifications)

    profile = {
        "candidate_id": str(c.get("id") or c.get("candidate_id") or ""),
        "name": c.get("name", ""),
        "email": c.get("email", ""),
        "phone": c.get("phone", ""),
        "location": c.get("location", ""),
        "headline": c.get("current_role", "") or c.get("headline", ""),
        "current_company": c.get("current_company", ""),
        "bio": c.get("summary", ""),
        "experience_years": c.get("experience_years") or c.get("total_experience_years"),
        "keySkills": key_skills,
        "experience": experience,
        "education": education,
        "availability": _normalize_availability_value(raw_data.get("availability", "")) or raw_data.get("availability", ""),
        "salary_expectation": raw_data.get("salary_expectation", ""),
        "preferred_roles": raw_data.get("preferred_roles") or [],
        "certifications": certifications,
        "projects": _normalize_projects(raw_data.get("projects")),
        "additional_information": raw_data.get("additional_information", ""),
        "parsing_status": c.get("parsing_status", ""),
        "photo_url": _candidate_photo_url(c, raw_data),
        "profile_strength_percent": strength_percent,
        "profile_strength_label": strength_label,
        "strengthPercent": strength_percent,
        "strength": strength_label,
        "profile_strength_detail": _ps_result,
        "recommendation_readiness": _ps_result.get("recommendation_readiness"),
    }
    voice_intake_summary = raw_data.get("voice_intake_summary")
    if isinstance(voice_intake_summary, dict):
        profile["voice_intake_summary_source"] = voice_intake_summary
    if voice_intake_resume:
        profile["voice_intake_resume"] = voice_intake_resume

    # Career-gap VAPI variables - derived dynamically from parsed experience
    gap_vars = _build_career_gap_vapi_vars({**c, "experience": experience, "raw_data": raw_data})
    profile.update(gap_vars)

    return profile


def _pdf_safe_text(value: Any) -> str:
    dash_translation = str.maketrans({
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2212": "-",  # minus sign
        "\uFE58": "-",  # small em dash
        "\uFE63": "-",  # small hyphen-minus
        "\uFF0D": "-",  # fullwidth hyphen-minus
    })
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip().translate(dash_translation)
    if isinstance(value, list):
        return ", ".join(_pdf_safe_text(item) for item in value if _pdf_safe_text(item))
    if isinstance(value, dict):
        parts = []
        for key in ("title", "degree", "company", "institution", "name", "value", "description"):
            cleaned = _pdf_safe_text(value.get(key))
            if cleaned:
                parts.append(cleaned)
        return " - ".join(parts) if parts else json.dumps(value, ensure_ascii=True, default=str)
    return str(value).strip()


def _pdf_filename(candidate_name: str, candidate_id: str) -> str:
    base = _pdf_safe_text(candidate_name) or f"candidate_{candidate_id}"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
    if not base:
        base = f"candidate_{candidate_id}"
    if base.lower().endswith(".pdf"):
        return base
    if not base.lower().endswith("_profile"):
        base += "_profile"
    return f"{base}.pdf"


def _candidate_profile_social_links(profile: dict) -> list[tuple[str, str]]:
    raw_data = _parse_raw_data(profile.get("raw_data"))
    sources = [profile]
    if raw_data:
        sources.append(raw_data)

    label_map = [
        ("linkedin_url", "LinkedIn"),
        ("linkedin", "LinkedIn"),
        ("github_url", "GitHub"),
        ("github", "GitHub"),
        ("portfolio_url", "Portfolio"),
        ("portfolio", "Portfolio"),
        ("website_url", "Website"),
        ("website", "Website"),
        ("personal_website", "Website"),
        ("twitter_url", "X"),
        ("x_url", "X"),
        ("instagram_url", "Instagram"),
        ("facebook_url", "Facebook"),
    ]

    links: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add_link(label: str, url: Any) -> None:
        cleaned = _pdf_safe_text(url)
        if not cleaned:
            return
        normalized = cleaned.lower()
        if normalized in seen:
            return
        seen.add(normalized)
        links.append((label, cleaned))

    for source in sources:
        for field, label in label_map:
            value = source.get(field)
            if isinstance(value, str):
                add_link(label, value)

        social_links = source.get("social_links") or source.get("socialLinks") or source.get("links")
        if isinstance(social_links, list):
            for item in social_links:
                if isinstance(item, str):
                    add_link("Link", item)
                elif isinstance(item, dict):
                    add_link(
                        _pdf_safe_text(item.get("label") or item.get("name") or item.get("type")) or "Link",
                        item.get("url") or item.get("href") or item.get("link") or item.get("value"),
                    )

    return links


# Noisy/conversational skill entries that must never appear in the resume PDF.
_NOISY_SKILL_PATTERNS = re.compile(
    r"^(?:some\s+more\s+skills?|more\s+skills?|additional\s+skills?|other\s+skills?|skills?\s+include|i\s+(?:also\s+)?(?:know|have|use)|voice\s+intake|resume\s+processing)\b",
    re.IGNORECASE,
)


def _candidate_profile_pdf_skills(profile: dict) -> list[str]:
    """Return deduplicated, cleaned skills for the ATS resume PDF."""
    raw_skills = profile.get("keySkills") or profile.get("skills") or []
    seen: set[str] = set()
    result: list[str] = []
    for skill in raw_skills:
        text = _pdf_safe_text(skill).strip()
        if not text:
            continue
        if _NOISY_SKILL_PATTERNS.match(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _ats_clean_description(description: str) -> list[str]:
    """Split a description into deduplicated bullet sentences for ATS resume output."""
    if not description:
        return []
    # Split on sentence boundaries
    raw_sentences = re.split(r"(?<=[.!?])\s+", description.strip())
    seen: set[str] = set()
    bullets: list[str] = []
    for sentence in raw_sentences:
        cleaned = sentence.strip().strip(".")
        if not cleaned:
            continue
        key = re.sub(r"\s+", " ", cleaned.lower())
        if key in seen:
            continue
        seen.add(key)
        bullets.append(cleaned)
    return bullets


def _format_pdf_date(value: Any) -> str:
    """Format a date value for display in the resume PDF.

    Converts raw ISO dates (2023-11-01) or timestamps to 'Nov 2023' format.
    Passes through already-human-readable strings (e.g. 'Present', '2022 - Present').
    """
    text = _pdf_safe_text(value).strip()
    if not text:
        return ""
    # Already human-readable (contains letters or slash-separated years)
    if re.search(r"[A-Za-z]", text):
        return text
    # ISO date: YYYY-MM-DD or YYYY/MM/DD
    m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.strftime("%b %Y")
        except ValueError:
            pass
    # YYYY-MM only
    m2 = re.match(r"^(\d{4})[/-](\d{1,2})$", text)
    if m2:
        try:
            dt = datetime(int(m2.group(1)), int(m2.group(2)), 1, tzinfo=timezone.utc)
            return dt.strftime("%b %Y")
        except ValueError:
            pass
    return text


def _format_pdf_date_range(item: dict) -> str:
    """Build a human-readable date range string for an experience/education item."""
    # Prefer the pre-built 'dates' field if it looks human-readable
    dates = _pdf_safe_text(item.get("dates") or item.get("duration") or "")
    if dates and re.search(r"[A-Za-z]", dates):
        return dates
    # Build from start_date / end_date
    start = _format_pdf_date(item.get("start_date") or item.get("startDate") or "")
    end_raw = item.get("end_date") or item.get("endDate") or ""
    end = "Present" if _is_open_ended_experience_value(end_raw) else _format_pdf_date(end_raw)
    if start and end:
        return f"{start} - {end}"
    if start:
        return start
    # Fall back to raw dates field
    return dates


def _candidate_profile_header(profile: dict) -> list[Paragraph]:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CandidateProfileTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#1F1F1F"),
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        "CandidateProfileSubtitle",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
        textColor=colors.HexColor("#4A4A48"),
        spaceAfter=6,
    )

    def p(text: Any, style=subtitle_style) -> Paragraph:
        clean = _pdf_safe_text(text)
        if not clean:
            clean = " "
        return Paragraph(html.escape(clean).replace("\n", "<br/>"), style)

    story: list[Paragraph] = [p(profile.get("name") or "Candidate Profile", title_style)]

    contact_bits = [profile.get("location"), profile.get("email"), profile.get("phone")]
    contact_line = " | ".join(_pdf_safe_text(bit) for bit in contact_bits if _pdf_safe_text(bit))
    if contact_line:
        story.append(p(contact_line))

    social_links = _candidate_profile_social_links(profile)
    if social_links:
        story.append(
            p(
                " | ".join(f"{label}: {url}" for label, url in social_links),
                ParagraphStyle(
                    "CandidateProfileSocial",
                    parent=subtitle_style,
                    textColor=colors.HexColor("#5A5A57"),
                    spaceAfter=10,
                ),
            )
        )
    elif story:
        story[-1].style.spaceAfter = 10

    return story


def _pdf_story_from_profile(profile: dict) -> list:
    """Build an ATS-friendly single-column resume story for ReportLab."""
    styles = getSampleStyleSheet()
    # Section heading: bold, uppercase-style, with a thin rule via spaceBefore
    section_style = ParagraphStyle(
        "ATSSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#000000"),
        spaceBefore=14,
        spaceAfter=4,
        borderPadding=(0, 0, 2, 0),
    )
    body_style = ParagraphStyle(
        "ATSBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#1A1A1A"),
    )
    job_title_style = ParagraphStyle(
        "ATSJobTitle",
        parent=body_style,
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        spaceAfter=1,
    )
    meta_style = ParagraphStyle(
        "ATSMeta",
        parent=body_style,
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#444444"),
        spaceAfter=3,
    )
    bullet_style = ParagraphStyle(
        "ATSBullet",
        parent=body_style,
        leftIndent=14,
        firstLineIndent=0,
        spaceAfter=2,
    )

    def p(text: Any, style=body_style) -> Paragraph:
        clean = _pdf_safe_text(text)
        if not clean:
            clean = " "
        return Paragraph(html.escape(clean).replace("\n", "<br/>"), style)

    def bullet_list(items: list[str]) -> list:
        cleaned = [_pdf_safe_text(i) for i in items if _pdf_safe_text(i)]
        if not cleaned:
            return []
        return [
            ListFlowable(
                [ListItem(p(i, bullet_style), leftIndent=6) for i in cleaned],
                bulletType="bullet",
                leftIndent=14,
                bulletFontName="Helvetica",
                bulletFontSize=8,
                bulletOffsetY=2,
            )
        ]

    story: list = []
    story.extend(_candidate_profile_header(profile))

    def add_section(title: str, body: list) -> None:
        if not body:
            return
        story.append(p(title.upper(), section_style))
        story.extend(body)
        story.append(Spacer(1, 0.08 * inch))

    # --- Summary ---
    summary = _pdf_safe_text(profile.get("bio") or profile.get("summary"))
    if summary:
        add_section("Professional Summary", [p(summary)])

    # --- Technical Skills (comma-separated, deduplicated) ---
    skills = _candidate_profile_pdf_skills(profile)
    if skills:
        skills_line = ", ".join(skills)
        add_section("Technical Skills", [p(skills_line)])

    # --- Professional Experience ---
    experience = _sort_experience_for_display(profile.get("experience") or profile.get("work_experience") or [])
    if experience:
        exp_blocks: list = []
        for item in experience:
            if not isinstance(item, dict):
                continue
            job_title = _pdf_safe_text(item.get("title"))
            company = _pdf_safe_text(item.get("company"))
            location = _pdf_safe_text(item.get("location"))
            dates = _format_pdf_date_range(item)
            if not job_title and not company:
                continue
            # Meta line: Company | Location | Dates
            meta_parts = [part for part in [company, location, dates] if part]
            block: list = [p(job_title, job_title_style)]
            if meta_parts:
                block.append(p(" | ".join(meta_parts), meta_style))
            description = _pdf_safe_text(item.get("description") or item.get("summary"))
            bullets = _ats_clean_description(description)
            if bullets:
                block.extend(bullet_list(bullets))
            exp_blocks.append(KeepTogether(block))
            exp_blocks.append(Spacer(1, 0.06 * inch))
        add_section("Professional Experience", exp_blocks)

    # --- Education ---
    education = profile.get("education") or []
    if education:
        edu_blocks: list = []
        for item in education:
            if not isinstance(item, dict):
                continue
            degree = _pdf_safe_text(item.get("degree"))
            institution = _pdf_safe_text(item.get("institution"))
            dates = _format_pdf_date_range(item)
            if not degree and not institution:
                continue
            block = [p(degree or institution, job_title_style)]
            meta_parts = [part for part in [institution if degree else "", dates] if part]
            if meta_parts:
                block.append(p(" | ".join(meta_parts), meta_style))
            edu_blocks.append(KeepTogether(block))
            edu_blocks.append(Spacer(1, 0.04 * inch))
        add_section("Education", edu_blocks)

    # --- Certifications ---
    certifications = profile.get("certifications") or []
    if certifications:
        add_section("Certifications", bullet_list([_pdf_safe_text(c) for c in certifications if _pdf_safe_text(c)]))

    return story


def _build_candidate_profile_pdf(profile: dict) -> bytes:
    if not REPORTLAB_AVAILABLE:
        return _build_candidate_profile_pdf_fallback(profile)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.72 * inch,
        rightMargin=0.72 * inch,
        topMargin=0.72 * inch,
        bottomMargin=0.72 * inch,
        title=f"{_pdf_safe_text(profile.get('name')) or 'Candidate'} Profile",
        author="Eve",
    )

    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#9A9A98"))
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 0.42 * inch, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(_pdf_story_from_profile(profile), onFirstPage=add_page_number, onLaterPages=add_page_number)
    buffer.seek(0)
    return buffer.getvalue()


def _candidate_profile_pdf_blocks(profile: dict) -> list[dict]:
    """Build ATS-friendly text blocks for the fallback (non-ReportLab) PDF renderer."""
    blocks: list[dict] = []

    def add(text: Any, *, size: int = 10, bold: bool = False, indent: int = 0, after: float = 0.0) -> None:
        clean = _pdf_safe_text(text)
        if clean:
            blocks.append({"text": clean, "size": size, "bold": bold, "indent": indent, "after": after})

    add(profile.get("name") or "Resume", size=20, bold=True, after=4)

    contact_bits = [profile.get("location"), profile.get("email"), profile.get("phone")]
    contact_line = " | ".join(_pdf_safe_text(bit) for bit in contact_bits if _pdf_safe_text(bit))
    if contact_line:
        add(contact_line, size=10, after=3)

    social_links = _candidate_profile_social_links(profile)
    if social_links:
        add(" | ".join(f"{label}: {url}" for label, url in social_links), size=9, after=5)

    summary = _pdf_safe_text(profile.get("bio") or profile.get("summary"))
    if summary:
        add("PROFESSIONAL SUMMARY", size=11, bold=True, after=2)
        add(summary, size=10, after=4)

    skills = _candidate_profile_pdf_skills(profile)
    if skills:
        add("TECHNICAL SKILLS", size=11, bold=True, after=2)
        add(", ".join(skills), size=10, after=4)

    experience = _sort_experience_for_display(profile.get("experience") or profile.get("work_experience") or [])
    if experience:
        add("PROFESSIONAL EXPERIENCE", size=11, bold=True, after=2)
        for item in experience:
            if not isinstance(item, dict):
                continue
            job_title = _pdf_safe_text(item.get("title"))
            company = _pdf_safe_text(item.get("company"))
            location = _pdf_safe_text(item.get("location"))
            dates = _format_pdf_date_range(item)
            if not job_title and not company:
                continue
            add(job_title or company, size=10, bold=True, after=1)
            meta_parts = [part for part in [company, location, dates] if part]
            if meta_parts:
                add(" | ".join(meta_parts), size=9, after=1)
            description = _pdf_safe_text(item.get("description") or item.get("summary"))
            for bullet in _ats_clean_description(description):
                add(f"- {bullet}", size=10, indent=12, after=1)
            blocks.append({"blank": True, "after": 3})
        blocks.append({"blank": True, "after": 2})

    education = profile.get("education") or []
    if education:
        add("EDUCATION", size=11, bold=True, after=2)
        for item in education:
            if not isinstance(item, dict):
                continue
            degree = _pdf_safe_text(item.get("degree"))
            institution = _pdf_safe_text(item.get("institution"))
            dates = _format_pdf_date_range(item)
            if not degree and not institution:
                continue
            add(degree or institution, size=10, bold=True, after=1)
            meta_parts = [part for part in [institution if degree else "", dates] if part]
            if meta_parts:
                add(" | ".join(meta_parts), size=9, after=2)
        blocks.append({"blank": True, "after": 2})

    certifications = profile.get("certifications") or []
    if certifications:
        add("CERTIFICATIONS", size=11, bold=True, after=2)
        for cert in certifications:
            add(f"- {_pdf_safe_text(cert)}", size=10, indent=12, after=1)
        blocks.append({"blank": True, "after": 4})

    return blocks


def _build_candidate_profile_pdf_fallback(profile: dict) -> bytes:
    page_width, page_height = letter
    left_margin = 54
    right_margin = 54
    top_margin = 54
    bottom_margin = 54
    usable_width = page_width - left_margin - right_margin

    def escape_pdf_text(value: str) -> str:
        cleaned = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return cleaned.encode("latin-1", "replace").decode("latin-1")

    def font_name(is_bold: bool) -> str:
        return "Helvetica-Bold" if is_bold else "Helvetica"

    def wrap_text(text: str, size: int, indent: int) -> list[str]:
        available = max(120.0, usable_width - indent)
        max_chars = max(18, int(available / max(size * 0.52, 1)))
        paragraphs = text.splitlines() or [text]
        wrapped: list[str] = []
        for paragraph in paragraphs:
            if not paragraph.strip():
                wrapped.append("")
                continue
            wrapped.extend(
                textwrap.wrap(
                    paragraph,
                    width=max_chars,
                    break_long_words=False,
                    break_on_hyphens=False,
                ) or [paragraph]
            )
        return wrapped

    blocks = _candidate_profile_pdf_blocks(profile)
    pages: list[list[str]] = []
    commands: list[str] = []
    current_y = page_height - top_margin

    def flush_page() -> None:
        nonlocal commands, current_y
        if commands:
            pages.append(commands)
        commands = []
        current_y = page_height - top_margin

    for block in blocks:
        if block.get("blank"):
            current_y -= float(block.get("after") or 12)
            continue

        text = _pdf_safe_text(block.get("text"))
        size = int(block.get("size") or 10)
        indent = int(block.get("indent") or 0)
        bold = bool(block.get("bold"))
        after = float(block.get("after") or 0)
        line_height = max(size * 1.35, 12)
        for line in wrap_text(text, size, indent):
            if current_y < bottom_margin + line_height:
                flush_page()
            commands.append(
                f"BT /{font_name(bold)} {size} Tf {left_margin + indent} {current_y:.2f} Td ({escape_pdf_text(line)}) Tj ET"
            )
            current_y -= line_height
        current_y -= after

    if commands or not pages:
        pages.append(commands)

    objects: list[tuple[int, str]] = []
    total_pages = len(pages)
    page_ids = [5 + index for index in range(total_pages)]
    content_ids = [5 + total_pages + index for index in range(total_pages)]

    objects.append((1, "<< /Type /Catalog /Pages 2 0 R >>"))
    objects.append((2, f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] /Count {total_pages} >>"))
    objects.append((3, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    objects.append((4, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"))

    for page_id, content_id, page_commands in zip(page_ids, content_ids, pages):
        content = "\n".join(page_commands)
        content_bytes = content.encode("latin-1", "replace")
        stream = f"<< /Length {len(content_bytes)} >>\nstream\n{content}\nendstream"
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_id} 0 R >>"
        )
        objects.append((page_id, page_obj))
        objects.append((content_id, stream))

    objects.sort(key=lambda item: item[0])

    header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    parts = [header]
    offsets = {0: 0}
    position = len(header)
    for obj_id, body in objects:
        chunk = f"{obj_id} 0 obj\n{body}\nendobj\n".encode("latin-1", "replace")
        offsets[obj_id] = position
        parts.append(chunk)
        position += len(chunk)

    max_id = max(offsets)
    xref_offset = position
    xref_lines = [f"xref\n0 {max_id + 1}\n", "0000000000 65535 f \n"]
    for obj_id in range(1, max_id + 1):
        xref_lines.append(f"{offsets.get(obj_id, 0):010d} 00000 n \n")
    trailer = (
        f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )
    parts.append("".join(xref_lines).encode("latin-1"))
    parts.append(trailer.encode("latin-1"))
    return b"".join(parts)


async def _get_candidate_profile_payload(candidate_id: str) -> dict:
    row = await _get_candidate_row(candidate_id)
    enriched = dict(row)
    enriched["candidate_certificates"] = await _load_candidate_certificates(candidate_id)
    # Load canonical preferences for strength scoring (Phase 2)
    async with SessionLocal() as db:
        _pr = await db.execute(
            text("SELECT * FROM candidate_preferences WHERE candidate_id = :cid LIMIT 1"),
            {"cid": candidate_id},
        )
        _prefs_row = _pr.mappings().fetchone()
    enriched["_prefs_row"] = dict(_prefs_row) if _prefs_row else None
    return _normalize_for_frontend(enriched)


async def _load_candidate_certificates(candidate_id: str) -> list[dict]:
    """Load certificate rows for a single candidate."""
    async with SessionLocal() as db:
        rows = await db.execute(
            text(
                "SELECT id, file_name, file_path "
                "FROM candidate_certificates "
                "WHERE candidate_id = :cid "
                "ORDER BY created_at ASC"
            ),
            {"cid": candidate_id},
        )
        result = rows.fetchall()
    return [
        {
            "id": str(row[0]),
            "file_name": row[1],
            "file_path": row[2],
        }
        for row in result
    ]


def _parse_raw_data(raw_data: Any) -> dict:
    if isinstance(raw_data, dict):
        return dict(raw_data)
    if isinstance(raw_data, str):
        try:
            parsed = json.loads(raw_data)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _candidate_storage_dir(candidate_id: str) -> Path:
    return (DOCS_DIR / candidate_id).resolve()


def _resolve_candidate_document_path(candidate_id: str, document_type: str, stored_reference: Any) -> Optional[Path]:
    """Resolve a document reference inside the configured persistent volume.

    Database rows created before a deployment may contain an absolute path from
    the previous container/host.  That path is metadata, not an authority: a
    candidate document may only be served from that candidate's directory in
    ``EVE_DOCS_DIR``.  Rebuilding the final component under the current volume
    keeps those rows portable across mount-path changes without ever consulting
    the upload machine's filesystem.
    """
    if document_type not in {"resume", "certificates", "application_resumes"} or not isinstance(stored_reference, str):
        return None
    reference = stored_reference.strip()
    if not reference:
        return None
    root = (_candidate_storage_dir(candidate_id) / document_type).resolve()
    try:
        stored_path = Path(reference).resolve()
        stored_path.relative_to(root)
        return stored_path
    except (OSError, ValueError):
        pass

    # Legacy absolute paths are portable by their generated storage key (the
    # basename), not by their old machine-specific parent path. Split both
    # separator styles: pathlib treats Windows paths as one filename on Linux.
    filename = re.split(r"[\\\\/]", reference.rstrip("\\\\/"))[-1]
    if not filename or filename in {".", ".."}:
        return None
    candidate = (root / filename).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _document_media_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _issue_candidate_session_token(candidate_id: str) -> str:
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "sub": candidate_id,
        "iat": now,
        "exp": now + CANDIDATE_SESSION_TTL_SECONDS,
        "scope": "candidate",
    }
    return jwt.encode(payload, CANDIDATE_SESSION_SECRET, algorithm="HS256")


def _verify_candidate_session_token(token: str, candidate_id: str) -> None:
    if not token:
        raise HTTPException(status_code=401, detail="Missing candidate session.")
    try:
        payload = jwt.decode(token, CANDIDATE_SESSION_SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid candidate session.")
    if payload.get("scope") != "candidate" or payload.get("sub") != candidate_id:
        raise HTTPException(status_code=403, detail="Forbidden.")


def _get_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return ""
    return authorization[7:].strip()


def _verify_document_view_session(candidate_id: str, authorization: Optional[str], candidate_token: Optional[str]) -> None:
    """Authorize a file GET or a targeted browser form POST.

    Browser navigations cannot carry an Authorization header. The form POST
    keeps the session token out of the URL and lets the browser render the
    original FileResponse, rather than a JavaScript Blob.
    """
    _verify_candidate_session_token(_get_bearer_token(authorization) or (candidate_token or ""), candidate_id)


def _build_support_email_text(message: str) -> str:
    return message.strip()


async def _send_support_email(
    subject: str,
    message: str,
    candidate_email: str = "",
) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    from_email = os.environ.get("RESEND_FROM_EMAIL", "").strip()
    if not api_key or not from_email:
        raise HTTPException(status_code=503, detail="Support email is not configured.")
    if resend is None or not hasattr(resend, "Emails"):
        raise HTTPException(status_code=503, detail="Support email is unavailable.")

    params: Dict[str, Any] = {
        "from": from_email,
        "to": [SUPPORT_EMAIL_TO],
        "subject": subject.strip(),
        "text": _build_support_email_text(message=message),
    }
    if candidate_email.strip():
        params["reply_to"] = candidate_email.strip()

    def _deliver() -> Any:
        resend.api_key = api_key
        return resend.Emails.send(params)

    try:
        await asyncio.to_thread(_deliver)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to send candidate help email via Resend: %s", exc)
        raise HTTPException(status_code=502, detail="Failed to send support email.") from exc


def _candidate_photo_dir(candidate_id: str) -> Path:
    return (DOCS_DIR / candidate_id / "photo").resolve()


def _photo_view_url(candidate_id: str, cache_key: Optional[str] = None) -> str:
    url = f"/api/candidate/{candidate_id}/photo/view"
    if cache_key:
        return f"{url}?rev={cache_key}"
    return url


def _resolve_candidate_photo_path(candidate_id: str, file_path: Any) -> Optional[Path]:
    if not isinstance(file_path, str) or not file_path.strip():
        return None
    try:
        resolved = Path(file_path).resolve()
    except Exception:
        return None
    photo_dir = _candidate_photo_dir(candidate_id)
    try:
        resolved.relative_to(photo_dir)
    except Exception:
        return None
    return resolved


async def _delete_candidate_photo(candidate_id: str, file_path: Any = None) -> None:
    resolved = _resolve_candidate_photo_path(candidate_id, file_path)
    if resolved and resolved.exists():
        try:
            resolved.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Could not delete candidate photo %s: %s", resolved, e)


def _validate_candidate_photo_upload(content_type: str, file_bytes: bytes) -> str:
    normalized = (content_type or "").lower().strip()
    if normalized not in ALLOWED_PROFILE_PHOTO_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image type. Please upload a JPG, JPEG, PNG, or WEBP image.",
        )
    if len(file_bytes) > MAX_PROFILE_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="Image must be smaller than 5 MB.")
    return ALLOWED_PROFILE_PHOTO_CONTENT_TYPES[normalized]


def _candidate_photo_url(candidate: dict, raw_data: dict) -> Optional[str]:
    candidate_id = str(candidate.get("id") or candidate.get("candidate_id") or "")
    if not candidate_id:
        stored_url = candidate.get("photo_url")
        return _clean_str(stored_url) or None

    stored_url = raw_data.get("photo_url")
    if _clean_str(stored_url):
        return stored_url

    profile_url = candidate.get("photo_url")
    if _clean_str(profile_url):
        return profile_url

    stored_path = raw_data.get("photo_file_path")
    if _resolve_candidate_photo_path(candidate_id, stored_path):
        return _photo_view_url(candidate_id, raw_data.get("photo_version"))

    return None


def _restore_candidate_photo(candidate_id: str, raw_data: dict) -> Optional[Path]:
    """Restore a persisted photo when ephemeral local document storage changed."""
    encoded = raw_data.get("photo_content_base64")
    destination = _resolve_candidate_photo_path(candidate_id, raw_data.get("photo_file_path"))
    if not isinstance(encoded, str) or not encoded or not destination:
        return None
    try:
        content = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        logger.warning("[candidate-photo] candidate=%s invalid persisted photo content", candidate_id)
        return None
    if not content or len(content) > MAX_PROFILE_PHOTO_BYTES:
        logger.warning("[candidate-photo] candidate=%s invalid persisted photo size=%s", candidate_id, len(content))
        return None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    except OSError as exc:
        logger.warning("[candidate-photo] candidate=%s could not restore path=%s: %s", candidate_id, destination, exc)
        return None
    logger.info("[candidate-photo] candidate=%s restored path=%s bytes=%d", candidate_id, destination, len(content))
    return destination


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_list_items(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if isinstance(item, str) and item.strip():
            return True
        if isinstance(item, dict) and any(
            _has_text(item.get(key))
            for key in ("name", "title", "degree", "institution", "company", "description", "value")
        ):
            return True
    return False


def _has_candidate_certificates(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if isinstance(item, dict):
            if any(_has_text(item.get(key)) for key in ("id", "file_name", "file_path")):
                return True
            if item:
                return True
        elif _has_text(item):
            return True
    return False


def _prefer_canonical_value(primary: Any, fallback: Any) -> Any:
    """Keep canonical profile data when present; only fall back to older resume JSON."""
    if _has_text(primary) or _has_list_items(primary):
        return primary
    if isinstance(primary, (int, float)) and primary is not None:
        return primary
    if primary is not None and primary != "":
        return primary
    return fallback


def _build_profile_strength_source(profile: dict, raw_data: Optional[dict] = None) -> dict:
    """
    Build the profile view used for strength scoring.

    Canonical DB columns win over parsed_resume_json so stale resume snapshots
    cannot override richer profile data already stored in the candidate row.
    """
    raw = raw_data if isinstance(raw_data, dict) else _parse_raw_data(profile.get("raw_data"))
    parsed_resume = _parse_raw_data(profile.get("parsed_resume_json"))

    strength_profile = dict(profile)
    strength_profile["raw_data"] = raw

    fallback_map = {
        "name": parsed_resume.get("name"),
        "email": parsed_resume.get("email"),
        "phone": parsed_resume.get("phone"),
        "location": parsed_resume.get("location"),
        "headline": parsed_resume.get("headline"),
        "current_role": parsed_resume.get("current_role") or parsed_resume.get("headline"),
        "current_company": parsed_resume.get("current_company"),
        "summary": parsed_resume.get("summary") or parsed_resume.get("bio"),
        "experience_years": parsed_resume.get("experience_years"),
        "skills": parsed_resume.get("skills"),
        "work_experience": parsed_resume.get("work_experience"),
        "education": parsed_resume.get("education"),
        "certifications": parsed_resume.get("certifications"),
        "photo_url": parsed_resume.get("photo_url"),
    }

    for field, fallback in fallback_map.items():
        strength_profile[field] = _prefer_canonical_value(strength_profile.get(field), fallback)

    strength_profile["candidate_certificates"] = profile.get("candidate_certificates") or []

    return strength_profile


def _apply_profile_strength_test_override(candidate: dict, result: dict) -> dict:
    """Apply the temporary, candidate-specific effective Profile Strength override."""
    if str(candidate.get("id") or candidate.get("candidate_id") or "") != "53a744f8-3292-4339-8533-f9a2f2f93e96":
        return result

    # TODO: Remove this temporary test override after validating 90% job access.
    overridden = dict(result)
    overridden["percent"] = 90
    overridden["label"] = "Strong"
    overridden["profile_strength"] = {
        **(result.get("profile_strength") or {}),
        "percent": 90,
        "label": "Strong",
    }
    return overridden


async def _effective_profile_strength_percent(candidate_id: str, candidate: dict) -> int:
    """Calculate the effective strength used to authorize job visibility."""
    from profile_strength_service import calculate_profile_strength_v2

    scoring_candidate = dict(candidate)
    scoring_candidate["candidate_certificates"] = await _load_candidate_certificates(candidate_id)
    raw_data = _parse_raw_data(scoring_candidate.get("raw_data"))
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT * FROM candidate_preferences WHERE candidate_id = :cid LIMIT 1"),
            {"cid": candidate_id},
        )
        prefs_row_result = row.mappings().fetchone()
    prefs_row = dict(prefs_row_result) if prefs_row_result else None

    result = calculate_profile_strength_v2(scoring_candidate, raw_data, prefs_row)
    effective_result = _apply_profile_strength_test_override(scoring_candidate, result)
    return effective_result["percent"]


def _has_work_experience(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            continue
        if any(_has_text(item.get(key)) for key in ("title", "company", "description", "summary")):
            return True
    return False


def _has_photo(profile: dict, raw_data: dict) -> bool:
    return bool(_candidate_photo_url(profile, raw_data))


def _has_target_role(profile: dict, raw_data: dict) -> bool:
    return any(
        _has_text(value)
        for value in (
            profile.get("headline"),
            profile.get("current_role"),
        )
    ) or _has_list_items(raw_data.get("preferred_roles"))


_PROJECT_HINT_PATTERNS = (
    r"\bproject(?:s)?\b",
    r"\bportfolio\b",
    r"\bcase study\b",
    r"\binitiative(?:s)?\b",
    r"\bwork sample(?:s)?\b",
)


def _has_projects(profile: dict, raw_data: dict) -> bool:
    if _has_list_items(raw_data.get("projects")) or _has_list_items(profile.get("projects")):
        return True

    project_summary = raw_data.get("project_summary") or raw_data.get("projects_summary")
    if _has_text(project_summary):
        return True

    voice_intake = raw_data.get("voice_intake")
    completed_turns = (voice_intake or {}).get("completed_turns") or []
    for turn in completed_turns:
        question = _clean_str((turn or {}).get("question"))
        answer = _clean_str((turn or {}).get("answer"))
        if not answer:
            continue
        if any(re.search(pattern, question, re.IGNORECASE) for pattern in _PROJECT_HINT_PATTERNS):
            return True
        if any(re.search(pattern, answer, re.IGNORECASE) for pattern in _PROJECT_HINT_PATTERNS):
            return True
    return False


def _has_preferences(profile: dict, raw_data: dict) -> bool:
    values = [
        profile.get("location"),
        raw_data.get("availability"),
        raw_data.get("location_preferences"),
        raw_data.get("work_type_preference"),
        raw_data.get("notice_period"),
        raw_data.get("salary_expectation"),
        raw_data.get("career_goals"),
        raw_data.get("target_industries"),
    ]
    return any(_has_text(v) or _has_list_items(v) for v in values)


PROFILE_STRENGTH_WEIGHTS = (
    ("name", 5),
    ("email", 5),
    ("photo", 5),
    ("work_experience", 15),
    ("skills", 15),
    ("target_role", 15),
    ("projects", 15),
    ("education", 10),
    ("certifications", 5),
    ("preferences", 10),
)


_FRESHER_ROLE_HINTS = (
    "student",
    "intern",
    "fresher",
    "graduate",
    "new grad",
    "entry level",
    "junior",
    "trainee",
)


def _is_fresher_profile(profile: dict, raw_data: dict) -> bool:
    """Best-effort fresher detection so missing work history is not penalized."""
    if _has_work_experience(profile.get("work_experience") or profile.get("experience")):
        return False

    years = profile.get("experience_years")
    try:
        if years is not None and float(years) >= 2:
            return False
    except (TypeError, ValueError):
        pass

    role_text = " ".join(
        str(value).lower()
        for value in (
            profile.get("headline"),
            profile.get("current_role"),
            raw_data.get("current_role"),
        )
        if _has_text(value)
    )
    if any(hint in role_text for hint in _FRESHER_ROLE_HINTS):
        return True

    # If there is no work history and no strong seniority signal, treat the
    # profile as fresher so skills/education/projects can carry the score.
    return not role_text or not any(token in role_text for token in ("senior", "lead", "principal", "manager", "director"))


def _profile_strength_weights(profile: dict, raw_data: dict) -> dict[str, int]:
    weights = {name: weight for name, weight in PROFILE_STRENGTH_WEIGHTS}
    if not _is_fresher_profile(profile, raw_data):
        return weights

    weights["work_experience"] = 0
    weights["skills"] += 4
    weights["projects"] += 4
    weights["education"] += 2
    weights["certifications"] += 2
    weights["target_role"] += 2
    weights["preferences"] += 1
    return weights


def _calculate_profile_strength(profile: dict, raw_data: Optional[dict] = None, prefs_row: Optional[dict] = None) -> tuple[int, str]:
    """Delegate to the new layered scoring engine. Returns (percent, label)."""
    from profile_strength_service import calculate_profile_strength_compat
    strength_profile = _build_profile_strength_source(profile, raw_data)
    return calculate_profile_strength_compat(
        strength_profile, strength_profile.get("raw_data"), prefs_row
    )


def _clean_str(value: Any) -> str:
    return " ".join(str(value).split()) if value is not None else ""


# High-level intake topics — used as hints to the LLM, not as hardcoded question wording.
VOICE_INTAKE_TOPICS = [
    "background_experience",
    "skills_technologies",
    "target_role",
    "career_preferences",
    "responsibilities_projects",
    "education_certifications",
    "availability_location",
]

VOICE_INTAKE_TOTAL_QUESTIONS = len(VOICE_INTAKE_TOPICS)

# Setup/greeting questions that must never count as intake questions even though
# they end with "?".
_SETUP_QUESTION_PATTERNS = (
    r"^how are you",
    r"^how(?:'re| are) you doing",
    r"^how is your day going",
    r"^how'?s your day going",
    r"^what are you doing today",
    r"^what are you up to today",
    r"^are you ready",
    r"^are you there",
    r"^(?:hi|hello|hey)[,!]?",
    r"^(?:good (?:morning|afternoon|evening))[,!]?",
    r"^(?:nice to meet you|great to meet you)",
    r"^(?:shall we (?:get started|begin|start))",
    r"^(?:ready to (?:get started|begin|start))",
    r"^(?:can you hear me)",
)

# Candidate phrases that are clarification requests, not real answers.
_CLARIFICATION_PATTERNS = (
    r"^can you (?:please )?repeat(?: the question)?",
    r"^(?:could you )?(?:please )?repeat(?: that)?",
    r"^can you (?:please )?(?:re-?frame|rephrase|restate)(?: the question)?",
    r"^(?:i )?(?:don'?t|cannot|can'?t) (?:understand|get|hear)(?: (?:you|that|it|the question))?",
    r"^(?:i )?(?:didn'?t|could not|couldn'?t) (?:understand|get|hear)(?: (?:you|that|it|the question))?",
    r"^(?:sorry[,.]? )?(?:i )?(?:don'?t|didn'?t) (?:understand|get)(?: (?:you|that|it|the question))?",
    r"^(?:are you (?:there|still there))[?.]?",
    r"^(?:hello)[?!]?$",
    r"^(?:can you hear me)[?.]?",
    r"^(?:is (?:anyone|somebody) there)[?.]?",
    r"^(?:what did you say)[?.]?",
    r"^(?:i can'?t (?:hear|get) (?:you|that|it))[.?]?",
)


# Short acknowledgement phrases Eve uses that are NOT real intake questions.
_ACK_PREFIXES = (
    "thanks", "thank you", "great", "got it", "perfect", "noted", "understood",
    "awesome", "sounds good", "i see", "okay", "ok,", "alright", "sure",
    "absolutely", "wonderful", "nice", "good to know", "i'll note", "i've noted",
    "interesting", "that's useful",
)

# Exact-match acknowledgements that must NEVER become a pending_question.
_ACK_EXACT = frozenset({
    "thanks", "thanks.", "thanks for that", "thanks for that.",
    "thanks for sharing", "thanks for sharing that", "thanks for sharing that.",
    "got it", "got it.", "great", "great.", "interesting", "interesting.",
    "understood", "understood.", "that's useful", "that's useful.",
    "thanks, that's useful.", "thanks, that's useful",
})

_USER_ACK_PREFIXES = (
    "yes", "yeah", "yep", "yup", "ready", "i'm ready", "im ready",
    "sure", "absolutely", "okay", "ok", "of course", "sounds good",
)

_USER_ACK_EXACT = frozenset({
    "doing good",
    "doing good what about you",
    "i'm good",
    "im good",
    "i am good",
    "i'm fine",
    "im fine",
    "fine",
    "good",
    "hello",
    "hi",
    "hey",
    "yes",
    "yes i'm ready",
    "yes im ready",
    "yes ready",
})


def _is_acknowledgement(text: str) -> bool:
    """Return True when an assistant turn is a short acknowledgement, not a real question."""
    t = text.lower().strip()
    if t in _ACK_EXACT:
        return True
    # Must be short and not end with a question mark to be an acknowledgement
    if "?" in t:
        return False
    if len(t) > 120:
        return False
    return any(t.startswith(p) for p in _ACK_PREFIXES)


def _is_brief_user_acknowledgement(text: str) -> bool:
    """Return True for short setup answers that should not be counted as intake progress."""
    t = _clean_str(text).lower().strip().rstrip(" .!?")
    if not t:
        return False
    if len(t) > 40:
        return False
    if t in _USER_ACK_EXACT:
        return True
    return any(t == p or t.startswith(f"{p} ") for p in _USER_ACK_PREFIXES)


def _is_setup_question(text: str) -> bool:
    """Return True if the text is a setup/greeting question (not an intake question)."""
    t = text.lower().strip()
    return any(re.search(p, t) for p in _SETUP_QUESTION_PATTERNS)


def _is_clarification_request(text: str) -> bool:
    """Return True if the candidate turn is a clarification/connection-check, not a real answer."""
    t = _clean_str(text).lower().strip().rstrip(" .!?")
    if not t:
        return False
    # Must be short — real answers are longer
    if len(t) > 80:
        return False
    return any(re.search(p, t) for p in _CLARIFICATION_PATTERNS)


# Statement-form intake prompts that don't end with '?' but are real information requests.
_INTAKE_STATEMENT_PATTERNS = (
    r"\btell me about (?:your|the) background\b",
    r"\btell me about yourself\b",
    r"\bdescribe your (?:background|experience|skills)\b",
    r"\bwalk me through your (?:background|experience|career)\b",
    r"\bshare (?:your|a bit about your) background\b",
)

_INTAKE_QUESTION_PATTERNS = (
    r"\bopportunit(?:y|ies)\b",
    r"\brole(?:s)?\b",
    r"\bjob(?:s)?\b",
    r"\bcareer(?:s)?\b",
    r"\bbackground\b",
    r"\bexperience\b",
    r"\bskill(?:s)?\b",
    r"\btechnolog(?:y|ies)\b",
    r"\bframework(?:s)?\b",
    r"\bproject(?:s)?\b",
    r"\bteam\b",
    r"\benvironment(?:s)?\b",
    r"\bresponsibilit(?:y|ies)\b",
    r"\bindustr(?:y|ies)\b",
    r"\blocation\b",
    r"\bremote\b",
    r"\bonsite\b",
    r"\beducation\b",
    r"\bdegree\b",
    r"\bcertification(?:s)?\b",
    r"\bavailability\b",
    r"\bnotice period\b",
    r"\bsalary\b",
    r"\bcompensation\b",
    r"\bcurrent (?:role|job|company)\b",
    r"\bnext (?:role|job|opportunity|move)\b",
)

_QUESTION_START_PATTERN = re.compile(
    r"\b(?:"
    r"what(?:'s| is| kind of| type of| would| do| are)?|"
    r"which|how|who|why|where|when|"
    r"tell me|can you|could you|would you|do you|are you|is your"
    r")\b",
    re.IGNORECASE,
)


_ASSISTANT_FRAGMENT_FILLERS = frozenset({"um", "uh", "er", "ah"})
_ASSISTANT_FRAGMENT_CONNECTORS = frozenset({
    "using", "with", "for", "to", "in", "on", "at", "from", "about", "of", "and", "or", "but",
})


def _normalize_assistant_question_clause(clause: str) -> str:
    """Clean a single assistant clause before question extraction."""
    text = _clean_str(clause)
    if not text:
        return ""

    lowered = text.lower().strip(" ,.-")
    if lowered in _ASSISTANT_FRAGMENT_FILLERS:
        return ""

    text = re.sub(r"\b(?:um|uh|er|ah)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    if not text:
        return ""

    lowered = text.lower()
    if lowered in _ASSISTANT_FRAGMENT_CONNECTORS:
        return lowered
    return text


def _normalize_extracted_question_text(question_text: str) -> str:
    """Normalize extracted assistant question text into one canonical question."""
    text = _clean_str(question_text)
    if not text:
        return ""

    clauses = [c.strip() for c in re.split(r"(?<=[?.!])\s+", text) if c.strip()]
    normalized_clauses = [_normalize_assistant_question_clause(clause) for clause in clauses]
    text = " ".join(clause for clause in normalized_clauses if clause)
    text = re.sub(r"\s+[,.!?]+\s+(?=\b(?:using|with|for|to|in|on|at|from|about|of|and|or|but)\b)", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:using|with|for|to|in|on|at|from|about|of|and|or|but)\.\s+(?=\w)", lambda m: f"{m.group(0).split('.')[0]} ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:um|uh|er|ah)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    return text


def _extract_question_from_assistant_turn(text: str) -> Optional[str]:
    """
    Extract the actual intake question from an assistant turn.
    Handles both '?' questions and statement-form intake prompts.
    """
    cleaned = _clean_str(text)
    if not cleaned:
        return None

    clauses = [c.strip() for c in re.split(r"(?<=[?.!])\s+", cleaned) if c.strip()]
    intake_start_idx: Optional[int] = None
    for idx, clause in enumerate(clauses):
        lowered = clause.lower().rstrip(" .!?")
        if any(re.search(p, lowered) for p in _INTAKE_STATEMENT_PATTERNS) or _QUESTION_START_PATTERN.search(clause):
            intake_start_idx = idx
            break

    # Check statement-form intake prompts (e.g. "Tell me about your background.")
    for clause in reversed(clauses):
        lowered = clause.lower().rstrip(" .!?")
        if any(re.search(p, lowered) for p in _INTAKE_STATEMENT_PATTERNS):
            return clause

    question_clause = next((clause for clause in reversed(clauses) if "?" in clause), "")
    if not question_clause:
        if intake_start_idx is None:
            return None
        question_clause = " ".join(clauses[intake_start_idx:])

    question_text = question_clause[: question_clause.rfind("?") + 1]
    if question_text == question_clause and not question_text.endswith("?"):
        question_text = question_text.rstrip(" .!,-") + "?"
    matches = list(_QUESTION_START_PATTERN.finditer(question_text))
    if matches:
        first_match = matches[0]
        prefix = question_text[: first_match.start()].strip(" ,.-")
        prefix_word_count = len(re.findall(r"[a-zA-Z]+", prefix))
        if first_match.start() > 0 and prefix_word_count <= 5:
            question_text = question_text[first_match.start():]

    if not _is_intake_question(question_text) and intake_start_idx is not None:
        question_text = _clean_str(" ".join(clauses[intake_start_idx:]))

    question_text = _normalize_extracted_question_text(question_text)
    if not question_text:
        return None

    for filler in ("Using", "With", "For", "To", "In", "On", "At", "From", "About", "Of", "And", "Or", "But"):
        question_text = re.sub(rf"\b{filler}\b", filler.lower(), question_text)

    question_text = question_text[0].upper() + question_text[1:]

    if not question_text.endswith("?"):
        question_text = question_text.rstrip(".") + "?"
    if _is_setup_question(question_text):
        return None
    if _is_acknowledgement(question_text):
        return None
    if not _is_intake_question(question_text):
        return None
    return question_text


def _is_intake_question(text: str) -> bool:
    """Return True when an assistant question is genuinely asking for career/profile information."""
    cleaned = _clean_str(text)
    if not cleaned:
        return False

    if _is_employment_gap_question(cleaned):
        return True
    lowered = cleaned.lower()
    if any(re.search(pattern, lowered) for pattern in _INTAKE_STATEMENT_PATTERNS):
        return True
    return any(re.search(pattern, lowered) for pattern in _INTAKE_QUESTION_PATTERNS)


def _is_employment_gap_question(text: str) -> bool:
    lowered = _clean_str(text).lower()
    if "gap" not in lowered:
        return False
    return (
        "what should i note" in lowered
        or "what happened" in lowered
        or "can you explain" in lowered
        or "tell me about" in lowered
        or "share" in lowered
    )


def _questions_are_rephrasing(q1: str, q2: str) -> bool:
    """
    Return True if q2 is likely a rephrasing of q1 (same topic, different wording).
    Heuristic: significant word overlap (>= 40% of the shorter question's content words).
    """
    if not q1 or not q2:
        return False
    stop = {"a", "an", "the", "is", "are", "was", "were", "do", "does", "did",
            "you", "your", "me", "my", "i", "it", "in", "on", "at", "to", "of",
            "and", "or", "but", "for", "with", "that", "this", "what", "how",
            "would", "could", "can", "will", "like", "about", "any", "some"}
    w1 = {w for w in re.findall(r"[a-z]+", q1.lower()) if w not in stop and len(w) > 2}
    w2 = {w for w in re.findall(r"[a-z]+", q2.lower()) if w not in stop and len(w) > 2}
    if not w1 or not w2:
        return False
    overlap = len(w1 & w2)
    shorter = min(len(w1), len(w2))
    return overlap / shorter >= 0.4


def _question_in_completed_turns(question: str, completed_turns: list[dict]) -> bool:
    """Return True when question matches one of the completed turns."""
    cleaned_question = _clean_str(question)
    if not cleaned_question:
        return False

    for turn in completed_turns:
        turn_question = _clean_str(turn.get("question"))
        if not turn_question:
            continue
        if cleaned_question == turn_question:
            return True
        if _questions_are_rephrasing(cleaned_question, turn_question):
            return True
        if _questions_are_rephrasing(turn_question, cleaned_question):
            return True
    return False


def _looks_fragmentary_question(question: str) -> bool:
    """Heuristic for short assistant fragments that should not be treated as the active current question."""
    cleaned = _clean_str(question)
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if any(re.search(p, lowered) for p in _INTAKE_STATEMENT_PATTERNS):
        return False
    if _QUESTION_START_PATTERN.search(cleaned):
        return False

    words = re.findall(r"[a-zA-Z]+", cleaned)
    if len(words) <= 6 and cleaned.endswith("?"):
        return True
    return len(words) <= 4


def _choose_active_current_question(current_question: str, next_question: str) -> str:
    """Prefer the fuller next_question when the saved current_question is only a fragment."""
    cleaned_current = _clean_str(current_question)
    cleaned_next = _clean_str(next_question)
    if cleaned_current and cleaned_next and _looks_fragmentary_question(cleaned_current):
        return cleaned_next
    return cleaned_current


def _voice_notes_from_transcript(transcript: str) -> list[dict]:
    """Best-effort parser for speaker-labelled transcripts when explicit voice_notes are absent."""
    notes: list[dict] = []
    for raw_line in (transcript or "").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        speaker, content = line.split(":", 1)
        role = speaker.strip().lower()
        text = content.strip()
        if not text:
            continue
        if role in {"assistant", "eve"}:
            notes.append({"role": "assistant", "text": text, "final": True})
        elif role in {"candidate", "user"}:
            notes.append({"role": "user", "text": text, "final": True})
    return notes


_VOICE_INTAKE_OFFTOPIC_PATTERNS = (
    r"\bappointment\b",
    r"\bcar\b",
    r"\bdoctor\b",
    r"\bdentist\b",
    r"\bclinic\b",
    r"\bhospital\b",
    r"\bmedication\b",
    r"\binsurance\b",
    r"\bpickup\b",
    r"\bdrop[- ]?off\b",
    r"\bcommute\b",
    r"\bschool\b",
    r"\bfamily\b",
    r"\bkid(?:s)?\b",
    r"\bhome\b",
    r"\blunch\b",
    r"\bdinner\b",
    r"\bweekend\b",
)

_VOICE_INTAKE_WORK_PATTERNS = (
    r"\bjob\b",
    r"\brole\b",
    r"\bwork\b",
    r"\bcareer\b",
    r"\bexperience\b",
    r"\bdeveloper\b",
    r"\bengineer\b",
    r"\bbackend\b",
    r"\bfrontend\b",
    r"\bjava\b",
    r"\bpython\b",
    r"\bspring\b",
    r"\bfastapi\b",
    r"\bproject\b",
    r"\btechnology\b",
    r"\bskills?\b",
    r"\bindustry\b",
    r"\bteam\b",
    r"\bapi\b",
)


def _candidate_fragment_is_off_topic(question: str, fragment: str) -> bool:
    """
    Best-effort filter for clearly unrelated candidate fragments.

    We only drop fragments when they look like logistics / personal questions and
    do not contain obvious work or career signals. Short but relevant fragments
    are preserved.
    """
    cleaned = _clean_str(fragment)
    if not cleaned:
        return False

    lowered = cleaned.lower()
    if any(re.search(pattern, lowered) for pattern in _VOICE_INTAKE_WORK_PATTERNS):
        return False

    off_topic_hits = sum(1 for pattern in _VOICE_INTAKE_OFFTOPIC_PATTERNS if re.search(pattern, lowered))
    if off_topic_hits == 0:
        return False

    if lowered.endswith("?"):
        return True

    if re.search(r"^(do|does|did|is|are|was|were|can|could|would|will|should|have|has|had)\b", lowered):
        return True

    question_words = set(re.findall(r"[a-z]+", _clean_str(question).lower()))
    fragment_words = set(re.findall(r"[a-z]+", lowered))
    if question_words and fragment_words and len(question_words & fragment_words) == 0:
        return True

    return off_topic_hits >= 2


def _normalize_voice_notes(voice_notes: Any, transcript: str = "") -> list[dict]:
    if voice_notes:
        normalized: list[dict] = []
        for note in voice_notes:
            if isinstance(note, dict):
                normalized.append(
                    {
                        "role": note.get("role"),
                        "text": note.get("text", ""),
                        "final": note.get("final", True),
                    }
                )
            else:
                normalized.append(
                    {
                        "role": getattr(note, "role", None),
                        "text": getattr(note, "text", ""),
                        "final": getattr(note, "final", True),
                    }
                )
        return normalized
    return _voice_notes_from_transcript(transcript)


def _voice_intake_turn_pairs(voice_notes: Any, transcript: str = "") -> tuple[list[dict], Optional[str]]:
    """
    Parse voice_notes into completed Q&A pairs.

    Rules:
    - Only final turns are considered.
    - Consecutive assistant fragments are combined before question detection.
    - An assistant turn that is a short acknowledgement does NOT start a new question;
      the previous real question remains active.
    - Any assistant turn containing a real question (ends with '?', not setup/greeting)
      is treated as an intake question — regardless of exact wording.
    - If a new question is a rephrasing of the current pending question, keep the
      original pending question text (do not create a new turn).
    - Consecutive user fragments belonging to the same answer are combined.
    - Candidate clarification requests ("Can you repeat?", "Are you there?", etc.)
      do NOT count as answers and do NOT clear the pending question.
    - User turns that occur before the first real question are discarded.
    - Returns (completed_pairs, pending_question) where pending_question is the last
      real assistant question that has not yet received a genuine user answer.
    """
    completed: list[dict] = []
    pending_question: Optional[str] = None
    answer_parts: list[str] = []
    # Buffer consecutive assistant fragments so multi-fragment questions are combined
    assistant_buffer: list[str] = []

    def _flush_assistant_buffer() -> None:
        """Process buffered assistant fragments as one combined turn."""
        nonlocal pending_question, answer_parts
        if not assistant_buffer:
            return
        combined = " ".join(assistant_buffer)
        assistant_buffer.clear()

        if _is_acknowledgement(combined):
            return

        detected_q = _extract_question_from_assistant_turn(combined)
        if detected_q:
            # Check if this is a rephrasing of the current pending question
            if pending_question and _questions_are_rephrasing(pending_question, detected_q):
                # Same question rephrased — keep original pending question, don't flush
                return
            # New real question: flush any accumulated answer first
            if answer_parts and pending_question:
                completed.append({
                    "question": pending_question,
                    "answer": " ".join(answer_parts),
                })
                answer_parts = []
            pending_question = detected_q
        else:
            # Non-question assistant chatter should not disturb the active intake
            # question. This includes setup/greeting context and profile statements.
            pass

    for note in _normalize_voice_notes(voice_notes, transcript):
        role = note.get("role")
        text = note.get("text", "")
        is_final = note.get("final", True)

        if not is_final:
            continue

        cleaned = _clean_str(text)
        if not cleaned:
            continue

        if role == "assistant":
            assistant_buffer.append(cleaned)
        elif role == "user":
            # Flush buffered assistant text before processing user turn
            _flush_assistant_buffer()

            if _is_brief_user_acknowledgement(cleaned):
                continue
            if _is_clarification_request(cleaned):
                # Keep pending_question active; do not count as an answer
                continue
            if pending_question and _candidate_fragment_is_off_topic(pending_question, cleaned):
                continue
            if pending_question:
                answer_parts.append(cleaned)
            # else: before first real question — discard

    # Flush any remaining assistant buffer
    _flush_assistant_buffer()

    # Flush any trailing answer that hasn't been closed yet
    if answer_parts and pending_question:
        completed.append({
            "question": pending_question,
            "answer": " ".join(answer_parts),
        })
        pending_question = None

    return completed, pending_question


def _next_voice_intake_question(completed_turns: list[dict], pending_question: Optional[str] = None) -> Optional[str]:
    """Return the pending question if one exists; otherwise None (LLM will determine next)."""
    return pending_question or None


# ---------- LLM-driven intake analysis ----------

VOICE_INTAKE_ANALYZE_SYSTEM = """You are an expert recruitment assistant analyzing a voice intake conversation.

You will receive:
- candidate_profile: existing resume/profile data already known
- conversation: the voice intake conversation so far (Q&A pairs already completed + any partial answer)
- intake_topics: high-level topics that should be covered

Your job:
1. Determine what information is already known (from profile OR conversation)
2. Identify what was newly provided in the conversation
3. Identify which intake topics are still genuinely missing
4. Determine if the current pending question has been answered
5. Suggest the single most useful next question to ask (or null if all topics covered)

Rules:
- Do NOT ask about information already present in the candidate profile
- Do NOT create duplicate questions for the same topic
- A topic is covered if the candidate provided meaningful information about it anywhere in the conversation or profile
- Before completing career_preferences/availability_location, explicitly collect any still-unknown work mode (Remote/Hybrid/On-site/Flexible), preferred industries, employment type, preferred locations, expected salary, and relocation preference. A candidate may decline a field; record that rather than guessing.
- next_question must be a natural, conversational question — not a hardcoded template
- If all important topics are covered, set next_question to null and completed to true

Return ONLY valid JSON:
{
  "known_topics": ["topic1", "topic2"],
  "missing_topics": ["topic3"],
  "newly_provided": {"topic": "summary of what candidate said"},
  "current_question_answered": true,
  "next_question": "What kind of backend role are you targeting next?",
  "completed": false
}"""


async def _llm_analyze_intake(
    candidate_profile: dict,
    completed_turns: list[dict],
    pending_question: Optional[str],
    partial_answer: str = "",
) -> dict:
    """Ask the LLM to determine what's known, what's missing, and what to ask next."""
    raw_data = _parse_raw_data(candidate_profile.get("raw_data"))
    profile_summary = {
        "name": candidate_profile.get("name") or candidate_profile.get("name"),
        "current_role": candidate_profile.get("current_role") or candidate_profile.get("headline"),
        "current_company": candidate_profile.get("current_company"),
        "experience_years": candidate_profile.get("experience_years"),
        "skills": candidate_profile.get("skills") or [],
        "work_experience": [
            {"title": w.get("title"), "company": w.get("company")}
            for w in (candidate_profile.get("work_experience") or [])[:3]
        ],
        "education": [
            {"degree": e.get("degree"), "institution": e.get("institution")}
            for e in (candidate_profile.get("education") or [])[:2]
        ],
        "summary": candidate_profile.get("summary") or "",
        "preferred_roles": candidate_profile.get("preferred_roles") or [],
        "availability": candidate_profile.get("availability") or "",
        "salary_expectation": candidate_profile.get("salary_expectation") or raw_data.get("salary_expectation") or "",
    }

    context = {
        "candidate_profile": profile_summary,
        "conversation": completed_turns,
        "pending_question": pending_question,
        "partial_answer": partial_answer,
        "intake_topics": VOICE_INTAKE_TOPICS,
    }

    try:
        resp = await openai_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": VOICE_INTAKE_ANALYZE_SYSTEM},
                {"role": "user", "content": json.dumps(context)[:6000]},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        logger.warning("[voice-intake] LLM analysis failed: %s", e)
        return {}


def _merge_voice_intake_topic_list(existing_values: Any, new_values: Any) -> list[str]:
    """
    Preserve persisted topic state when the incoming LLM analysis is missing or
    empty, while still using any new non-empty analysis.
    """
    if isinstance(new_values, list) and new_values:
        source = new_values
    elif isinstance(existing_values, list):
        source = existing_values
    else:
        source = []

    merged: list[str] = []
    seen: set[str] = set()
    for value in source:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(cleaned)
    return merged


def _voice_intake_turns_to_transcript(completed_turns: list[dict]) -> str:
    """
    Reconstruct a cleaned transcript from completed turns.
    This intentionally excludes raw interrupted/off-topic fragments so profile
    extraction only sees the normalized conversational content.
    """
    lines: list[str] = []
    for turn in completed_turns:
        question = _clean_str(turn.get("question"))
        answer = _clean_str(turn.get("answer"))
        if question:
            lines.append(f"Assistant: {question}")
        if answer:
            lines.append(f"Candidate: {answer}")
    return "\n".join(lines)


def _promote_active_voice_question(
    current_question: str,
    next_question: str,
    completed_turns: list[dict],
) -> tuple[str, str]:
    """
    Keep the unanswered active question in current_question and ensure answered
    questions never remain there.
    """
    current = _clean_str(current_question)
    next_q = _clean_str(next_question)

    current_answered = _question_in_completed_turns(current, completed_turns)
    next_answered = _question_in_completed_turns(next_q, completed_turns)

    if current and current_answered:
        if next_q and not next_answered:
            current = next_q
            next_q = ""
        else:
            current = ""
    elif not current and next_q and not next_answered:
        current = next_q
        next_q = ""

    if current and next_q and _questions_are_rephrasing(current, next_q):
        next_q = ""

    return current, next_q


def _merge_completed_turns(existing_turns: list[dict], new_turns: list[dict]) -> list[dict]:
    """
    Return the union of existing and new completed turns.

    We dedupe by question text, but also treat rephrased questions as the same
    turn so repeated cumulative voice_notes do not create duplicates. Existing
    turns remain the source of truth unless the new answer is a longer cumulative
    version of the same turn.
    """
    merged = [dict(t) for t in existing_turns if _clean_str(t.get("question"))]

    def _find_match(question: str) -> Optional[int]:
        for idx, existing in enumerate(merged):
            existing_q = _clean_str(existing.get("question"))
            if not existing_q:
                continue
            if existing_q == question:
                return idx
            if _questions_are_rephrasing(existing_q, question) or _questions_are_rephrasing(question, existing_q):
                return idx
        return None

    for turn in new_turns:
        question = _clean_str(turn.get("question"))
        answer = _clean_str(turn.get("answer"))
        if not question:
            continue

        match_idx = _find_match(question)
        if match_idx is None:
            merged.append({"question": question, "answer": answer})
            continue

        existing = merged[match_idx]
        existing_answer = _clean_str(existing.get("answer"))
        if not existing_answer:
            existing["answer"] = answer
            continue
        if not answer or answer == existing_answer:
            continue
        if len(answer) > len(existing_answer) and existing_answer in answer:
            existing["answer"] = answer
        elif len(existing_answer) > len(answer) and answer in existing_answer:
            continue
        elif answer not in existing_answer:
            existing["answer"] = f"{existing_answer} {answer}".strip()
    return merged


def _build_voice_intake_resume_from_notes(
    voice_notes: Any,
    transcript: str = "",
    existing_resume: Optional[dict] = None,
    candidate_profile: Optional[dict] = None,
    llm_analysis: Optional[dict] = None,
) -> dict:
    """
    Build the voice intake resume state from conversation notes.
    Uses LLM analysis when available; falls back to structural parsing.

    completed_turns is ALWAYS the union of previously persisted turns and
    newly parsed turns — never a replacement.
    """
    new_turns, pending_question = _voice_intake_turn_pairs(voice_notes, transcript)

    existing_turns = (existing_resume or {}).get("completed_turns") or []
    normalized_incoming_notes = _normalize_voice_notes(voice_notes, transcript)
    source_turns = _merge_completed_turns(existing_turns, new_turns) if existing_turns else list(new_turns)
    completed_turns = [
        {
            "question": _clean_str(turn.get("question")),
            "answer": _clean_str(turn.get("answer")),
        }
        for turn in source_turns
        if _clean_str(turn.get("question"))
    ]
    progress = len(completed_turns)

    # Prefer LLM-derived state; fall back to persisted state; fall back to parsed state
    existing_next_q = (existing_resume or {}).get("next_question") or ""
    existing_current_q = (existing_resume or {}).get("current_question") or ""
    existing_missing = (existing_resume or {}).get("missing_topics") or []
    existing_known = (existing_resume or {}).get("known_topics") or []
    existing_status = str((existing_resume or {}).get("status") or "in_progress").lower()

    answered_question = _question_in_completed_turns(existing_current_q, completed_turns)

    if llm_analysis:
        llm_next_q = _clean_str(llm_analysis.get("next_question"))
        next_question = llm_next_q or existing_next_q
        is_completed = bool(llm_analysis.get("completed")) and not next_question
        missing_topics = _merge_voice_intake_topic_list(existing_missing, llm_analysis.get("missing_topics"))
        known_topics = _merge_voice_intake_topic_list(existing_known, llm_analysis.get("known_topics"))
    else:
        next_question = existing_next_q
        missing_topics = existing_missing
        known_topics = existing_known
        is_completed = False

    completion_ready = bool(llm_analysis and llm_analysis.get("completed")) and not next_question and not pending_question

    # Preserve the existing state machine's completion decision, but do not let
    # stale persisted missing_topics block the final transition once the last
    # required question has been answered.
    if completion_ready:
        is_completed = True
        missing_topics = []
    elif missing_topics or pending_question:
        is_completed = False

    if existing_status == "completed":
        return existing_resume  # type: ignore[return-value]

    current_question = pending_question
    if not current_question and existing_current_q and not answered_question:
        current_question = existing_current_q
    if not current_question and answered_question and next_question and not _question_in_completed_turns(next_question, completed_turns):
        current_question = next_question
    promoted_current = _choose_active_current_question(current_question or "", next_question or "")
    if promoted_current and promoted_current != current_question:
        current_question = promoted_current
        next_question = ""
    if current_question and next_question and _questions_are_rephrasing(current_question, next_question):
        # Preserve next_question only when it was already equal to current_question
        # in the persisted state (e.g. LLM returned empty and both fields held the same value).
        # When they were different before (promotion happened), clear next_question.
        if existing_current_q != existing_next_q or not existing_next_q:
            next_question = ""
    if not is_completed and completed_turns and not current_question and not next_question and not pending_question:
        # If the state machine has no active question left, normalize any stale
        # persisted in_progress snapshot into the completed form.
        is_completed = True
        missing_topics = []

    completed_turn_answers = {
        _normalize_profile_key(turn.get("question")): _clean_str(turn.get("answer"))
        for turn in completed_turns
        if _clean_str(turn.get("question")) and _clean_str(turn.get("answer"))
    }
    employment_gap = _detect_employment_gap(candidate_profile or {}) if candidate_profile else None
    employment_gaps = [dict(g) for g in ((existing_resume or {}).get("employment_gaps") or []) if isinstance(g, dict)]
    for gap in employment_gaps:
        question_key = _normalize_profile_key(gap.get("question"))
        answered_text = completed_turn_answers.get(question_key)
        if answered_text and not _clean_str(gap.get("answer")):
            gap["answer"] = answered_text
    def _answer_after_gap_question(question_text: str) -> str:
        question_key = _normalize_profile_key(question_text)
        for index, note in enumerate(normalized_incoming_notes):
            if _normalize_profile_key(note.get("text")) != question_key:
                continue
            if note.get("role") != "assistant":
                continue
            for follower in normalized_incoming_notes[index + 1 :]:
                if follower.get("role") != "user":
                    continue
                answer = _clean_str(follower.get("text"))
                if answer:
                    return answer
        return ""

    if employment_gap:
        existing_gap = next(
            (
                gap for gap in employment_gaps
                if _normalize_profile_key(gap.get("gap_key")) == _normalize_profile_key(employment_gap["gap_key"])
            ),
            None,
        )
        if existing_gap and _clean_str(existing_gap.get("answer")):
            employment_gap = None
        else:
            matched_gap_question = existing_gap.get("question") if existing_gap else employment_gap["question"]
            gap_answer = _clean_str(existing_gap.get("answer")) if existing_gap else ""
            if not gap_answer and matched_gap_question:
                gap_answer = _answer_after_gap_question(matched_gap_question)
                if gap_answer and existing_gap:
                    existing_gap["answer"] = gap_answer

            if gap_answer:
                employment_gap = None
                if _normalize_profile_key(current_question or "") == _normalize_profile_key(matched_gap_question):
                    current_question = None
                if _normalize_profile_key(next_question or "") == _normalize_profile_key(matched_gap_question):
                    next_question = ""
                # Mark gap as explained in employment_gaps list
                if existing_gap:
                    existing_gap["status"] = "explained"
            else:
                # Gap is unanswered — mandatory: override current/next question
                is_completed = False
                gap_q = employment_gap["question"]
                missing_topics = _merge_voice_intake_topic_list(missing_topics, ["employment_gap"])
                if existing_gap:
                    existing_gap["question"] = gap_q
                    existing_gap["gap_days"] = employment_gap["gap_days"]
                    existing_gap["previous_label"] = employment_gap["previous_label"]
                    existing_gap["current_label"] = employment_gap["current_label"]
                    existing_gap["previous_end_label"] = employment_gap["previous_end_label"]
                    existing_gap["current_start_label"] = employment_gap["current_start_label"]
                    existing_gap.setdefault("status", "unanswered")
                    answered_text = completed_turn_answers.get(_normalize_profile_key(gap_q))
                    if answered_text:
                        existing_gap["answer"] = answered_text
                        existing_gap["status"] = "explained"
                        employment_gap = None
                else:
                    employment_gaps.append({
                        "gap_key": employment_gap["gap_key"],
                        "question": gap_q,
                        "gap_days": employment_gap["gap_days"],
                        "previous_label": employment_gap["previous_label"],
                        "current_label": employment_gap["current_label"],
                        "previous_end_label": employment_gap["previous_end_label"],
                        "current_start_label": employment_gap["current_start_label"],
                        "status": "unanswered",
                    })
                # Mandatory: gap question takes priority over any other question
                if employment_gap is not None:
                    current_question = gap_q
                    next_question = ""

    status = "completed" if is_completed else "in_progress"
    if is_completed:
        current_question = None
        next_question = None

    resume: dict = {
        "status": status,
        "progress": progress,
        "voice_notes": _normalize_voice_notes(voice_notes, transcript) or (existing_resume or {}).get("voice_notes") or [],
        "completed_turns": completed_turns,
        "has_open_question": bool(current_question or next_question),
        "known_topics": known_topics,
        "missing_topics": missing_topics,
    }
    if employment_gaps:
        resume["employment_gaps"] = employment_gaps
    if completed_turns:
        resume["latest_completed_question"] = completed_turns[-1]["question"]
        resume["latest_completed_answer"] = completed_turns[-1]["answer"]
    if next_question:
        resume["next_question"] = next_question
    if current_question:
        resume["current_question"] = current_question

    return resume


def _build_voice_intake_resume(profile: dict) -> Optional[dict]:
    raw_data = _parse_raw_data(profile.get("raw_data"))
    voice_intake = _parse_raw_data(raw_data.get("voice_intake"))
    if not voice_intake:
        return None
    status = str(voice_intake.get("status") or "").lower()
    if status == "completed":
        return None
    status = "in_progress"

    saved_completed_turns = voice_intake.get("completed_turns") or []
    saved_progress = voice_intake.get("progress")
    saved_next_question = voice_intake.get("next_question")
    saved_current_question = voice_intake.get("current_question")
    saved_missing_topics = voice_intake.get("missing_topics") or []
    saved_known_topics = voice_intake.get("known_topics") or []

    if saved_completed_turns:
        completed_turns = saved_completed_turns
        progress = int(saved_progress) if saved_progress is not None else len(completed_turns)
        current_question = saved_current_question if not _question_in_completed_turns(saved_current_question, completed_turns) else None
        next_question = saved_next_question
    else:
        voice_notes = voice_intake.get("voice_notes") or []
        completed_turns, pending_question = _voice_intake_turn_pairs(voice_notes)
        progress = int(saved_progress) if saved_progress is not None else len(completed_turns)
        current_question = pending_question
        if not current_question and saved_current_question and not _question_in_completed_turns(saved_current_question, completed_turns):
            current_question = saved_current_question
        next_question = saved_next_question

    current_question, next_question = _promote_active_voice_question(current_question or "", next_question or "", completed_turns)

    resume = {
        "status": status,
        "progress": progress,
        "completed_turns": completed_turns,
        "has_open_question": bool(current_question or next_question),
        "missing_topics": saved_missing_topics,
        "known_topics": saved_known_topics,
    }
    if completed_turns:
        resume["latest_completed_question"] = completed_turns[-1]["question"]
        resume["latest_completed_answer"] = completed_turns[-1]["answer"]
    if next_question:
        resume["next_question"] = next_question
    if current_question:
        resume["current_question"] = current_question
    return resume


async def _save_voice_intake_resume(candidate_id: str, resume: dict) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text("""
                UPDATE candidates
                SET raw_data = jsonb_set(
                        COALESCE(raw_data, '{}'::jsonb),
                        '{voice_intake}',
                        CAST(:voice_intake AS jsonb),
                        true
                    ),
                    updated_at = now(),
                    updated_by_source = 'eve_voice'
                WHERE id = :cid
            """),
            {"voice_intake": json.dumps(resume), "cid": candidate_id},
        )
        await db.commit()


def _voice_intake_resume_to_voice_notes(
    resume: dict,
    candidate_answer: str = "",
) -> list[dict]:
    """Reconstruct voice notes from a persisted resume plus an optional current answer."""
    notes: list[dict] = []
    for turn in resume.get("completed_turns") or []:
        question = _clean_str(turn.get("question"))
        answer = _clean_str(turn.get("answer"))
        if not question:
            continue
        notes.append({"role": "assistant", "text": question, "final": True})
        if answer:
            notes.append({"role": "user", "text": answer, "final": True})

    current_question = _clean_str(resume.get("current_question"))
    candidate_answer = _clean_str(candidate_answer)
    if current_question and candidate_answer:
        notes.append({"role": "assistant", "text": current_question, "final": True})
        notes.append({"role": "user", "text": candidate_answer, "final": True})
    return notes


async def _advance_voice_intake_from_chat(
    candidate_id: str,
    candidate_message: str,
) -> Optional[dict]:
    """
    Advance persisted Voice Intake state when a chat message answers the active question.

    This reuses the same voice-intake parsing and resume builder as the VAPI flow.
    """
    candidate = await _get_candidate_row(candidate_id)
    raw_data = _parse_raw_data(candidate.get("raw_data"))
    existing_resume = _parse_raw_data(raw_data.get("voice_intake"))
    if not existing_resume:
        return None

    current_question = _clean_str(existing_resume.get("current_question"))
    if not current_question:
        if str(existing_resume.get("status") or "").lower() == "completed":
            return None
        if not existing_resume.get("has_open_question"):
            updated_resume = _build_voice_intake_resume_from_notes(
                [],
                "",
                existing_resume,
                candidate_profile=candidate,
            )
            if updated_resume != existing_resume:
                await _save_voice_intake_resume(candidate_id, updated_resume)
                return updated_resume
        return None

    completed_turns = existing_resume.get("completed_turns") or []
    if any(_clean_str(turn.get("question")) == current_question for turn in completed_turns):
        return None

    candidate_answer = _clean_str(candidate_message)
    if not candidate_answer:
        return None

    latest_completed_answer = _clean_str(existing_resume.get("latest_completed_answer"))
    latest_completed_question = _clean_str(existing_resume.get("latest_completed_question"))
    if (
        latest_completed_answer
        and candidate_answer == latest_completed_answer
        and latest_completed_question
        and latest_completed_question != current_question
    ):
        return None

    if _is_brief_user_acknowledgement(candidate_answer):
        return None
    if _is_clarification_request(candidate_answer):
        return None
    if _candidate_fragment_is_off_topic(current_question, candidate_answer):
        return None

    merged_turns = [dict(t) for t in completed_turns if _clean_str(t.get("question"))]
    if any(
        _clean_str(turn.get("question")) == current_question
        and _clean_str(turn.get("answer")) == candidate_answer
        for turn in merged_turns
    ):
        return None
    merged_turns.append({"question": current_question, "answer": candidate_answer})

    staged_resume = dict(existing_resume)
    staged_resume["completed_turns"] = merged_turns
    staged_resume["current_question"] = current_question
    staged_resume["progress"] = len(merged_turns)
    staged_resume["status"] = str(existing_resume.get("status") or "in_progress").lower()

    llm_analysis = await _llm_analyze_intake(
        candidate,
        merged_turns,
        None,
    )
    if not llm_analysis or not llm_analysis.get("current_question_answered"):
        return None

    updated_resume = _build_voice_intake_resume_from_notes(
        [],
        "",
        staged_resume,
        candidate_profile=candidate,
        llm_analysis=llm_analysis,
    )

    if updated_resume == existing_resume:
        return None

    await _save_voice_intake_resume(candidate_id, updated_resume)
    return updated_resume


async def _get_candidate_row(candidate_id: str) -> dict:
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT * FROM candidates WHERE id = :cid LIMIT 1"),
            {"cid": candidate_id},
        )
        result = row.mappings().fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    candidate = dict(result)
    # Subscription state is loaded from the payment ledger, never from a
    # client-controlled profile payload.
    try:
        async with SessionLocal() as db:
            active = await db.execute(text("""
                SELECT expires_at FROM candidate_subscriptions
                WHERE candidate_id = :cid AND status = 'active' AND expires_at > now()
                ORDER BY expires_at DESC LIMIT 1
            """), {"cid": candidate_id})
            expires_at = active.scalar()
        candidate["subscription_active"] = bool(expires_at)
        candidate["subscription_status"] = "active" if expires_at else "free"
    except Exception:
        # The billing migration may not have run in older installations yet.
        candidate["subscription_active"] = False
    return candidate


def _merge_resume_into_existing_profile(existing: dict, parsed: dict) -> dict:
    """
    Merge a freshly-parsed resume into an existing candidate profile.

    Rules:
    - Identity (name, email, phone) is ALWAYS taken from the existing DB row.
      The parsed resume values are ignored for these three fields.
    - Scalar profile fields (current_role, current_company, location, summary,
      experience_years) are updated from the new resume only when the existing
      value is empty/None.
    - List fields (skills, work_experience, education) are merged/enriched
      using the existing merge helpers — no duplicates, existing data preserved.
    """
    merged = dict(existing)
    parsed = _sanitize_profile_field_mapping(parsed)

    # A legacy extractor may have persisted a date range in location or a
    # technology in current_role. Treat only those invalid values as absent so
    # a subsequent valid resume value can repair the profile without a manual
    # database edit. Valid existing profile data remains authoritative.
    if merged.get("current_role") and not _is_actual_job_role(merged.get("current_role")):
        merged["current_role"] = ""
    if merged.get("location") and not _is_actual_location(merged.get("location")):
        merged["location"] = ""

    # Identity is always immutable
    merged["name"] = existing.get("name") or ""
    merged["email"] = existing.get("email") or ""
    merged["phone"] = existing.get("phone") or parsed.get("phone") or ""

    # Scalar enrichment: only fill if currently empty
    for field, parsed_key in (
        ("current_role", "current_role"),
        ("current_role", "headline"),
        ("current_company", "current_company"),
        ("location", "location"),
        ("summary", "bio"),
        ("summary", "summary"),
    ):
        if field == "current_role" and parsed_key == "headline" and not _is_actual_job_role(parsed.get(parsed_key)):
            continue
        if not merged.get(field) and parsed.get(parsed_key):
            merged[field] = parsed[parsed_key]

    # experience_years: keep existing if set; fill from new resume otherwise
    if merged.get("experience_years") is None and parsed.get("experience_years") is not None:
        try:
            merged["experience_years"] = float(parsed["experience_years"])
        except (TypeError, ValueError):
            pass

    # Merge lists
    merged["skills"] = _merge_skills(
        existing.get("skills") or [],
        parsed.get("skills") or [],
    )
    merged["work_experience"] = _merge_work_experience(
        existing.get("work_experience") or [],
        parsed.get("work_experience") or [],
    )
    merged["education"] = _merge_education(
        existing.get("education") or [],
        parsed.get("education") or [],
    )

    return merged


async def _upsert_candidate(parsed: dict, fingerprint: str, file_bytes: bytes,
                             original_filename: str, resume_text: str = "",
                             existing_id: Optional[str] = None,
                             force_new: bool = False) -> str:
    """
    Create or update a candidate record.
    Identity: existing_id (re-upload) > email match > fingerprint > new record.
    When force_new=True (new-candidate onboarding), always insert a fresh record.
    Returns the candidate UUID.
    """
    normalized_certs = _normalize_certifications(parsed.get("certifications") or [])
    normalized_skills = _normalize_skills(parsed.get("skills") or [], certifications=normalized_certs)
    parsed = dict(parsed)
    parsed["skills"] = normalized_skills
    parsed["certifications"] = normalized_certs
    skills_json = json.dumps(normalized_skills)
    work_exp_json = json.dumps(parsed.get("work_experience") or [])
    edu_json = json.dumps(parsed.get("education") or [])

    logger.info("[parse-resume] incoming existing_id=%s force_new=%s email=%s fingerprint=%s",
                existing_id, force_new, parsed.get("email"), fingerprint[:12])

    async with SessionLocal() as db:
        cid = None

        if not force_new:
            # 1. Prefer explicit existing_id (resume replace flow)
            if existing_id:
                r = await db.execute(
                    text("SELECT id FROM candidates WHERE id = :cid LIMIT 1"),
                    {"cid": existing_id},
                )
                if r.fetchone():
                    cid = existing_id
                    logger.info("[parse-resume] matched by existing_id=%s", cid)

            # 2. Match by email to avoid duplicates
            if not cid and parsed.get("email"):
                r = await db.execute(
                    text("SELECT id FROM candidates WHERE email = :email LIMIT 1"),
                    {"email": parsed["email"]},
                )
                row = r.fetchone()
                if row:
                    cid = str(row[0])
                    logger.info("[parse-resume] matched by email -> candidate_id=%s", cid)

            # 3. Check fingerprint (same file re-uploaded)
            if not cid:
                r = await db.execute(
                    text("SELECT candidate_id FROM internal_candidate_resumes WHERE resume_fingerprint = :fp LIMIT 1"),
                    {"fp": fingerprint},
                )
                row = r.fetchone()
                if row:
                    cid = str(row[0])
                    logger.info("[parse-resume] matched by fingerprint -> candidate_id=%s", cid)
        else:
            logger.info("[parse-resume] force_new=True — skipping email/fingerprint lookup, will insert new record")

        logger.info("[parse-resume] existing candidate found=%s candidate_id=%s", cid is not None, cid)

        # Store resume file first so we have the path for DB writes
        dest_dir = DOCS_DIR / cid if cid else DOCS_DIR / "tmp"
        # We need cid before storing; for new records generate it now
        if not cid:
            cid = str(uuid.uuid4())
            logger.info("[parse-resume] inserting new candidate_id=%s", cid)

        dest_dir = DOCS_DIR / cid / "resume"
        dest_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid.uuid4()}.pdf"
        dest_path = dest_dir / stored_name
        dest_path.write_bytes(file_bytes)

        if cid and await db.execute(text("SELECT 1 FROM candidates WHERE id = :cid LIMIT 1"), {"cid": cid}) and \
                (await db.execute(text("SELECT 1 FROM candidates WHERE id = :cid LIMIT 1"), {"cid": cid})).fetchone():
            # Preserve voice-derived raw_data when updating via resume
            existing_raw = await db.execute(
                text("SELECT raw_data FROM candidates WHERE id = :cid LIMIT 1"),
                {"cid": cid},
            )
            existing_raw_row = existing_raw.fetchone()
            existing_raw_data = {}
            if existing_raw_row and existing_raw_row[0]:
                try:
                    existing_raw_data = existing_raw_row[0] if isinstance(existing_raw_row[0], dict) else json.loads(existing_raw_row[0])
                except Exception:
                    existing_raw_data = {}
            existing_raw_data["certifications"] = _candidate_certification_sources(
                {
                    "raw_data": existing_raw_data,
                    "parsed_resume_json": parsed,
                }
            )
            existing_raw_data["projects"] = _merge_projects(
                existing_raw_data.get("projects"), parsed.get("projects")
            )

            # UPDATE existing candidate — merge resume into existing profile.
            # Identity (name, email, phone) is always preserved from the DB.
            # All other fields are merged/enriched rather than replaced.
            existing_row_result = await db.execute(
                text("SELECT name, email, phone, current_role, current_company, location, summary, skills, work_experience, education, experience_years FROM candidates WHERE id = :cid LIMIT 1"),
                {"cid": cid},
            )
            existing_row = existing_row_result.fetchone()
            if existing_row:
                existing_dict = {
                    "name": existing_row[0] or "",
                    "email": existing_row[1] or "",
                    "phone": existing_row[2] or "",
                    "current_role": existing_row[3] or "",
                    "current_company": existing_row[4] or "",
                    "location": existing_row[5] or "",
                    "summary": existing_row[6] or "",
                    "skills": existing_row[7] if isinstance(existing_row[7], list) else (json.loads(existing_row[7]) if existing_row[7] else []),
                    "work_experience": existing_row[8] if isinstance(existing_row[8], list) else (json.loads(existing_row[8]) if existing_row[8] else []),
                    "education": existing_row[9] if isinstance(existing_row[9], list) else (json.loads(existing_row[9]) if existing_row[9] else []),
                    "experience_years": existing_row[10],
                    "raw_data": existing_raw_data,
                }
            else:
                existing_dict = {"raw_data": existing_raw_data}
            merged = _merge_resume_into_existing_profile(existing_dict, parsed)
            merged_skills = _normalize_skills(merged.get("skills") or [], certifications=existing_raw_data.get("certifications") or [])
            merged_work_exp = merged.get("work_experience") or []
            merged_edu = merged.get("education") or []
            existing_raw_data["certifications"] = _candidate_certification_sources(
                {"raw_data": existing_raw_data, "parsed_resume_json": parsed}
            )
            await db.execute(
                text("""
                    UPDATE candidates SET
                        name = :name, email = :email, phone = :phone,
                        "current_role" = :current_role, current_company = :current_company,
                        location = :location, summary = :summary,
                        skills = CAST(:skills AS json), work_experience = CAST(:work_experience AS json),
                        education = CAST(:education AS json),
                        experience_years = :exp_years,
                        raw_data = CAST(:raw_data AS jsonb),
                        resume_file_path = :resume_file_path,
                        resume_text = :resume_text,
                        resume_received_at = now(),
                        parsed_resume_json = CAST(:parsed_resume_json AS jsonb),
                        parsed_resume_text = :parsed_resume_text,
                        parsing_status = 'completed',
                        updated_at = now(), updated_by_source = 'eve'
                    WHERE id = :cid
                """),
                {
                    "name": merged["name"],
                    "email": merged["email"],
                    "phone": merged["phone"],
                    "current_role": merged.get("current_role") or "",
                    "current_company": merged.get("current_company") or "",
                    "location": merged.get("location") or "",
                    "summary": merged.get("summary") or "",
                    "skills": json.dumps(merged_skills),
                    "work_experience": json.dumps(merged_work_exp),
                    "education": json.dumps(merged_edu),
                    "exp_years": merged.get("experience_years"),
                    "raw_data": json.dumps(existing_raw_data),
                    "resume_file_path": stored_name,
                    "resume_text": resume_text,
                    "parsed_resume_json": json.dumps(parsed),
                    "parsed_resume_text": resume_text,
                    "cid": cid,
                },
            )
        else:
            # INSERT new candidate with all resume fields
            await db.execute(
                text("""
                    INSERT INTO candidates
                        (id, name, email, phone, "current_role", current_company,
                         location, summary, skills, work_experience, education,
                         experience_years, source, created_by_source, updated_by_source,
                         parsing_status, resume_file_path, resume_text, resume_received_at, raw_data,
                         parsed_resume_json, parsed_resume_text,
                         created_at, updated_at)
                    VALUES
                        (:cid, :name, :email, :phone, :current_role, :current_company,
                         :location, :summary, CAST(:skills AS json), CAST(:work_experience AS json), CAST(:education AS json),
                         :exp_years, 'eve', 'eve', 'eve',
                         'completed', :resume_file_path, :resume_text, now(), CAST(:raw_data AS jsonb),
                         CAST(:parsed_resume_json AS jsonb), :parsed_resume_text,
                         now(), now())
                """),
                {
                    "cid": cid,
                    "name": parsed.get("name", ""),
                    "email": parsed.get("email", ""),
                    "phone": parsed.get("phone", ""),
                    "current_role": (
                        parsed.get("current_role")
                        or (parsed.get("headline", "") if _is_actual_job_role(parsed.get("headline")) else "")
                    ),
                    "current_company": parsed.get("current_company", ""),
                    "location": parsed.get("location", ""),
                    "summary": parsed.get("bio") or parsed.get("summary", ""),
                    "skills": skills_json,
                    "work_experience": work_exp_json,
                    "education": edu_json,
                    "exp_years": parsed.get("experience_years"),
                    "resume_file_path": stored_name,
                    "resume_text": resume_text,
                    "raw_data": json.dumps({
                        "certifications": _candidate_certification_sources({"parsed_resume_json": parsed}),
                        "projects": _normalize_projects(parsed.get("projects")),
                    }),
                    "parsed_resume_json": json.dumps(parsed),
                    "parsed_resume_text": resume_text,
                },
            )

        # Upsert internal_candidate_resumes
        r = await db.execute(
            text("SELECT id FROM internal_candidate_resumes WHERE candidate_id = :cid LIMIT 1"),
            {"cid": cid},
        )
        if r.fetchone():
            await db.execute(
                text("""
                    UPDATE internal_candidate_resumes SET
                        source_filename = :fn, source_path = :sp,
                        resume_fingerprint = :fp, updated_at = now()
                    WHERE candidate_id = :cid
                """),
                {"fn": original_filename, "sp": stored_name, "fp": fingerprint, "cid": cid},
            )
        else:
            await db.execute(
                text("""
                    INSERT INTO internal_candidate_resumes
                        (id, candidate_id, source_filename, source_path, resume_fingerprint, created_at, updated_at)
                    VALUES (:id, :cid, :fn, :sp, :fp, now(), now())
                """),
                {
                    "id": str(uuid.uuid4()), "cid": cid,
                    "fn": original_filename, "sp": stored_name, "fp": fingerprint,
                },
            )

        await db.commit()

    logger.info("[parse-resume] final candidate_id returned=%s", cid)
    return cid


# ---------- Semantic matching helper ----------

async def _trigger_matching(candidate_id: str) -> None:
    """Fire-and-forget: refresh semantic job matches for a candidate."""
    try:
        from candidate_job_matching_service import refresh_candidate_job_matches
        candidate = await _get_candidate_row(candidate_id)
        # candidate_preferences is the authoritative persisted source for
        # career intent. Attach it to the matching profile without changing
        # the retrieval or recommendation persistence flow.
        async with SessionLocal() as db:
            prefs_result = await db.execute(
                text("SELECT * FROM candidate_preferences WHERE candidate_id = :cid LIMIT 1"),
                {"cid": candidate_id},
            )
            prefs_row = prefs_result.mappings().fetchone()
        candidate["_prefs_row"] = dict(prefs_row) if prefs_row else None
        await refresh_candidate_job_matches(candidate_id, candidate, SessionLocal)
    except Exception as e:
        logger.warning("[matching] Failed for candidate %s: %s", candidate_id, e)


def _voice_intake_completed_for_matching(candidate: dict) -> bool:
    """Use the existing persisted Voice Intake status as the matching gate."""
    from candidate_job_matching_service import voice_intake_completed
    return voice_intake_completed(candidate)


# ---------- Routes ----------

@api_router.get("/")
async def root():
    return {"message": "Pontis / Eve API is running"}


@api_router.post("/onboarding/parse-resume")
async def parse_resume(file: UploadFile = File(...), existing_id: Optional[str] = None):
    filename = (file.filename or "").lower()
    if not filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF resumes are supported right now.")

    file_bytes = await file.read()
    resume_text, used_ocr = _extract_pdf_text(file_bytes)

    if len(resume_text.strip()) < 50:
        raise HTTPException(
            status_code=400,
            detail="Resume appears empty or unreadable. Please upload a text-based PDF or a clearer scan.",
        )

    parsed = await _parse_resume_with_llm(resume_text)
    fingerprint = hashlib.sha256(file_bytes).hexdigest()

    # Reject duplicate resume when creating a new candidate (force_new path).
    if not existing_id:
        async with SessionLocal() as _db:
            _dup = await _db.execute(
                text("SELECT candidate_id FROM internal_candidate_resumes WHERE resume_fingerprint = :fp LIMIT 1"),
                {"fp": fingerprint},
            )
            if _dup.fetchone():
                raise HTTPException(status_code=409, detail="Duplicate resume")

    # If an existing_id is provided (e.g. test candidate re-onboarding), update that record.
    # Otherwise force_new=True to create a fresh record for a genuinely new candidate.
    if existing_id:
        cid = await _upsert_candidate(parsed, fingerprint, file_bytes, file.filename or "resume.pdf", resume_text=resume_text, existing_id=existing_id)
    else:
        cid = await _upsert_candidate(parsed, fingerprint, file_bytes, file.filename or "resume.pdf", resume_text=resume_text, force_new=True)

    asyncio.ensure_future(_trigger_matching(cid))
    asyncio.ensure_future(_seed_employment_gaps_after_parse(cid, parsed))

    profile = _normalize_for_frontend({**parsed, "id": cid})
    profile["_meta"] = {"used_ocr": used_ocr}
    profile["candidate_token"] = _issue_candidate_session_token(cid)
    return profile


async def _seed_employment_gaps_after_parse(candidate_id: str, parsed: dict) -> None:
    """Detect career gaps from parsed resume and persist them as unanswered in voice_intake."""
    profile_for_gap = {
        "experience": [
            {
                "title": w.get("title", ""),
                "company": w.get("company", ""),
                "start_date": w.get("start_date", ""),
                "end_date": w.get("end_date", ""),
            }
            for w in (parsed.get("work_experience") or [])
        ]
    }
    gap = _detect_employment_gap(profile_for_gap)
    if not gap:
        return
    try:
        async with SessionLocal() as db:
            row = await db.execute(
                text("SELECT raw_data FROM candidates WHERE id = :cid LIMIT 1"),
                {"cid": candidate_id},
            )
            result = row.fetchone()
        if not result:
            return
        raw_data = _parse_raw_data(result[0])
        voice_intake = _parse_raw_data(raw_data.get("voice_intake"))
        existing_gaps = voice_intake.get("employment_gaps") or []
        already = any(
            _normalize_profile_key(g.get("gap_key")) == _normalize_profile_key(gap["gap_key"])
            for g in existing_gaps
            if isinstance(g, dict)
        )
        if already:
            return
        existing_gaps.append({
            "gap_key": gap["gap_key"],
            "question": gap["question"],
            "gap_days": gap["gap_days"],
            "previous_label": gap["previous_label"],
            "current_label": gap["current_label"],
            "previous_end_label": gap["previous_end_label"],
            "current_start_label": gap["current_start_label"],
            "status": "unanswered",
        })
        voice_intake["employment_gaps"] = existing_gaps
        raw_data["voice_intake"] = voice_intake
        async with SessionLocal() as db:
            await db.execute(
                text("UPDATE candidates SET raw_data = CAST(:rd AS jsonb), updated_at = now() WHERE id = :cid"),
                {"rd": json.dumps(raw_data), "cid": candidate_id},
            )
            await db.commit()
        logger.info("[gap-seed] seeded unanswered gap for candidate %s", candidate_id)
    except Exception as exc:
        logger.warning("[gap-seed] failed for candidate %s: %s", candidate_id, exc)


@api_router.post("/candidate/{candidate_id}/photo")
async def upload_profile_photo(candidate_id: str, file: UploadFile = File(...)):
    existing = await _get_candidate_row(candidate_id)
    # Always derive storage and URLs from the row we found.  This prevents an
    # alias/stale client-side id from writing a photo under a different
    # candidate directory than the profile GET endpoint reads.
    canonical_candidate_id = str(existing.get("id") or existing.get("candidate_id") or candidate_id)
    file_bytes = await file.read()
    ext = _validate_candidate_photo_upload(file.content_type or "", file_bytes)
    photo_version = uuid.uuid4().hex

    dest_dir = _candidate_photo_dir(canonical_candidate_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{photo_version}{ext}"

    raw_data = _parse_raw_data(existing.get("raw_data"))
    old_path = raw_data.get("photo_file_path")
    previous_photo_path = _resolve_candidate_photo_path(canonical_candidate_id, old_path)

    try:
        dest_path.write_bytes(file_bytes)
        raw_data["photo_url"] = _photo_view_url(canonical_candidate_id, photo_version)
        raw_data["photo_file_path"] = str(dest_path)
        raw_data["photo_version"] = photo_version
        # The path is an optimization; /tmp storage can disappear between requests.
        raw_data["photo_content_base64"] = base64.b64encode(file_bytes).decode("ascii")
        async with SessionLocal() as db:
            await db.execute(
                text("UPDATE candidates SET raw_data = CAST(:rd AS jsonb), updated_at = now() WHERE id = :cid"),
                {"rd": json.dumps(raw_data), "cid": canonical_candidate_id},
            )
            await db.commit()
        logger.info("[candidate-photo] candidate=%s persisted_path=%s exists=%s bytes=%d version=%s", canonical_candidate_id, dest_path, dest_path.exists(), len(file_bytes), photo_version)
    except Exception:
        try:
            dest_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    if previous_photo_path and previous_photo_path != dest_path:
        await _delete_candidate_photo(canonical_candidate_id, str(previous_photo_path))

    return {"photo_url": raw_data["photo_url"]}


@api_router.get("/candidate/{candidate_id}/photo/view")
async def view_profile_photo(candidate_id: str):
    existing = await _get_candidate_row(candidate_id)
    canonical_candidate_id = str(existing.get("id") or existing.get("candidate_id") or candidate_id)
    raw_data = _parse_raw_data(existing.get("raw_data"))
    file_path = _resolve_candidate_photo_path(canonical_candidate_id, raw_data.get("photo_file_path"))
    logger.info("[candidate-photo] candidate=%s persisted_path=%s resolved_path=%s exists=%s", canonical_candidate_id, raw_data.get("photo_file_path"), file_path, bool(file_path and file_path.exists()))
    if file_path and not file_path.exists():
        file_path = _restore_candidate_photo(canonical_candidate_id, raw_data)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="No profile photo.")
    suffix = Path(file_path).suffix.lower()
    media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                  "webp": "image/webp", "gif": "image/gif"}.get(suffix.lstrip("."), "image/jpeg")
    return FileResponse(
        file_path,
        media_type=media_type,
        headers={"Cache-Control": "no-store, max-age=0, must-revalidate"},
    )


@api_router.delete("/candidate/{candidate_id}/photo")
async def delete_profile_photo(candidate_id: str):
    existing = await _get_candidate_row(candidate_id)
    raw_data = _parse_raw_data(existing.get("raw_data"))
    photo_path = raw_data.get("photo_file_path")

    raw_data.pop("photo_url", None)
    raw_data.pop("photo_file_path", None)
    raw_data.pop("photo_version", None)
    raw_data.pop("photo_content_base64", None)

    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE candidates SET raw_data = CAST(:rd AS jsonb), updated_at = now() WHERE id = :cid"),
            {"rd": json.dumps(raw_data), "cid": candidate_id},
        )
        await db.commit()

    await _delete_candidate_photo(candidate_id, photo_path)
    return {"photo_url": None, "status": "deleted"}


@api_router.delete("/candidate/{candidate_id}/account")
async def delete_candidate_account(
    candidate_id: str,
    authorization: Optional[str] = Header(default=None),
):
    _verify_candidate_session_token(_get_bearer_token(authorization), candidate_id)
    candidate = await _get_candidate_row(candidate_id)
    candidate_dir = _candidate_storage_dir(candidate_id)
    raw_data = _parse_raw_data(candidate.get("raw_data"))

    file_paths: set[str] = set()
    photo_path = raw_data.get("photo_file_path")
    if isinstance(photo_path, str) and photo_path.strip():
        file_paths.add(photo_path.strip())

    delete_tables = [
        "candidate_chat_sessions",
        "eve_outbound_events",
        "recruiter_interest_requests",
        "candidate_activity_feed",
        "candidate_job_recommendations",
        "candidate_voice_intakes",
        "candidate_voice_sessions",
        "candidate_certificates",
        "candidate_preferences",
        "candidate_feedback",
        "candidate_lifecycle_events",
        "email_communications",
        "inbound_email_replies",
        "internal_candidate_resumes",
        "interview_evaluations",
        "interview_sessions",
        "interviews",
        "ats_exports",
        "automation_jobs",
        "booking_links",
        "linkedin_attachments",
        "linkedin_connections",
        "linkedin_conversations",
        "linkedin_jobs",
        "linkedin_messages",
        "notification_events",
        "notification_workflow_tokens",
        "outreach_events",
        "ranking_explanations",
        "recruiter_notes",
        "recruiter_tasks",
    ]

    async with SessionLocal() as db:
        cert_rows = await db.execute(
            text("SELECT file_path FROM candidate_certificates WHERE candidate_id = :cid"),
            {"cid": candidate_id},
        )
        file_paths.update(str(row[0]).strip() for row in cert_rows.fetchall() if row[0])

        resume_rows = await db.execute(
            text("SELECT source_path FROM internal_candidate_resumes WHERE candidate_id = :cid"),
            {"cid": candidate_id},
        )
        file_paths.update(str(row[0]).strip() for row in resume_rows.fetchall() if row[0])

        for table in delete_tables:
            await db.execute(text(f"DELETE FROM {table} WHERE candidate_id = :cid"), {"cid": candidate_id})

        await db.execute(text("DELETE FROM candidates WHERE id = :cid"), {"cid": candidate_id})
        await db.commit()

    for path_str in sorted(file_paths):
        try:
            resolved = Path(path_str).resolve()
            if candidate_dir in resolved.parents or resolved == candidate_dir:
                resolved.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("[account-delete] could not delete file %s: %s", path_str, exc)

    try:
        if candidate_dir.exists():
            shutil.rmtree(candidate_dir)
    except Exception as exc:
        logger.warning("[account-delete] could not remove storage dir %s: %s", candidate_dir, exc)

    return {"status": "deleted", "candidate_id": candidate_id}


@api_router.post("/candidate/{candidate_id}/help")
async def candidate_help(
    candidate_id: str,
    body: CandidateHelpRequest,
    authorization: Optional[str] = Header(default=None),
):
    _verify_candidate_session_token(_get_bearer_token(authorization), candidate_id)
    candidate = await _get_candidate_row(candidate_id)

    subject = body.subject.strip()
    message = body.message.strip()
    if len(subject) < 3:
        raise HTTPException(status_code=400, detail="Subject is required.")
    if len(message) < 5:
        raise HTTPException(status_code=400, detail="Message is required.")
    if len(subject) > 180:
        raise HTTPException(status_code=400, detail="Subject is too long.")
    if len(message) > 5000:
        raise HTTPException(status_code=400, detail="Message is too long.")

    await _send_support_email(
        subject=subject,
        message=message,
        candidate_email=str(candidate.get("email") or ""),
    )
    return {"status": "sent"}


@api_router.get("/candidate/{candidate_id}/chat")
async def get_candidate_chat(candidate_id: str):
    """Return the persisted short-term chat window for a candidate."""
    await _get_candidate_row(candidate_id)
    messages = await _load_chat_window(candidate_id)
    return {"messages": messages}


@api_router.get("/candidate/{candidate_id}/profile")
async def get_candidate_profile(candidate_id: str):
    return await _get_candidate_profile_payload(candidate_id)




@api_router.get("/candidate/{candidate_id}/profile/strength")
async def get_candidate_profile_strength(candidate_id: str):
    """
    Return the full structured Profile Strength and Recommendation Readiness
    for a candidate (Phase 9 output).
    """
    from profile_strength_service import calculate_profile_strength_v2
    candidate = await _get_candidate_row(candidate_id)
    candidate["candidate_certificates"] = await _load_candidate_certificates(candidate_id)
    raw_data = _parse_raw_data(candidate.get("raw_data"))

    # Load canonical preferences row
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT * FROM candidate_preferences WHERE candidate_id = :cid LIMIT 1"),
            {"cid": candidate_id},
        )
        prefs_row_result = row.mappings().fetchone()
    prefs_row = dict(prefs_row_result) if prefs_row_result else None

    result = calculate_profile_strength_v2(candidate, raw_data, prefs_row)
    result = _apply_profile_strength_test_override(candidate, result)
    return result

@api_router.get("/candidate/{candidate_id}/profile/download")
async def download_candidate_profile(candidate_id: str):
    profile = await _get_candidate_profile_payload(candidate_id)
    candidate_row = await _get_candidate_row(candidate_id)
    profile["raw_data"] = _parse_raw_data(candidate_row.get("raw_data"))
    candidate_name = _pdf_safe_text(profile.get("name")) or f"candidate_{candidate_id}"
    filename = _pdf_filename(candidate_name, candidate_id)
    pdf_bytes = _build_candidate_profile_pdf(profile)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.get("/candidate/{candidate_id}/documents")
async def get_candidate_documents(candidate_id: str, authorization: Optional[str] = Header(default=None)):
    _verify_candidate_session_token(_get_bearer_token(authorization), candidate_id)
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        resume_row = await db.execute(
            text("SELECT source_filename, resume_fingerprint FROM internal_candidate_resumes WHERE candidate_id = :cid ORDER BY created_at DESC LIMIT 1"),
            {"cid": candidate_id},
        )
        resume = resume_row.fetchone()
        certs_rows = await db.execute(
            text("SELECT id, file_name FROM candidate_certificates WHERE candidate_id = :cid ORDER BY created_at ASC"),
            {"cid": candidate_id},
        )
        certs = certs_rows.fetchall()
        application_rows = await db.execute(
            text("SELECT id, file_name, company_name, recommendation_id FROM candidate_application_resumes WHERE candidate_id = :cid ORDER BY created_at DESC"),
            {"cid": candidate_id},
        )
        application_resumes = application_rows.fetchall()
    return {
        "resume": {"filename": resume[0], "fingerprint": resume[1]} if resume else None,
        "certificates": [{"id": str(r[0]), "filename": r[1]} for r in certs],
        "application_resumes": [{"id": str(r[0]), "filename": r[1], "company": r[2], "recommendation_id": str(r[3])} for r in application_resumes],
    }


@api_router.get("/candidate/{candidate_id}/resume/view")
@api_router.post("/candidate/{candidate_id}/resume/view")
async def view_resume(candidate_id: str, download: bool = False, authorization: Optional[str] = Header(default=None), candidate_token: Optional[str] = Form(default=None)):
    _verify_document_view_session(candidate_id, authorization, candidate_token)
    candidate = await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT source_filename, source_path FROM internal_candidate_resumes WHERE candidate_id = :cid ORDER BY created_at DESC LIMIT 1"),
            {"cid": candidate_id},
        )
        result = row.fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="No resume found.")
    filename = result[0]
    persisted_path = candidate.get("resume_file_path")
    file_path = _resolve_candidate_document_path(candidate_id, "resume", result[1])
    if not file_path or not file_path.exists():
        file_path = _resolve_candidate_document_path(candidate_id, "resume", persisted_path)
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Resume file is referenced in your profile but is unavailable in document storage.")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        str(file_path), media_type=_document_media_type(file_path), filename=filename,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@api_router.get("/candidate/{candidate_id}/application-resumes/{resume_id}/view")
@api_router.post("/candidate/{candidate_id}/application-resumes/{resume_id}/view")
async def view_application_resume(candidate_id: str, resume_id: str, download: bool = False, authorization: Optional[str] = Header(default=None), candidate_token: Optional[str] = Form(default=None)):
    _verify_document_view_session(candidate_id, authorization, candidate_token)
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(text("SELECT file_name, file_path, recommendation_id, company_name FROM candidate_application_resumes WHERE id = :id AND candidate_id = :cid LIMIT 1"), {"id": resume_id, "cid": candidate_id})
        result = row.fetchone()
    if not result:
        logger.info("[application-resume-view] candidate_id=%s application_resume_id=%s db_record_exists=false", candidate_id, resume_id)
        raise HTTPException(status_code=404, detail="Application resume not found.")
    filename, storage_key, recommendation_id = result[0], result[1], str(result[2])
    company_name = str(result[3] or "") if len(result) > 3 else ""
    diagnostics: dict[str, Any] = {}
    # Always resolve the DB's canonical relative key beneath the candidate's
    # persistent application-resume volume.  The recovery branch only supports
    # deterministic legacy names; it never follows an old host/browser path.
    path = _resolve_application_resume_path(
        candidate_id, storage_key, recommendation_id=recommendation_id,
        resume_id=resume_id, filename=filename, recover_legacy=True, diagnostics=diagnostics,
    )
    exists = bool(path and path.exists())
    size = path.stat().st_size if exists and path.is_file() else None
    logger.info(
        "[application-resume-view] candidate_id=%s application_resume_id=%s db_record_exists=true "
        "db_file_path=%r db_filename=%r recommendation_id=%s company_name=%r "
        "canonical_relative_path=%r persistent_root=%s final_absolute_path=%s exists=%s bytes=%s legacy_fallbacks=%s selected_path=%s",
        candidate_id, resume_id, storage_key, filename, recommendation_id, company_name,
        diagnostics.get("canonical_relative_path"), diagnostics.get("persistent_root"), path,
        exists, size, diagnostics.get("legacy_fallbacks", []), diagnostics.get("selected_path"),
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Application resume file is not available.")
    disposition = "attachment" if download else "inline"
    return FileResponse(str(path), media_type="application/pdf", filename=filename,
                        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'})


@api_router.delete("/candidate/{candidate_id}/resume")
async def delete_resume(candidate_id: str, authorization: Optional[str] = Header(default=None)):
    _verify_candidate_session_token(_get_bearer_token(authorization), candidate_id)
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT id, source_path FROM internal_candidate_resumes WHERE candidate_id = :cid ORDER BY created_at DESC LIMIT 1"),
            {"cid": candidate_id},
        )
        result = row.fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="No resume found.")
    resume_id, source_path = result[0], result[1]
    async with SessionLocal() as db:
        await db.execute(
            text("DELETE FROM internal_candidate_resumes WHERE id = :rid AND candidate_id = :cid"),
            {"rid": resume_id, "cid": candidate_id},
        )
        await db.commit()
    stored_file_path = _resolve_candidate_document_path(candidate_id, "resume", source_path)
    if stored_file_path:
        try:
            stored_file_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Could not delete resume file %s: %s", source_path, e)
    return {"status": "deleted"}


@api_router.post("/candidate/{candidate_id}/resume/verify")
async def verify_resume_identity(candidate_id: str, file: UploadFile = File(...)):
    """Parse a resume PDF and return the extracted name and email for identity pre-check."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF resumes are supported.")
    file_bytes = await file.read()
    resume_text, _ = _extract_pdf_text(file_bytes)
    if len(resume_text.strip()) < 50:
        raise HTTPException(status_code=400, detail="Resume appears empty or unreadable.")
    parsed = await _parse_resume_with_llm(resume_text)
    return {"name": parsed.get("name") or "", "email": parsed.get("email") or ""}


@api_router.post("/candidate/{candidate_id}/resume/replace")
async def replace_resume(candidate_id: str, file: UploadFile = File(...), authorization: Optional[str] = Header(default=None)):
    _verify_candidate_session_token(_get_bearer_token(authorization), candidate_id)
    await _get_candidate_row(candidate_id)
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF resumes are supported.")

    file_bytes = await file.read()
    resume_text, _ = _extract_pdf_text(file_bytes)

    if len(resume_text.strip()) < 50:
        raise HTTPException(status_code=400, detail="Resume appears empty or unreadable.")

    parsed = await _parse_resume_with_llm(resume_text)
    fingerprint = hashlib.sha256(file_bytes).hexdigest()

    await _upsert_candidate(parsed, fingerprint, file_bytes, file.filename or "resume.pdf", resume_text=resume_text, existing_id=candidate_id)

    asyncio.ensure_future(_trigger_matching(candidate_id))

    profile_row = {**parsed, "id": candidate_id}
    profile_row["candidate_certificates"] = await _load_candidate_certificates(candidate_id)
    profile = _normalize_for_frontend(profile_row)
    return {"status": "replaced", "filename": file.filename, "profile": profile}


@api_router.post("/candidate/{candidate_id}/certificates/upload")
async def upload_certificate(candidate_id: str, file: UploadFile = File(...), authorization: Optional[str] = Header(default=None)):
    _verify_candidate_session_token(_get_bearer_token(authorization), candidate_id)
    await _get_candidate_row(candidate_id)
    allowed = (".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg")
    if not any((file.filename or "").lower().endswith(ext) for ext in allowed):
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    file_bytes = await file.read()
    dest_dir = DOCS_DIR / candidate_id / "certificates"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "cert").suffix
    dest_path = dest_dir / f"{uuid.uuid4()}{ext}"
    dest_path.write_bytes(file_bytes)

    cert_id = str(uuid.uuid4())
    async with SessionLocal() as db:
        await db.execute(
            text("INSERT INTO candidate_certificates (id, candidate_id, file_name, file_path, created_at) VALUES (:id, :cid, :fn, :fp, now())"),
        {"id": cert_id, "cid": candidate_id, "fn": file.filename, "fp": dest_path.name},
        )
        await db.commit()
    return {"id": cert_id, "filename": file.filename}


@api_router.get("/candidate/{candidate_id}/certificates/{cert_id}/view")
@api_router.post("/candidate/{candidate_id}/certificates/{cert_id}/view")
async def view_certificate(candidate_id: str, cert_id: str, download: bool = False, authorization: Optional[str] = Header(default=None), candidate_token: Optional[str] = Form(default=None)):
    _verify_document_view_session(candidate_id, authorization, candidate_token)
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT file_name, file_path FROM candidate_certificates WHERE id = :cid AND candidate_id = :owner LIMIT 1"),
            {"cid": cert_id, "owner": candidate_id},
        )
        result = row.fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Certificate not found.")
    filename, file_path = result[0], result[1]
    path = _resolve_candidate_document_path(candidate_id, "certificates", file_path)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Certificate file not available.")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        str(path), media_type=_document_media_type(path), filename=filename,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@api_router.delete("/candidate/{candidate_id}/certificates/{cert_id}")
async def delete_certificate(candidate_id: str, cert_id: str, authorization: Optional[str] = Header(default=None)):
    _verify_candidate_session_token(_get_bearer_token(authorization), candidate_id)
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT file_path FROM candidate_certificates WHERE id = :cid AND candidate_id = :owner LIMIT 1"),
            {"cid": cert_id, "owner": candidate_id},
        )
        result = row.fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Certificate not found.")
    file_path = result[0]
    async with SessionLocal() as db:
        await db.execute(
            text("DELETE FROM candidate_certificates WHERE id = :cid AND candidate_id = :owner"),
            {"cid": cert_id, "owner": candidate_id},
        )
        await db.commit()
    stored_file_path = _resolve_candidate_document_path(candidate_id, "certificates", file_path)
    if stored_file_path:
        try:
            stored_file_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Could not delete certificate file %s: %s", file_path, e)
    return {"status": "deleted"}


@api_router.post("/candidate/{candidate_id}/certificates/{cert_id}/replace")
async def replace_certificate(candidate_id: str, cert_id: str, file: UploadFile = File(...), authorization: Optional[str] = Header(default=None)):
    _verify_candidate_session_token(_get_bearer_token(authorization), candidate_id)
    await _get_candidate_row(candidate_id)
    allowed = (".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg")
    if not any((file.filename or "").lower().endswith(ext) for ext in allowed):
        raise HTTPException(status_code=400, detail="Unsupported file type.")

    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT file_path FROM candidate_certificates WHERE id = :cid AND candidate_id = :owner LIMIT 1"),
            {"cid": cert_id, "owner": candidate_id},
        )
        result = row.fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Certificate not found.")

    old_path = _resolve_candidate_document_path(candidate_id, "certificates", result[0])
    if old_path and old_path.exists():
        old_path.unlink(missing_ok=True)

    file_bytes = await file.read()
    dest_dir = DOCS_DIR / candidate_id / "certificates"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "cert").suffix
    dest_path = dest_dir / f"{uuid.uuid4()}{ext}"
    dest_path.write_bytes(file_bytes)

    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE candidate_certificates SET file_name=:fn, file_path=:fp WHERE id=:cid AND candidate_id=:owner"),
            {"fn": file.filename, "fp": dest_path.name, "cid": cert_id, "owner": candidate_id},
        )
        await db.commit()
    return {"id": cert_id, "filename": file.filename}


# ---------- Chat session persistence ----------

CREATE_CHAT_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS candidate_chat_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id    UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    session_id      TEXT NOT NULL,
    messages        JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
CREATE_CHAT_SESSIONS_IDX = """
CREATE INDEX IF NOT EXISTS idx_ccs_candidate ON candidate_chat_sessions(candidate_id)
"""

CHAT_WINDOW_SIZE = 20   # messages kept in DB per candidate
CHAT_PRUNE_KEEP  = 10   # messages retained after pruning


async def _ensure_chat_sessions_table():
    async with SessionLocal() as db:
        await db.execute(text(CREATE_CHAT_SESSIONS_TABLE))
        await db.execute(text(CREATE_CHAT_SESSIONS_IDX))
        await db.commit()


async def _load_chat_window(candidate_id: str) -> list[dict]:
    """Return the persisted short-term message window for a candidate."""
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT messages FROM candidate_chat_sessions WHERE candidate_id = :cid ORDER BY updated_at DESC LIMIT 1"),
            {"cid": candidate_id},
        )
        result = row.fetchone()
    if not result:
        return []
    msgs = result[0]
    if isinstance(msgs, str):
        try:
            msgs = json.loads(msgs)
        except Exception:
            msgs = []
    return msgs if isinstance(msgs, list) else []


async def _save_chat_window(candidate_id: str, session_id: str, messages: list[dict]) -> None:
    """Persist the message window; prune old messages after extracting long-term facts."""
    if len(messages) > CHAT_WINDOW_SIZE:
        overflow = messages[: len(messages) - CHAT_PRUNE_KEEP]
        messages = messages[len(messages) - CHAT_PRUNE_KEEP :]
        asyncio.ensure_future(_extract_and_merge_chat_facts(candidate_id, overflow))

    async with SessionLocal() as db:
        existing = await db.execute(
            text("SELECT id FROM candidate_chat_sessions WHERE candidate_id = :cid LIMIT 1"),
            {"cid": candidate_id},
        )
        row = existing.fetchone()
        if row:
            await db.execute(
                text("UPDATE candidate_chat_sessions SET messages = CAST(:msgs AS jsonb), session_id = :sid, updated_at = now() WHERE candidate_id = :cid"),
                {"msgs": json.dumps(messages), "sid": session_id, "cid": candidate_id},
            )
        else:
            await db.execute(
                text("INSERT INTO candidate_chat_sessions (id, candidate_id, session_id, messages) VALUES (gen_random_uuid(), :cid, :sid, CAST(:msgs AS jsonb))"),
                {"cid": candidate_id, "sid": session_id, "msgs": json.dumps(messages)},
            )
        await db.commit()


CHAT_FACTS_EXTRACT_SYSTEM = """You are a recruitment data extractor. Given a conversation excerpt between a candidate and an AI recruiter, extract any meaningful career facts the candidate revealed.
Return ONLY valid JSON with these keys (omit keys where no information was found):
{
  "preferred_roles": [],
  "career_goals": "",
  "target_industries": [],
  "location_preferences": "",
  "salary_expectation": "",
  "availability": "",
  "notice_period": "",
  "work_type_preference": "",
  "additional_information": ""
}
Do NOT invent or hallucinate. Only include fields explicitly mentioned by the candidate."""


async def _extract_and_merge_chat_facts(candidate_id: str, messages: list[dict]) -> None:
    """Extract long-term facts from pruned messages and merge into candidate raw_data."""
    if not messages:
        return
    try:
        convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
        resp = await openai_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": CHAT_FACTS_EXTRACT_SYSTEM},
                {"role": "user", "content": convo[:6000]},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        facts = json.loads(resp.choices[0].message.content or "{}")
        if not facts:
            return

        async with SessionLocal() as db:
            row = await db.execute(
                text("SELECT raw_data FROM candidates WHERE id = :cid LIMIT 1"),
                {"cid": candidate_id},
            )
            result = row.fetchone()
        if not result:
            return
        existing_raw = result[0] or {}
        if isinstance(existing_raw, str):
            try:
                existing_raw = json.loads(existing_raw)
            except Exception:
                existing_raw = {}

        raw = dict(existing_raw)
        # Merge: never overwrite non-empty values with empty ones
        for key, val in facts.items():
            if not val:
                continue
            if isinstance(val, list):
                existing_list = raw.get(key) or []
                seen = {str(x).lower() for x in existing_list}
                for item in val:
                    if str(item).lower() not in seen:
                        existing_list.append(item)
                        seen.add(str(item).lower())
                raw[key] = existing_list
            else:
                if not raw.get(key):
                    raw[key] = val

        async with SessionLocal() as db:
            await db.execute(
                text("UPDATE candidates SET raw_data = CAST(:rd AS jsonb), updated_at = now() WHERE id = :cid"),
                {"rd": json.dumps(raw), "cid": candidate_id},
            )
            await db.commit()
        logger.info("[chat-facts] merged facts for candidate %s", candidate_id)
    except Exception as e:
        logger.warning("[chat-facts] extraction failed for %s: %s", candidate_id, e)


# ---------- Chat with candidate context + structured profile updates ----------

EVE_SYSTEM_TEMPLATE = """You are Eve, the candidate-side AI recruitment agent on the Pontis platform.
You help candidates refine their profile, discover matching roles, and prep for outreach.

STYLE: Warm, concise, action-oriented. 2-3 short sentences per reply unless depth is requested.
Speak as a trusted career partner. No emojis. No markdown headers.

CANDIDATE PROFILE (current state):
{profile_context}

MISSING FIELDS (ask about these — do NOT ask for fields already listed above): {missing_fields}

PROFILE COMPLETION GUIDANCE: {profile_completion_guidance}

BEHAVIOR:
- ALWAYS answer the candidate's current message FIRST and DIRECTLY, using the candidate profile above. Do not redirect to job search or any other topic unless the candidate's message explicitly asks for it.
- PROFILE IMPROVEMENT QUESTIONS: When you receive a message starting with [PROFILE_QUESTION], it is an internal instruction — do NOT treat it as a candidate statement. Instead, ask the candidate that exact question naturally and conversationally, then wait for their answer. Do not acknowledge the instruction format.
- PROFILE COMPLETION: If PROFILE COMPLETION GUIDANCE says the profile is below 75% and lists a next question, and the candidate's current message is NOT a direct question about something else, proactively ask that ONE question at the end of your reply. Do NOT ask it if the candidate's message already answers it. Stop asking profile questions once the guidance says the profile is at 75%+.
- PREFERENCE COMPLETION: When PROFILE COMPLETION GUIDANCE asks about a work preference, ask that exact question naturally. Never expose field keys, internal guidance, or a profile score/percentage to the candidate.
- If the candidate asks whether you have their resume, details, or profile — answer YES or NO based on the profile above, and summarise what you have. Never say you are loading jobs in response to such questions.
- If the candidate asks what information you still need — list only the MISSING FIELDS from the profile above. Do not mention jobs.
- Job search, job matching, and job recommendations must ONLY be triggered when the candidate explicitly asks for jobs, roles, or matches (e.g. "find me jobs", "show me matches", "what roles suit me"). Never volunteer job search in response to profile/resume/details questions.
- Use the candidate profile above to personalise every response. Address the candidate by their actual name when known.
- NEVER ask for information that is already present in the candidate profile above (name, email, phone, resume, skills, experience, etc.).
- When "Resume status: Available" appears in the profile above, you MUST NOT say you don't have the resume, MUST NOT say you can't see the resume, and MUST NOT ask the candidate to upload or share their resume. Treat all parsed resume data (role, skills, experience, education) as fully known.
- Only ask the candidate to upload a resume when "Resume status: Not available" appears in the profile above.
- If a VOICE INTAKE RESUME section is present with status in_progress, treat the conversation as a continuation of the interrupted intake. Briefly explain that you were in the middle of the intake, use the saved completed turns and candidate profile to avoid repeating anything already known, and ask exactly one next unanswered question.
- Never ask about information that is already present in the VOICE INTAKE RESUME section, the profile above, or the saved chat memory.
- Once the VOICE INTAKE RESUME section is absent or marked completed, return to normal career-assistant behavior.
- If important profile fields are missing, ask ONE focused question to fill the most critical gap.
- When the candidate provides new professional information, extract it and include a "profile_updates" JSON block at the END of your reply in this exact format:
  <<<PROFILE_UPDATES>>>
  {{"profile_updates": {{"field": value}}}}
  <<<END_UPDATES>>>
- Only include profile_updates when the candidate actually provides new information.
- Do not guess a destination for a bare request such as "Add Python". Ask where it belongs before emitting an update.
- Before changing or deleting an existing value, use the supplied profile context to identify every matching record. If more than one record/section matches, ask the candidate to choose; never emit an update for an ambiguous target.
- Preserve every explicitly stated Education or Work Experience fact in its proper record. Ask for missing dates/details, and merge those later; do not discard an otherwise meaningful degree/institution or title/company statement.
- Do NOT change open_to_opportunities unless the candidate explicitly asks.
- Do NOT overwrite fields that already have good data unless the candidate is correcting them.
- DELETION: When the candidate asks to remove/delete a specific item from their profile (e.g. "remove FastAPI from my skills", "delete my AWS cert", "I no longer want Hyderabad as my preferred location"), include a "profile_deletions" key inside profile_updates with the field and item to remove:
  <<<PROFILE_UPDATES>>>
  {{"profile_updates": {{"profile_deletions": {{"skills": ["FastAPI"]}}}}}}
  <<<END_UPDATES>>>
  Supported deletion fields: skills, certifications, preferred_roles, work_experience, education, projects, preferred_locations, additional_information.
  When the candidate explicitly says an item is "from Additional Information", use
  ONLY `additional_information` as the deletion target. Never delete a
  certification merely because the requested phrase contains "certificate".
  If the item does not exist in the profile, say so — do NOT add it.
  If the request is ambiguous (multiple items could match), ask a clarification question instead of deleting.

JOB RECOMMENDATIONS — STRICT RULES:
- NEVER invent, fabricate, or hallucinate job titles, company names, salaries, benefits, job descriptions, or hiring status.
- When the candidate asks for jobs/matches, the real database results will appear under "REAL JOB MATCHES FROM DATABASE" in the profile context above. Present ONLY those results — title, company, location, salary, match score, and a brief description summary.
- If "REAL JOB MATCHES FROM DATABASE" is present in the context, you MUST present those jobs directly. Do NOT say you are pulling jobs, loading jobs, or that results will appear elsewhere.
- If no "REAL JOB MATCHES FROM DATABASE" section is present and the candidate asks for jobs, say no matches were found right now and suggest they check back after their profile is more complete.
- You may summarise or explain job data that has been returned to you, but you must not add details that were not in the source data.
- When a candidate expresses interest in a specific job, tell them to click the Apply Now button on the job card to complete their application on the company's website. Never say you have submitted or will submit their application.

APPLICATION WORKFLOW — STRICT RULES:
- NEVER say or imply that an application has been submitted. Eve does NOT submit applications.
- When a candidate expresses interest in a job, respond with something like: "I haven't submitted your application. Click Apply Now to complete it on the company's website." Then direct them to use the Apply Now button shown in the job card.
- Do NOT ask "Shall I submit your application?" — Eve cannot submit applications.
- The Apply Now button opens the company's actual careers/application page in a new tab. The candidate completes the application there.

FIELD DEFINITIONS — use exactly these keys:
  current_role   : The candidate's job TITLE (e.g. "Python Backend Developer", "Data Analyst").
                   NEVER put a technology, tool, or database name here (e.g. PostgreSQL, FastAPI, Python are NOT job titles).
  experience_years: Total years of professional experience as a number.
  skills         : List of technology/tool strings (e.g. ["FastAPI", "PostgreSQL", "Python"]).
  preferred_roles: List of job titles the candidate explicitly says they want.
  preferred_locations, preferred_industries, employment_types: Lists of explicitly stated preferences.
  remote_preference: "Remote", "Hybrid", "On-site", or "Flexible" only when stated.
  expected_salary: Plain salary expectation string; use this key, never salary_expectation.
  willing_to_relocate, open_to_opportunities: Boolean only when explicitly stated.
  availability / notice_period: Plain string describing when the candidate can start or their notice period.
  work_experience: List of job objects. Each object must have:
                     {{"title": "<job title>", "company": "<company name>", "description": "<responsibilities>"}}
                   title   = the role/position held (e.g. "Python Backend Developer")
                   company = the employer name (e.g. "ABC Technologies")
                   description = what they did (technologies used, responsibilities)
                   Do NOT put a technology name as title. Do NOT put a company name as title.
  name, email, phone, location, bio: plain string fields.
  education      : List of {{"degree": "", "institution": "", "start_date": "", "end_date": ""}}. A degree completed/studied at a university is Education, never Certification; retain the stated degree and institution even when dates are absent.
  certifications : List of certification names only. Use this only for an explicitly stated certificate, certification, credential, licence, or certification exam -- never infer it from a university, college, degree, Master's, Bachelor's, MBA, or PhD.

EXAMPLE — if candidate says "I worked at ABC Technologies as a Python Backend Developer. I built REST APIs with FastAPI and PostgreSQL.":
  <<<PROFILE_UPDATES>>>
  {{"profile_updates": {{
    "current_role": "Python Backend Developer",
    "work_experience": [{{"title": "Python Backend Developer", "company": "ABC Technologies", "description": "Built REST APIs using FastAPI and PostgreSQL."}}],
    "skills": ["Python", "FastAPI", "PostgreSQL", "REST APIs"],
    "certifications": ["AWS Certified Solutions Architect - Associate"]
  }}}}
  <<<END_UPDATES>>>

VALIDATION RULES:
  - current_role must be a human job title, never a technology or database name.
  - If the candidate mentions a company AND a role, always populate work_experience.
  - Extract skills/technologies into the skills list, not into current_role."""


def _build_profile_context(profile: dict) -> tuple[str, list[str]]:
    lines = []
    missing = []

    def add(label, val, *, required=False):
        if val and (not isinstance(val, list) or len(val) > 0):
            lines.append(f"- {label}: {val}")
        elif required:
            missing.append(label)

    # Core identity — always collected during signup/resume; never ask again
    add("Name", profile.get("name"), required=True)
    add("Email", profile.get("email"), required=False)   # known from LinkedIn auth
    add("Phone", profile.get("phone"), required=False)
    add("Headline / Current Role", profile.get("headline") or profile.get("current_role"), required=True)
    add("Location", profile.get("location"), required=True)
    add("Bio/Summary", profile.get("bio") or profile.get("summary"), required=True)
    add("Experience Years", profile.get("experience_years"), required=True)
    add("Skills", profile.get("keySkills") or profile.get("skills"), required=True)
    add("Work Experience", profile.get("experience") or profile.get("work_experience"), required=True)
    add("Education", profile.get("education"), required=False)

    # Resume availability — derived from parsing_status set during resume upload
    resume_available = profile.get("parsing_status") == "completed"
    lines.append(f"- Resume status: {'Available' if resume_available else 'Not available'}")

    # Voice / chat-derived enrichment fields
    raw_data = profile.get("raw_data") or {}
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except Exception:
            raw_data = {}

    add("Preferred Roles", profile.get("preferred_roles") or raw_data.get("preferred_roles"), required=True)
    add("Availability", profile.get("availability") or raw_data.get("availability"), required=True)
    add("Notice Period", raw_data.get("notice_period"), required=False)
    add("Certifications", raw_data.get("certifications"), required=False)
    add("Salary Expectation", raw_data.get("salary_expectation"), required=False)
    add("Work Type Preference", raw_data.get("work_type_preference"), required=False)
    add("Career Goals", raw_data.get("career_goals"), required=False)
    add("Location Preferences", raw_data.get("location_preferences"), required=False)
    add("Target Industries", raw_data.get("target_industries"), required=False)

    gap = _detect_employment_gap(profile)
    if gap:
        lines.append(f"- Employment gap to clarify: {gap['question']}")
        missing.append("Employment gap explanation")
    else:
        explained_gap = _employment_gap_answered(profile)
        if explained_gap:
            lines.append(f"- Employment gap explained: {explained_gap['answer']}")

    return "\n".join(lines) if lines else "No profile data yet.", missing


# These are deliberately candidate-facing descriptions/questions.  The storage
# keys stay at the boundary between the chat workflow and persistence layer.
_CANONICAL_PREFERENCE_LABELS = {
    "preferred_roles": "roles they are targeting",
    "preferred_locations": "preferred work locations",
    "remote_preference": "remote, hybrid, or on-site preference",
    "notice_period": "availability to start",
    "expected_salary": "salary expectation",
    "employment_types": "preferred employment type",
    "preferred_industries": "preferred industries",
    "willing_to_relocate": "relocation preference",
}

_CANONICAL_PREFERENCE_QUESTIONS = {
    "preferred_roles": "What kinds of roles are you looking for?",
    "preferred_locations": "Which locations would you prefer to work in?",
    "remote_preference": "Do you prefer remote, hybrid, on-site, or flexible work?",
    "notice_period": "What is your notice period or when could you start?",
    "expected_salary": "What salary range are you targeting?",
    "employment_types": "Are you looking for full-time, part-time, contract, or freelance work?",
    "preferred_industries": "Are there any industries you would especially like to work in?",
    "willing_to_relocate": "Would you be open to relocating for the right role?",
}


def _missing_canonical_preference_fields(candidate: dict, prefs_row: Optional[dict] = None) -> list[str]:
    """Return only canonical preference keys that have no saved candidate value."""
    from profile_strength_service import get_canonical_preferences

    preferences = get_canonical_preferences(candidate, prefs_row)
    return [
        field for field in _CANONICAL_PREFERENCE_LABELS
        if preferences.get(field) in (None, "", [])
    ]


def _build_profile_completion_guidance(profile: dict) -> str:
    """
    Return a short guidance string for Eve's system prompt.
    When profile strength < 75%, returns the single most important next question.
    When >= 75%, returns a note to stop asking profile questions.
    """
    from profile_strength_service import calculate_profile_strength_v2
    raw_data = profile.get("raw_data") or {}
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except Exception:
            raw_data = {}
    try:
        result = calculate_profile_strength_v2(profile, raw_data, profile.get("_prefs_row"))
        percent = result.get("percent", 0)
        if percent >= 75:
            return f"Profile is at {percent}% (75%+ reached). Do NOT ask any more profile-completion questions."
        # Preference completion is based on the canonical reader, rather than
        # whichever legacy alias happened to be present in raw_data. Preserve
        # the established 75% completion boundary for all chat behavior.
        missing_preferences = _missing_canonical_preference_fields(
            profile, profile.get("_prefs_row")
        )
        if missing_preferences:
            question = _CANONICAL_PREFERENCE_QUESTIONS[missing_preferences[0]]
            return f'Ask this one work-preference question naturally: "{question}"'
        next_actions = result.get("recommended_next_actions") or []
        if next_actions:
            return (
                f"Profile is at {percent}% (below 75%). "
                f"Ask this ONE question to help complete the profile: \"{next_actions[0]}\""
            )
        return f"Profile is at {percent}% (below 75%). Ask about any missing fields listed above."
    except Exception:
        return "No profile completion guidance available."


# Conversational prefixes that must never appear as list items in structured fields.
_CONVERSATIONAL_LIST_PREFIXES = re.compile(
    r"^(?:"
    # "These are [my] skills", "Here are the skills", "My skills", "The skills", etc.
    r"(?:these\s+are|here\s+are)\s+(?:my\s+|the\s+|our\s+)?(?:skills?|certifications?|certificates?|preferred\s+roles?|roles?|technologies?|tools?|projects?|education|experience)\b.*"
    r"|(?:my|the|some|a\s+few|following|listed\s+below)\s+(?:are\s+)?(?:skills?|certifications?|certificates?|preferred\s+roles?|roles?|technologies?|tools?|projects?|education|experience)\b.*"
    r"|(?:i\s+have|i\s+hold|i\s+possess|i\s+know|i\s+use|i\s+work\s+with)\s+(?:the\s+following\s+)?(?:skills?|certifications?|tools?|technologies?)\b.*"
    r"|(?:skills?|certifications?|certificates?|preferred\s+roles?|technologies?|tools?)\s+(?:are|include|i\s+have|i\s+know)\b.*"
    r")",
    re.IGNORECASE,
)


# Single-word conversational fillers that must never be saved as structured list items.
_CONVERSATIONAL_FILLER_WORDS: frozenset[str] = frozenset({
    "any", "some", "yes", "no", "yeah", "yep", "nope", "sure", "okay", "ok",
    "both", "all", "none", "few", "many", "several", "various", "other",
    "these", "those", "this", "that", "them", "they", "it",
})


def _sanitize_structured_list_items(items: list) -> list:
    """
    Remove conversational noise phrases from a list of strings.
    Keeps only items that look like real structured data (short, no verb phrases).
    """
    if not isinstance(items, list):
        return items
    cleaned: list = []
    for item in items:
        if not isinstance(item, str):
            cleaned.append(item)
            continue
        stripped = item.strip()
        if not stripped:
            continue
        # Drop bare conversational filler words (e.g. "any", "yes", "some")
        if stripped.lower() in _CONVERSATIONAL_FILLER_WORDS:
            continue
        # Drop items that match conversational prefix patterns
        if _CONVERSATIONAL_LIST_PREFIXES.match(stripped):
            continue
        # Drop items that are full sentences (contain a verb phrase indicating prose)
        if re.search(
            r"\b(?:are|is|were|was|have|has|had|include|includes|following|below|above|here|these|those|this|that)\b",
            stripped,
            re.IGNORECASE,
        ) and len(stripped.split()) > 4:
            continue
        cleaned.append(stripped)
    return cleaned


def _sanitize_profile_updates(updates: dict) -> dict:
    """
    Validate and clean a profile_updates dict before it is applied.
    Strips conversational noise from list fields so only real structured data is saved.
    Also handles profile_deletions sub-dict.
    """
    if not isinstance(updates, dict):
        return {}
    sanitized: dict = {}
    for field, value in updates.items():
        if field == "profile_deletions":
            if isinstance(value, dict):
                sanitized[field] = value
            continue
        if field in ("skills", "certifications", "preferred_roles", "preferred_locations",
                     "preferred_industries", "employment_types"):
            if not isinstance(value, list):
                continue
            clean_items = _sanitize_structured_list_items(value)
            if clean_items:
                sanitized[field] = clean_items
        elif field == "work_experience":
            if not isinstance(value, list):
                continue
            valid_entries = [
                entry for entry in value
                if isinstance(entry, dict)
                and (_normalize_profile_text(entry.get("title")) or _normalize_profile_text(entry.get("company")))
            ]
            if valid_entries:
                sanitized[field] = valid_entries
        elif field == "education":
            if not isinstance(value, list):
                continue
            valid_entries = [
                entry for entry in value
                if isinstance(entry, dict)
                and (_normalize_profile_text(entry.get("degree")) or _normalize_profile_text(entry.get("institution")))
            ]
            if valid_entries:
                sanitized[field] = valid_entries
        elif field == "experience_years":
            try:
                sanitized[field] = float(value)
            except (TypeError, ValueError):
                pass
        elif field in ("availability", "notice_period"):
            if not isinstance(value, str):
                continue
            normalized = _normalize_availability_value(value)
            if normalized:
                sanitized[field] = normalized
        elif field == "salary_expectation":
            if not isinstance(value, str):
                continue
            cleaned = value.strip()
            if cleaned:
                sanitized[field] = cleaned
        elif value is not None:
            sanitized[field] = value
    return _sanitize_profile_field_mapping(sanitized)


# Scalar profile fields are shared by resume parsing, voice intake, and chat.
# Keep this validation deterministic: model prompts improve extraction, but must
# never be the only protection before a value reaches the candidates table.
_TIMELINE_ONLY_VALUE = re.compile(
    r"^\s*(?:\d{4}|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4})"
    r"\s*(?:[-–—]|to|through|until)\s*(?:\d{4}|present|current|now|"
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{4})\s*$",
    re.IGNORECASE,
)
_ROLE_WORDS = frozenset({
    "architect", "analyst", "associate", "consultant", "coordinator", "designer",
    "developer", "director", "engineer", "executive", "founder", "head", "intern",
    "lead", "manager", "officer", "partner", "president", "producer", "professor",
    "researcher", "scientist", "specialist", "supervisor", "technician", "trainee",
    "vice president", "vp",
})
_TECHNOLOGY_ONLY_VALUES = frozenset({
    "postgres", "postgresql", "mysql", "mongodb", "redis", "sqlite", "oracle", "sql",
    "python", "java", "javascript", "typescript", "fastapi", "django", "flask",
    "react", "angular", "node", "node.js", "spring", "spring boot", "docker",
    "kubernetes", "aws", "azure", "gcp", "git", "linux", "html", "css",
})


def _is_timeline_only_value(value: Any) -> bool:
    text_value = _normalize_profile_text(value)
    return bool(text_value and _TIMELINE_ONLY_VALUE.fullmatch(text_value))


def _is_actual_job_role(value: Any) -> bool:
    text_value = _normalize_profile_text(value)
    if not text_value or _is_timeline_only_value(text_value):
        return False
    lowered = text_value.casefold()
    if lowered in _TECHNOLOGY_ONLY_VALUES:
        return False
    words = set(re.findall(r"[a-z]+", lowered))
    return bool(words & _ROLE_WORDS)


def _is_actual_location(value: Any) -> bool:
    text_value = _normalize_profile_text(value)
    if not text_value or _is_timeline_only_value(text_value):
        return False
    # A lone technology/database is not a geographic location either.
    return text_value.casefold() not in _TECHNOLOGY_ONLY_VALUES


def _sanitize_profile_field_mapping(values: dict) -> dict:
    """Drop invalid role/location scalars and retain misclassified skills."""
    if not isinstance(values, dict):
        return {}
    result = dict(values)
    role = result.get("current_role")
    if role is not None and not _is_actual_job_role(role):
        # Preserve a clearly recognizable misclassified technology as a skill;
        # never let it overwrite a job-title column.
        role_text = _normalize_profile_text(role)
        if role_text and role_text.casefold() in _TECHNOLOGY_ONLY_VALUES:
            skills = result.get("skills") if isinstance(result.get("skills"), list) else []
            result["skills"] = _merge_skills(skills, [role_text])
        result.pop("current_role", None)
    location = result.get("location")
    if location is not None and not _is_actual_location(location):
        result.pop("location", None)
    return result


# Patterns that signal a deletion intent in natural language.
_DELETION_PATTERNS = re.compile(
    r"\b(?:remove|delete|drop|take\s+out|get\s+rid\s+of|no\s+longer\s+(?:want|have|need)|don'?t\s+(?:want|have)\s+(?:this|that|it|my)|i\s+don'?t\s+have\s+(?:this|that|it)\s+anymore)\b",
    re.IGNORECASE,
)

# Map section keywords to profile field names for deletion.
_DELETION_SECTION_MAP = {
    "skill": "skills",
    "skills": "skills",
    "certification": "certifications",
    "certifications": "certifications",
    "cert": "certifications",
    "role": "preferred_roles",
    "roles": "preferred_roles",
    "preferred role": "preferred_roles",
    "preferred roles": "preferred_roles",
    "experience": "work_experience",
    "work experience": "work_experience",
    "job": "work_experience",
    "education": "education",
    "degree": "education",
    "master": "education",
    "bachelor": "education",
    "project": "projects",
    "projects": "projects",
    "location": "preferred_locations",
    "preferred location": "preferred_locations",
    "preferred locations": "preferred_locations",
    "additional information": "additional_information",
    "additional info": "additional_information",
}


# Section keywords used in "delete my <item> <section>" pattern
_SECTION_KEYWORDS = (
    "certification", "cert", "degree", "master", "bachelor",
    "skill", "experience", "project", "role", "location",
)


def _resolve_section_field(section_raw: str) -> Optional[str]:
    """Map a raw section string to a profile field name."""
    s = section_raw.strip().lower().rstrip("s")
    field = _DELETION_SECTION_MAP.get(s) or _DELETION_SECTION_MAP.get(s + "s")
    if field:
        return field
    for key, val in _DELETION_SECTION_MAP.items():
        if s.startswith(key) or key.startswith(s):
            return val
    return None


def _detect_deletion_intent(message: str) -> Optional[dict]:
    """
    Detect natural-language deletion requests such as:
      "remove FastAPI from my skills"
      "delete my Java certification"
      "I no longer want Hyderabad as my preferred location"
      "delete my master's degree"
      "I no longer have the AWS certification"
    Returns {"field": str, "item": str} or None.
    """
    if not isinstance(message, str) or not _DELETION_PATTERNS.search(message):
        return None

    # Pattern 1: remove/delete <item> from (my) <section>
    m = re.search(
        r"\b(?:remove|delete|drop|take\s+out|get\s+rid\s+of)\s+(?P<item>.+?)\s+from\s+(?:my\s+)?(?P<section>[\w\s]+?)(?:[.!?;]|$)",
        message,
        re.IGNORECASE,
    )
    if m:
        item = m.group("item").strip().strip('"\'')
        field = _resolve_section_field(m.group("section"))
        if field and item:
            deletion = {"field": field, "item": item}
            # Additional Information is free-form text, so a single request can
            # name several phrases. Split them before persistence while keeping
            # the original item for backwards-compatible intent consumers.
            if field == "additional_information":
                deletion["items"] = _split_update_list(item)
            return deletion

    # Pattern 2: "I no longer want <item> as my (preferred) <section>"
    m_no_longer_as = re.search(
        r"\bno\s+longer\s+(?:want|have|need)\s+(?P<item>.+?)\s+as\s+(?:my\s+)?(?:preferred\s+)?(?P<section>[\w\s]+?)(?:[.!?;]|$)",
        message,
        re.IGNORECASE,
    )
    if m_no_longer_as:
        item = m_no_longer_as.group("item").strip().strip('"\'')
        field = _resolve_section_field(m_no_longer_as.group("section"))
        if field and item:
            return {"field": field, "item": item}

    # Pattern 3: "I no longer have/want/need (the) <item> <section_keyword>"
    section_kw_alt = "|".join(_SECTION_KEYWORDS)
    m_no_longer_kw = re.search(
        rf"\bno\s+longer\s+(?:want|have|need)\s+(?:the\s+)?(?P<item>[\w\s.+#-]{{2,60}}?)\s+(?P<section>{section_kw_alt})s?(?:[.!?;]|$)",
        message,
        re.IGNORECASE,
    )
    if m_no_longer_kw:
        item = m_no_longer_kw.group("item").strip().strip('"\'')
        field = _resolve_section_field(m_no_longer_kw.group("section"))
        if field and item:
            return {"field": field, "item": item}
    # Also handle "no longer have" with no explicit section (item only)
    m_no_longer_bare = re.search(
        r"\bno\s+longer\s+(?:want|have|need)\s+(?:the\s+)?(?P<item>[\w\s.+#-]{2,80})(?:[.!?;]|$)",
        message,
        re.IGNORECASE,
    )
    if m_no_longer_bare:
        item = m_no_longer_bare.group("item").strip().strip('"\'')
        if item:
            return {"field": None, "item": item}

    # Pattern 4: "delete/remove my <item> <section_keyword>" e.g. "delete my Java certification"
    section_kw = "|".join(_SECTION_KEYWORDS)
    # Allow apostrophes in item (e.g. "master's degree")
    m_my_item_section = re.search(
        rf"\b(?:remove|delete|drop)\s+my\s+(?P<item>[\w\s.+#'\u2019-]{{2,60}}?)\s+(?P<section>{section_kw})s?(?:[.!?;]|$)",
        message,
        re.IGNORECASE,
    )
    if m_my_item_section:
        item = m_my_item_section.group("item").strip().strip('"\'')
        field = _resolve_section_field(m_my_item_section.group("section"))
        if field and item:
            return {"field": field, "item": item}

    # Pattern 5: remove/delete <item> (no explicit section — field unknown)
    m2 = re.search(
        r"\b(?:remove|delete|drop)\s+(?P<item>[\w\s.+#-]{2,60})(?:[.!?;]|$)",
        message,
        re.IGNORECASE,
    )
    if m2:
        item = m2.group("item").strip().strip('"\'')
        if item:
            return {"field": None, "item": item}  # field unknown; caller must resolve
    return None


def _apply_deletion_to_profile_updates(updates: dict, deletion: dict) -> dict:
    """
    Embed a deletion instruction into the profile_updates dict so
    _apply_profile_updates can process it.
    """
    if not deletion or not deletion.get("item"):
        return updates
    result = dict(updates)
    existing_deletions = dict(result.get("profile_deletions") or {})
    field = deletion.get("field")
    items = deletion.get("items") or [deletion["item"]]
    # A user-specified Additional Information target is authoritative. The LLM
    # may otherwise infer a certifications deletion from words in the item.
    if field == "additional_information":
        existing_deletions = {
            "additional_information": existing_deletions.get("additional_information") or []
        }
    if field:
        bucket = existing_deletions.get(field) or []
        for item in items:
            if item not in bucket:
                bucket.append(item)
        existing_deletions[field] = bucket
    else:
        # Unknown field — store under "_unknown" for best-effort matching
        bucket = existing_deletions.get("_unknown") or []
        for item in items:
            if item not in bucket:
                bucket.append(item)
        existing_deletions["_unknown"] = bucket
    result["profile_deletions"] = existing_deletions
    return result


def _extract_profile_updates(reply_text: str, candidate_message: str = "") -> tuple[str, Optional[dict]]:
    """Split LLM reply into (clean_reply, profile_updates_dict).
    Also detects natural-language deletion requests in the candidate message.
    """
    marker_start = "<<<PROFILE_UPDATES>>>"
    marker_end = "<<<END_UPDATES>>>"

    # Detect deletion intent from the candidate's own message first
    deletion = _detect_deletion_intent(candidate_message) if candidate_message else None

    if marker_start not in reply_text:
        fallback_updates = _infer_profile_updates_from_message(candidate_message or reply_text)
        sanitized = _sanitize_profile_updates(fallback_updates) if fallback_updates else {}
        if deletion:
            sanitized = _apply_deletion_to_profile_updates(sanitized, deletion)
        return reply_text.strip(), _correct_profile_categories(sanitized, candidate_message) or None

    parts = reply_text.split(marker_start, 1)
    clean = parts[0].strip()
    rest = parts[1].split(marker_end, 1)[0].strip()
    try:
        data = json.loads(rest)
        updates = data.get("profile_updates") or {}
        if isinstance(updates, dict):
            sanitized = _sanitize_profile_updates(updates)
        else:
            sanitized = {}
        if deletion:
            sanitized = _apply_deletion_to_profile_updates(sanitized, deletion)
        return clean, _correct_profile_categories(sanitized, candidate_message) or None
    except Exception:
        fallback_updates = _infer_profile_updates_from_message(candidate_message or reply_text)
        sanitized = _sanitize_profile_updates(fallback_updates) if fallback_updates else {}
        if deletion:
            sanitized = _apply_deletion_to_profile_updates(sanitized, deletion)
        return clean, _correct_profile_categories(sanitized, candidate_message) or None


def _split_update_list(text: str) -> list[str]:
    """Split a natural-language list into normalized items."""
    if not isinstance(text, str):
        return []
    cleaned = text.strip().strip(".,;: ")
    if not cleaned:
        return []
    cleaned = re.sub(r"\s+(?:and|or|&)\s+", ", ", cleaned, flags=re.IGNORECASE)
    parts = [part.strip(" .;:") for part in re.split(r"[,/|]", cleaned)]
    return [part for part in parts if part]


def _looks_like_target_role_phrase(text: str) -> bool:
    """Return True when a phrase is describing a desired role, not a current role."""
    if not isinstance(text, str):
        return False
    t = text.strip().lower()
    return t.startswith((
        "targeting ",
        "looking for ",
        "seeking ",
        "wanting ",
        "aiming for ",
        "interested in ",
        "open to ",
    ))


def _extract_first_match(text: str, patterns: list[str]) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        value = match.group("value") if "value" in match.groupdict() else match.group(0)
        value = re.sub(r"^[,\s:-]+", "", value).strip(" .;:")
        if value:
            return value
    return None


_IMMEDIATE_JOINER_PATTERN = re.compile(
    r"\b(?:immediate\s+joiner|i(?:'m| am)?\s+an?\s+immediate\s+joiner)\b",
    re.IGNORECASE,
)


def _normalize_availability_value(value: Any) -> str:
    """Normalize known availability phrases while preserving specific notice periods."""
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        return ""
    if _IMMEDIATE_JOINER_PATTERN.search(cleaned):
        return "Immediately"
    return cleaned


def _infer_profile_updates_from_message(message: str) -> dict:
    """
    Deterministically infer explicit profile updates from the candidate's message.

    This is a conservative fallback used when the LLM reply does not emit the
    structured markers. It only extracts fields that are directly stated.
    """
    if not isinstance(message, str) or not message.strip():
        return {}

    text = " ".join(message.split())
    lower = text.lower()
    updates: dict[str, Any] = {}

    preferred_roles = _extract_first_match(
        text,
        [
            r"\b(?:i(?:'m| am)?\s+)?(?:targeting|looking for|seeking|want(?:ing)?|interested in|open to)\s+(?P<value>.+?)(?:\s+roles?\b|\s+positions?\b|\s+opportunities\b|[.!?;]|$)",
            r"\bpreferred roles?\s*[:\-]\s*(?P<value>.+?)(?:[.!?;]|$)",
        ],
    )
    if preferred_roles:
        roles = _normalize_preferred_roles(_split_update_list(preferred_roles))
        if roles:
            updates["preferred_roles"] = roles

    skills = _extract_first_match(
        text,
        [
            r"\bpreferably\s+with\s+(?P<value>.+?)(?:[.!?;]|$)",
            r"\b(?:preferably\s+)?working with\s+(?P<value>.+?)(?:[.!?;]|$)",
            r"\bskills?\s*[:\-]\s*(?P<value>.+?)(?:[.!?;]|$)",
            r"\b(?:i\s+)?know\s+(?P<value>.+?)(?:[.!?;]|$)",
            r"\bexperience with\s+(?P<value>.+?)(?:[.!?;]|$)",
            r"\busing\s+(?P<value>.+?)(?:[.!?;]|$)",
        ],
    )
    if skills:
        normalized_skills = _merge_skills([], _split_update_list(skills))
        if normalized_skills:
            updates["skills"] = normalized_skills

    availability = _extract_first_match(
        text,
        [
            r"\b(?:i(?:'m| am)?\s+an?\s+)?immediate\s+joiner\b",
            r"\b(?:i(?:'m| am)?\s+)?(?:available|can join|can start|start)\s+(?P<value>immediately|right away|now|today|within\b.+?)(?:[.!?;]|$)",
            r"\b(?:i(?:'m| am)?\s+)?(?:available immediately|can join immediately|can start immediately)\b",
            r"\bnotice period\s*[:\-]\s*(?P<value>.+?)(?:[.!?;]|$)",
        ],
    )
    if availability:
        availability = _normalize_availability_value(availability)
        updates["availability"] = availability
        updates["notice_period"] = availability
    else:
        notice_period = _extract_first_match(
            text, [r"\b(?:have|with|on)?\s*(?P<value>\d+\s*(?:days?|weeks?|months?))\s+notice period\b"]
        )
        if notice_period:
            updates["notice_period"] = notice_period

    salary_expectation = _extract_first_match(
        text,
        [
            r"\b(?:salary expectation|expected salary|salary range|compensation expectation)\s*[:\-]\s*(?P<value>.+?)(?:[.!?;]|$)",
            r"\b(?:salary expectation|expected salary|salary range|compensation expectation)\s+(?:is|of)\s+(?P<value>.+?)(?:[.!?;]|$)",
            r"\b(?:i(?:'m| am)?\s+(?:expecting|targeting|seeking|looking for|hoping for|want(?:ing)?|after)|expecting|targeting|seeking|looking for|hoping for|want(?:ing)?|after)\s+(?P<value>(?:[₹$€£]\s*)?\d[\d,]*(?:\s*(?:[-–—]|to)\s*(?:[₹$€£]\s*)?\d[\d,]*)?(?:\s*(?:k|lpa|pa|per annum|annual(?:ly)?|year(?:ly)?|yr|month(?:ly)?|lac|lakhs?|crore|crores))?)(?:[.!?;]|$)",
        ],
    )
    if salary_expectation:
        # Preserve the fallback's established output contract; application maps
        # this legacy extraction alias into the canonical expected_salary key.
        updates["salary_expectation"] = salary_expectation.strip()

    remote_match = re.search(r"\b(?:prefer|want|looking for|open to)\s+(remote|hybrid|on[ -]?site|flexible)\b", text, re.IGNORECASE)
    if remote_match:
        updates["remote_preference"] = {"remote": "Remote", "hybrid": "Hybrid", "on-site": "On-site", "onsite": "On-site", "flexible": "Flexible"}[remote_match.group(1).lower().replace(" ", "-")]

    employment_match = re.search(r"\b(full[ -]?time|part[ -]?time|contract|freelance|internship)\b", text, re.IGNORECASE)
    if employment_match and any(term in lower for term in ("prefer", "looking for", "want", "open to")):
        updates["employment_types"] = [_normalize_profile_text(employment_match.group(1)).title().replace("Full-Time", "Full-time").replace("Part-Time", "Part-time")]

    locations = _extract_first_match(text, [
        r"\b(?:preferred locations?|locations? preferred)\s*[:\-]\s*(?P<value>.+?)(?:[.!?;]|$)",
        r"\b(?:i\s+)?prefer\s+(?P<value>[A-Z][A-Za-z .'-]+?)(?:\s+(?:now|instead)|[.!?;]|$)",
    ])
    if locations:
        updates["preferred_locations"] = _split_update_list(locations)
        # "actually ... now" is a correction, not an additional location.
        if re.search(r"\b(?:actually\s*,?\s*)?i\s+prefer\b.*\b(?:now|instead)\b", text, re.I):
            updates["replace_preferred_locations"] = True
    industries = _extract_first_match(text, [r"\b(?:preferred|target) industries?\s*[:\-]\s*(?P<value>.+?)(?:[.!?;]|$)"])
    if industries:
        updates["preferred_industries"] = _split_update_list(industries)
    if re.search(r"\b(?:willing|happy|open)\s+to\s+relocate\b", text, re.IGNORECASE):
        updates["willing_to_relocate"] = True
    elif re.search(r"\b(?:not|not willing|unwilling)\s+to\s+relocate\b", text, re.IGNORECASE):
        updates["willing_to_relocate"] = False
    if re.search(r"\b(?:not\s+open|closed)\s+to\s+(?:new\s+)?opportunities\b", text, re.IGNORECASE):
        updates["open_to_opportunities"] = False
    elif re.search(r"\bopen\s+to\s+(?:new\s+)?opportunities\b", text, re.IGNORECASE):
        updates["open_to_opportunities"] = True

    current_role = _extract_first_match(
        text,
        [
            r"\bmy current (?:role|title) is\s+(?:an?\s+)?(?P<value>.+?)(?:\s+with\b|\s+at\b|\s+for\b|[.!?;]|$)",
            r"\b(?:i(?:'m| am)\s+currently\s+(?:working\s+as|work(?:ing)?\s+as)|currently\s+(?:working\s+as|work(?:ing)?\s+as)|i\s+work\s+as|i(?:'m| am)\s+working\s+as|working\s+as)\s+(?:an?\s+)?(?P<value>.+?)(?:\s+with\b|\s+at\b|\s+for\b|[.!?;]|$)",
            r"\b(?:i(?:'m| am)\s+(?:a|an))\s+(?P<value>.+?)(?:\s+with\b|\s+at\b|\s+for\b|[.!?;]|$)",
        ],
    )
    if current_role and not _looks_like_target_role_phrase(current_role):
        updates["current_role"] = current_role

    experience_match = re.search(r"\b(?P<value>\d+(?:\.\d+)?)\s*\+?\s*years?\b", lower)
    if experience_match:
        try:
            updates["experience_years"] = float(experience_match.group("value"))
        except ValueError:
            pass

    location = _extract_first_match(
        text,
        [
            r"\b(?:based in|located in|live in|living in|from)\s+(?P<value>.+?)(?:[.!?;]|$)",
        ],
    )
    if location:
        updates["location"] = location

    work_experience: list[dict[str, str]] = []
    work_patterns = [
        # "I am a Backend Developer working at Viralbug from January 2025 to present."
        re.compile(
            r"\b(?:i\s+am|i(?:'m| am))\s+(?:an?\s+)?(?P<title>.+?)\s+working\s+at\s+(?P<company>.+?)\s+"
            r"from\s+(?P<start>.+?)\s+(?:to|until|through)\s+(?P<end>present|current|ongoing|now|.+?)(?:[.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:i\s+was\s+working|i\s+worked|worked|working|i\s+have\s+been\s+working)\s+"
            r"(?:from\s+)?(?P<start>.+?)\s+(?:to|until|through)\s+(?P<end>present|current|ongoing|now|.+?)\s+"
            r"at\s+(?P<company>.+?)\s+as\s+(?:an?\s+)?(?P<title>.+?)(?:[.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:i\s+was\s+working|i\s+worked|worked|working|i\s+have\s+been\s+working)\s+"
            r"at\s+(?P<company>.+?)\s+as\s+(?:an?\s+)?(?P<title>.+?)\s+"
            r"(?:from\s+)?(?P<start>.+?)\s+(?:to|until|through)\s+(?P<end>present|current|ongoing|now|.+?)(?:[.!?;]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bworked\s+at\s+(?P<company>.+?)\s+as\s+(?:an?\s+)?(?P<title>.+?)(?:[.!?;]|$)",
            re.IGNORECASE,
        ),
    ]

    for pattern in work_patterns:
        match = pattern.search(text)
        if not match:
            continue
        company = _normalize_profile_text(match.groupdict().get("company"))
        title = _normalize_profile_text(match.groupdict().get("title"))
        title = re.sub(r"\s+for\s+\d+(?:\.\d+)?\s*years?\s*$", "", title, flags=re.I).strip()
        if not company or not title:
            continue
        entry: dict[str, str] = {"title": title, "company": company}
        start = _normalize_profile_text(match.groupdict().get("start"))
        end = _normalize_profile_text(match.groupdict().get("end"))
        if start:
            entry["start_date"] = start
        if end:
            entry["end_date"] = end
        description = _extract_first_match(
            text,
            [
                r"\b(?:i\s+was\s+working|i\s+worked|worked|working|i\s+have\s+been\s+working)\b.*?(?:\.\s*|\s+and\s+|\s+while\s+)(?P<value>.+)$",
            ],
        )
        if description:
            entry["description"] = description.strip(" .;:")
        work_experience = [entry]
        break

    if work_experience:
        updates["work_experience"] = work_experience

    education_match = re.search(
        r"\b(?:completed|studied|graduated(?:\s+from)?|earned)\s+(?:my\s+|a\s+|an\s+)?(?P<degree>(?:master'?s|bachelor'?s|mba|m\.?(?:tech|sc|a)|b\.?(?:tech|sc|a)|ph\.?d)[^,.]*?)\s+(?:at|from)\s+(?P<institution>[^,.!?;]+)",
        text, re.I,
    )
    if education_match:
        updates["education"] = [
            {
                "degree": _normalize_profile_text(education_match.group("degree")),
                "institution": _normalize_profile_text(education_match.group("institution")),
            }
        ]

    certifications = _extract_first_match(
        text,
        [
            r"\bcertifications?\s*[:\-]\s*(?P<value>.+?)(?:[.!?;]|$)",
            r"\b(?:hold|holding|have|earned|completed|obtained|got)\s+(?P<value>.+?)(?:\s+certifications?\b|\s+certified\b|[.!?;]|$)",
        ],
    )
    # A university/degree statement must never flow into certifications merely
    # because it used a verb such as "completed" or "earned".
    if certifications and not education_match:
        normalized_certs = _merge_certifications([], [
            re.sub(r"^(?:a|an|the)\s+", "", item, flags=re.I).strip()
            for item in _split_update_list(certifications)
        ])
        if normalized_certs:
            updates["certifications"] = normalized_certs

    return updates


def _correct_profile_categories(updates: dict, candidate_message: str) -> dict:
    """Make direct candidate wording authoritative over an LLM's category guess.

    This is deliberately narrow: it supplements a structured extractor with
    unambiguous conversational facts, rather than attempting a parallel schema.
    """
    result = dict(updates or {})
    inferred = _infer_profile_updates_from_message(candidate_message)
    if "education" in inferred:
        # Degree + institution is conclusive education evidence, including when
        # the institution name resembles a training provider.
        result["education"] = inferred["education"]
        result.pop("certifications", None)
    for field in ("skills", "work_experience", "preferred_roles", "preferred_locations"):
        if field in inferred:
            if field in ("skills", "preferred_roles", "preferred_locations") and isinstance(result.get(field), list):
                result[field] = _merge_profile_updates({field: result[field]}, {field: inferred[field]})[field]
            else:
                result[field] = inferred[field]
    if inferred.get("replace_preferred_locations"):
        result["replace_preferred_locations"] = True
    return _sanitize_profile_updates(result)


VALID_UPDATE_FIELDS = {
    "name", "email", "phone", "location", "headline", "bio",
    "current_role", "experience_years", "skills", "work_experience", "education",
    "preferred_roles", "availability", "notice_period", "salary_expectation", "certifications",
    "projects", "preferred_locations", "preferred_industries", "employment_types",
    "remote_preference", "expected_salary", "willing_to_relocate",
    "open_to_opportunities", "additional_information",
    "profile_deletions", "profile_record_replacements",
    "replace_preferred_locations",
}


def _profile_edit_matches(candidate: dict, value: str) -> dict[str, list[dict]]:
    """Find a candidate supplied value in every editable record collection.

    This intentionally searches persisted columns, raw profile data, and the
    parsed-resume snapshot.  It is the server-side source of truth used before
    a conversational replacement or removal is allowed to write.
    """
    needle = _normalize_profile_key(value)
    if not needle:
        return {}
    raw = _parse_raw_data(candidate.get("raw_data"))
    parsed = _parse_raw_data(candidate.get("parsed_resume_json"))
    sources = {
        "Education": candidate.get("education") or [],
        "Work Experience": candidate.get("work_experience") or [],
        "Projects": raw.get("projects") or parsed.get("projects") or [],
        "Skills": candidate.get("skills") or [],
        "Certifications": _candidate_certification_sources(candidate),
        "Preferences": [raw.get(key) for key in (
            "preferred_roles", "preferred_locations", "preferred_industries",
            "employment_types", "remote_preference", "expected_salary",
            "notice_period", "additional_information",
        )],
    }
    matches: dict[str, list[dict]] = {}
    for section, records in sources.items():
        if not isinstance(records, list):
            records = [records]
        for index, record in enumerate(records):
            text_value = json.dumps(record, sort_keys=True) if isinstance(record, dict) else str(record or "")
            if needle in _normalize_profile_key(text_value):
                matches.setdefault(section, []).append({"index": index, "record": record})
    return matches


def _profile_record_label(section: str, record: object) -> str:
    """A short, human-readable label for a selectable profile record."""
    if isinstance(record, dict):
        if section == "Education":
            return " — ".join(part for part in (record.get("degree"), record.get("institution")) if part) or "Education record"
        if section == "Work Experience":
            return " — ".join(part for part in (record.get("title"), record.get("company")) if part) or "Work experience record"
        if section == "Projects":
            return str(record.get("name") or record.get("title") or "Project")
        return " — ".join(str(value) for value in record.values() if value)[:120] or section
    return str(record) or section


def _replacement_update(section: str, old: str, new: str, matches: list[dict], *, all_matches: bool = False) -> dict:
    """Make a replacement payload whose exact persisted records are explicit."""
    return {"profile_record_replacements": [{
        "section": section, "old": old, "new": new,
        "record_indexes": [match["index"] for match in matches],
        "all_matches": all_matches,
    }]}


def _replacement_clarification(old: str, matches: dict[str, list[dict]]) -> str:
    choices = [(section, item) for section, items in matches.items() for item in items]
    lines = [f"I found {old} in multiple profile records. Which one would you like to update?", ""]
    lines.extend(f"{number}. {_profile_record_label(section, item['record'])}" for number, (section, item) in enumerate(choices, 1))
    lines.append(f"{len(choices) + 1}. Both")
    return "\n".join(lines)


def _pending_replacement_selection(message: str, candidate: dict, history: list[dict]) -> Optional[dict]:
    """Resolve a number or 'both' after an ambiguity prompt, without storing state client-side."""
    choice = message.strip().lower().rstrip(".")
    if not (choice == "both" or re.fullmatch(r"\d+", choice)):
        return None
    prior_users = [m.get("content", "") for m in history[:-1] if m.get("role") == "user"]
    for prior in reversed(prior_users):
        replacement = re.search(r"\b(?:replace|update|change)\s+(.+?)\s+(?:with|to)\s+(.+?)[.!]?$", prior, re.I)
        if not replacement:
            continue
        old, new = replacement.group(1).strip(), replacement.group(2).strip()
        matches = _profile_edit_matches(candidate, old)
        choices = [(section, item) for section, items in matches.items() for item in items]
        if len(choices) < 2:
            return None
        selected = choices if choice == "both" else [choices[int(choice) - 1]] if 0 < int(choice) <= len(choices) else []
        if not selected:
            return {"reply": f"Please choose a number from 1 to {len(choices)}, or Both.", "updates": None}
        by_section: dict[str, list[dict]] = {}
        for section, item in selected:
            by_section.setdefault(section, []).append(item)
        updates = {"profile_record_replacements": [
            {"section": section, "old": old, "new": new, "record_indexes": [item["index"] for item in items], "all_matches": choice == "both"}
            for section, items in by_section.items()
        ]}
        return {"reply": f"Updated {old} to {new}.", "updates": updates}
    return None


def _chat_profile_preflight(message: str, candidate: dict, history: list[dict]) -> Optional[dict]:
    """Resolve deterministic profile-edit safety cases before asking the LLM.

    A response from this function is deliberately a no-write clarification or
    a fully specified update.  This prevents a model instruction from choosing
    between duplicate records or creating a partial education/employment row.
    """
    text_value = message.strip()
    lower = text_value.lower()
    pending_selection = _pending_replacement_selection(text_value, candidate, history)
    if pending_selection:
        return pending_selection
    # Education has an unambiguous semantic shape even when the candidate does
    # not literally say "education" (for example: "Add Masters Degree in CMR
    # University 2023-2025"). Recognize it before the generic bare-add guard.
    education_with_dates = re.search(
        r"\b(?:add|completed|studied|graduated(?:\s+from)?|earned)\s+(?:my\s+|a\s+|an\s+)?"
        r"(?P<degree>(?:master'?s|bachelor'?s|mba|m\.?(?:tech|sc|a)|b\.?(?:tech|sc|a)|ph\.?d)[^,.;]*?(?:degree)?)"
        r"\s+(?:at|from|in)\s+(?P<institution>[^,.;]*?)\s+(?P<start>\d{4})\s*(?:-|–|—|to)\s*(?P<end>\d{4}|present)\b",
        text_value, re.I,
    )
    if education_with_dates:
        degree = _normalize_profile_text(education_with_dates.group("degree"))
        degree = re.sub(r"\bmasters\b", "Master's", degree, flags=re.I)
        degree = re.sub(r"\bbachelors\b", "Bachelor's", degree, flags=re.I)
        return {"reply": f"Added your {degree} at {education_with_dates.group('institution').strip()}.", "updates": {"education": [{
            "degree": degree,
            "institution": _normalize_profile_text(education_with_dates.group("institution")),
            "start_date": education_with_dates.group("start"),
            "end_date": education_with_dates.group("end"),
        }]}}

    # A bare "Add X" has no durable profile destination. Do not infer Skills.
    bare_add = re.match(r"^add\s+(.+?)[.!]?$", text_value, re.I)
    if bare_add and not re.search(r"\b(skill|skills|education|experience|project|certification|preference|master'?s|bachelor'?s|mba|university|college|degree)\b", lower):
        return {"reply": f"Where would you like me to add {bare_add.group(1).strip()}?", "updates": None}

    # Complete an immediately preceding education/work timeline question using
    # the original statement retained in the chat history.
    years = re.fullmatch(r"\s*(\d{4})\s*(?:-|–|—|to)\s*(\d{4}|present)\s*", text_value, re.I)
    if years:
        prior_users = [m.get("content", "") for m in history[:-1] if m.get("role") == "user"]
        prior = prior_users[-1] if prior_users else ""
        education = re.search(r"(?:completed|add|my)\s+(?:a\s+)?(.+?(?:master'?s|bachelor'?s|mba|ph\.?d)[^.]*)\s+(?:at|from)\s+([^,.]+)", prior, re.I)
        work = re.search(r"(?:worked|work)\s+(?:at|for)\s+([^,.]+?)\s+as\s+(?:a\s+)?([^,.]+)", prior, re.I)
        if education:
            degree, institution = re.sub(r"^my\s+", "", education.group(1).strip(), flags=re.I), education.group(2).strip()
            return {"reply": f"Added your {degree} at {institution} from {years.group(1)} - {years.group(2)}.", "updates": {"education": [{"degree": degree, "institution": institution, "start_date": years.group(1), "end_date": years.group(2)}]}}
        if work:
            company, title = work.group(1).strip(), work.group(2).strip()
            return {"reply": f"Added your {title} experience at {company} from {years.group(1)} - {years.group(2)}.", "updates": {"work_experience": [{"title": title, "company": company, "start_date": years.group(1), "end_date": years.group(2)}]}}

    new_education = re.search(r"(?:completed|add|my)\s+(?:a\s+)?([^,.]*(?:master'?s|bachelor'?s|mba|ph\.?d)[^,.]*)\s+(?:at|from)\s+([^,.]+)", text_value, re.I)
    if new_education and not re.search(r"\b(?:change|update)\b.*\bfrom\b.*\bto\b", text_value, re.I) and not re.search(r"\b\d{4}\s*(?:-|–|—|to)\s*(?:\d{4}|present)\b", text_value, re.I):
        return {"reply": f"What was the time period for your {new_education.group(1).strip()}? Please provide it like YYYY - YYYY.", "updates": None}
    new_work = re.search(r"(?:worked|work)\s+(?:at|for)\s+([^,.]+?)\s+as\s+(?:a\s+)?([^,.]+)", text_value, re.I)
    if new_work and not re.search(r"\b\d{4}\s*(?:-|–|—|to)\s*(?:\d{4}|present)\b", text_value, re.I):
        return {"reply": f"What was your employment period at {new_work.group(1).strip()}? Please provide it like YYYY - YYYY.", "updates": None}

    deletion = re.search(r"\b(?:delete|remove)\b\s+(?:my\s+)?(.+?)(?:\s+(?:work\s+)?experience)?[.!]?$", text_value, re.I)
    if deletion:
        requested = deletion.group(1).strip()
        matches = _profile_edit_matches(candidate, requested)
        total = sum(len(items) for items in matches.values())
        if total > 1:
            return {"reply": f"I found {requested} in {' and '.join(matches)}. Which record should I delete?", "updates": None}

    explicit_target = re.search(r"\b(?:change|update)\s+(?:my\s+)?(?P<target>master(?:'s|s)|bachelor(?:'s|s)|mba|ph\.?d).*?\s+from\s+(?P<old>.+?)\s+to\s+(?P<new>.+?)[.!]?$", text_value, re.I)
    replacement = re.search(r"\b(?:replace|update|change)\s+(.+?)\s+(?:with|to)\s+(.+?)[.!]?$", text_value, re.I)
    if explicit_target:
        old, new = explicit_target.group("old").strip(), explicit_target.group("new").strip()
        matches = _profile_edit_matches(candidate, old)
        target = _normalize_profile_key(explicit_target.group("target")).replace("'", "")
        matches = {section: [item for item in items if target in _normalize_profile_key(json.dumps(item["record"])).replace("'", "")] for section, items in matches.items()}
        matches = {section: items for section, items in matches.items() if items}
        if sum(map(len, matches.values())) == 1:
            section, items = next(iter(matches.items()))
            return {"reply": f"Updated {old} to {new} in {section}.", "updates": _replacement_update(section, old, new, items)}
    if replacement:
        old, new = replacement.group(1).strip(), replacement.group(2).strip()
        explicit_both = bool(re.search(r"\s+in\s+both\b", new, re.I))
        new = re.sub(r"\s+in\s+both\b.*$", "", new, flags=re.I).strip()
        matches = _profile_edit_matches(candidate, old)
        sections = list(matches)
        total = sum(len(items) for items in matches.values())
        if explicit_both and total:
            return {"reply": f"Updated {old} to {new} in all matching records.", "updates": {"profile_record_replacements": [_replacement_update(section, old, new, items, all_matches=True)["profile_record_replacements"][0] for section, items in matches.items()]}}
        if total > 1:
            return {"reply": _replacement_clarification(old, matches), "updates": None}
        if total == 1:
            section = sections[0]
            if section in ("Education", "Work Experience"):
                return {"reply": f"Updated {old} to {new} in {section}.", "updates": _replacement_update(section, old, new, matches[section])}
    return None


async def _verify_profile_update_persisted(candidate_id: str, updates: dict) -> bool:
    """Read the canonical candidate row after a chat write before confirming it."""
    persisted = await _get_candidate_row(candidate_id)
    for replacement in updates.get("profile_record_replacements") or []:
        if not isinstance(replacement, dict) or replacement.get("section") not in ("Education", "Work Experience"):
            continue
        old, new = replacement.get("old"), replacement.get("new")
        records = persisted.get("education" if replacement["section"] == "Education" else "work_experience") or []
        indexes = replacement.get("record_indexes") or []
        if not isinstance(old, str) or not isinstance(new, str) or not indexes or any(
            not isinstance(index, int) or index >= len(records) or new.lower() not in json.dumps(records[index]).lower()
            for index in indexes
        ):
            return False
    for entry in updates.get("education") or []:
        if not isinstance(entry, dict):
            continue
        if not any(all(
            _normalize_profile_key(row.get(field)) == _normalize_profile_key(entry.get(field))
            for field in ("degree", "institution", "start_date", "end_date")
        ) for row in (persisted.get("education") or []) if isinstance(row, dict)):
            return False
    return True


def _remove_item_from_list(existing_list: list, item_to_remove: str) -> tuple[list, bool]:
    """
    Remove an item from a list using case-insensitive matching.
    Returns (new_list, was_found).
    """
    key = item_to_remove.strip().lower()
    new_list = [x for x in existing_list if str(x).strip().lower() != key]
    return new_list, len(new_list) < len(existing_list)


def _remove_item_from_dict_list(existing_list: list, item_to_remove: str, match_fields: list) -> tuple[list, bool]:
    """
    Remove a dict entry from a list by fuzzy-matching item_to_remove against match_fields.
    Returns (new_list, was_found).
    """
    key = item_to_remove.strip().lower()
    new_list = []
    found = False
    for entry in existing_list:
        if not isinstance(entry, dict):
            new_list.append(entry)
            continue
        matched = any(
            key in str(entry.get(f) or "").strip().lower()
            or str(entry.get(f) or "").strip().lower() in key
            for f in match_fields
        )
        if matched and not found:
            found = True  # remove only the first match
        else:
            new_list.append(entry)
    return new_list, found


def _remove_phrase_from_text(existing_text: Any, phrase_to_remove: str) -> tuple[Any, bool]:
    """Remove one phrase from free-form text, ignoring case and separators.

    Additional information is a single text value in ``raw_data``, rather than a
    list.  Match words across whitespace/punctuation variations (for example,
    ``Java full-stack`` for ``Java Full Stack``), while keeping surrounding text.
    """
    if not isinstance(existing_text, str) or not isinstance(phrase_to_remove, str):
        return existing_text, False

    words = re.findall(r"\w+", phrase_to_remove, flags=re.UNICODE)
    if not words:
        return existing_text, False

    separator = r"[\W_]+"
    pattern = r"(?<!\w)" + separator.join(re.escape(word) for word in words) + r"(?!\w)"
    updated_text, replacements = re.subn(pattern, "", existing_text, flags=re.IGNORECASE)
    if not replacements:
        return existing_text, False

    # Keep list-like prose tidy after an item is removed without altering the
    # remaining content or any other raw_data fields.
    updated_text = re.sub(r"([,;|])\s*(?:[,;|]\s*)+", r"\1 ", updated_text)
    updated_text = re.sub(r"(?m)^[ \t]*[-*•][ \t]*(?:\r?\n|$)", "", updated_text)
    updated_text = re.sub(r"[ \t]{2,}", " ", updated_text)
    updated_text = re.sub(r"^[ \t,;|]+|[ \t,;|]+$", "", updated_text)
    return updated_text, True


# System prompt for scanning a candidate answer against ALL missing profile fields.
_MULTI_FIELD_EXTRACT_SYSTEM = """You are a recruitment data extractor.
Given a candidate's message and a list of missing profile fields, extract any information
that fills one or more of those fields.
Return ONLY valid JSON with the exact keys below (omit keys where nothing was found):
{
  "current_role": "",
  "location": "",
  "bio": "",
  "experience_years": null,
  "skills": [],
  "preferred_roles": [],
  "preferred_locations": [],
  "preferred_industries": [],
  "employment_types": [],
  "remote_preference": "",
  "availability": "",
  "notice_period": "",
  "expected_salary": "",
  "willing_to_relocate": null,
  "certifications": [],
  "additional_information": ""
}
Rules:
- Only include a field if the candidate explicitly provided that information.
- Do NOT invent or hallucinate.
- skills and certifications must be plain name strings, not sentences.
- preferred_roles must be job title strings.
- preferred_locations, preferred_industries, and employment_types must be lists of explicit preferences.
- remote_preference must be Remote, Hybrid, On-site, or Flexible only when explicitly stated.
- willing_to_relocate must be true or false only when explicitly stated.
- experience_years must be a number or null."""


async def _extract_multi_field_updates_from_answer(
    candidate_message: str,
    missing_fields: list[str],
) -> dict:
    """
    Scan the candidate's answer against ALL currently missing profile fields and
    return a sanitized profile_updates dict for every field that was answered.
    Returns {} on failure or when nothing was found.
    """
    if not candidate_message or not missing_fields:
        return {}
    try:
        resp = await openai_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _MULTI_FIELD_EXTRACT_SYSTEM},
                {"role": "user", "content": json.dumps({
                    "candidate_message": candidate_message[:2000],
                    "missing_fields": missing_fields,
                })},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = json.loads(resp.choices[0].message.content or "{}")
        return _sanitize_profile_updates(raw)
    except Exception as e:
        logger.warning("[chat-multi-field] extraction failed: %s", e)
        return {}


def _merge_profile_updates(base: dict, extra: dict) -> dict:
    """Merge extra profile_updates into base without overwriting non-empty base values."""
    if not extra:
        return base
    result = dict(base)
    for field, value in extra.items():
        if field == "profile_deletions":
            continue
        if field in ("availability", "notice_period"):
            if not isinstance(value, str):
                continue
            value = _normalize_availability_value(value)
            if not value:
                continue
        elif field == "salary_expectation":
            if not isinstance(value, str):
                continue
            value = value.strip()
            if not value:
                continue
        if field not in result or result[field] is None or result[field] == "" or result[field] == []:
            result[field] = value
        elif isinstance(result[field], list) and isinstance(value, list):
            # Merge lists without duplicates (case-insensitive for strings)
            seen = {str(x).lower() for x in result[field]}
            for item in value:
                if str(item).lower() not in seen:
                    result[field].append(item)
                    seen.add(str(item).lower())
    return result


_EXPLICIT_SKILL_USAGE = re.compile(
    r"\b(?:i\s+(?:personally\s+)?(?:used|use|built|developed|implemented|created|wrote|worked\s+with)|"
    r"my\s+(?:work|role|project)\s+(?:used|uses|involved))\b",
    re.IGNORECASE,
)


def _existing_skills_explicitly_used(existing_skills: Any, statement: Any) -> list[str]:
    """Return existing skills named in a candidate's explicit usage statement.

    This intentionally does not infer use from a technology list or project
    description.  It also never returns a skill that was not already on the
    profile at the start of the interaction.
    """
    text_value = _normalize_profile_text(statement)
    if not text_value or not _EXPLICIT_SKILL_USAGE.search(text_value):
        return []
    supported: list[str] = []
    seen: set[str] = set()
    for skill in existing_skills if isinstance(existing_skills, list) else []:
        name = _normalize_profile_text(skill)
        key = _normalize_profile_key(name)
        if not name or not key or key in seen:
            continue
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text_value, re.IGNORECASE):
            supported.append(name)
            seen.add(key)
    return supported


def _append_demonstrated_skill_evidence(raw_data: dict, existing_skills: Any, statement: Any, source: str) -> dict:
    """Add idempotent direct-usage provenance, retaining only existing skills."""
    supported = _existing_skills_explicitly_used(existing_skills, statement)
    if not supported:
        return raw_data
    raw = dict(raw_data or {})
    records = raw.get("demonstrated_skill_evidence") or []
    records = [record for record in records if isinstance(record, dict)]
    key = (source, _normalize_profile_text(statement).lower(), tuple(sorted(_normalize_profile_key(s) for s in supported)))
    for record in records:
        record_key = (
            record.get("source"),
            _normalize_profile_text(record.get("statement")).lower(),
            tuple(sorted(_normalize_profile_key(s) for s in (record.get("skills") or []))),
        )
        if record_key == key:
            return raw
    raw["demonstrated_skill_evidence"] = records + [{
        "source": source,
        "statement": _normalize_profile_text(statement),
        "skills": supported,
    }]
    return raw


async def _record_demonstrated_skill_usage(candidate_id: str, existing_skills: Any, statement: Any, source: str) -> None:
    """Persist Chat usage evidence after structured profile updates are applied."""
    if not _existing_skills_explicitly_used(existing_skills, statement):
        return
    candidate = await _get_candidate_row(candidate_id)
    raw = _parse_raw_data(candidate.get("raw_data"))
    updated_raw = _append_demonstrated_skill_evidence(raw, existing_skills, statement, source)
    if updated_raw == raw:
        return
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE candidates SET raw_data = CAST(:raw_data AS jsonb), updated_at = now() WHERE id = :cid"),
            {"raw_data": json.dumps(updated_raw), "cid": candidate_id},
        )
        await db.commit()


async def _apply_profile_updates(candidate_id: str, updates: dict) -> dict:
    """Validate and apply structured profile updates to PostgreSQL, merging lists.
    Also handles profile_deletions to remove specific items from profile sections.
    """
    safe = {k: v for k, v in updates.items() if k in VALID_UPDATE_FIELDS and v is not None}
    if not safe:
        return {"updated": False, "deleted": {}}

    # Fetch existing candidate row for merging lists
    existing = await _get_candidate_row(candidate_id)
    existing_raw = _parse_raw_data(existing.get("raw_data"))

    set_clauses = []
    params: dict = {"cid": candidate_id}
    current_role_set = False
    raw_data_changed = False
    parsed_resume = _parse_raw_data(existing.get("parsed_resume_json"))
    parsed_resume_changed = False
    applied_deletions: dict[str, list[str]] = {}
    not_found_deletions: dict[str, list[str]] = {}
    has_preference_payload = False

    for field, value in safe.items():
        if field == "profile_record_replacements":
            if not isinstance(value, list):
                continue
            for replacement in value:
                if not isinstance(replacement, dict):
                    continue
                section, old, new = replacement.get("section"), replacement.get("old"), replacement.get("new")
                all_matches = replacement.get("all_matches") is True
                record_indexes = replacement.get("record_indexes")
                if not all(isinstance(part, str) and part.strip() for part in (section, old, new)):
                    continue
                # Re-query the persisted record at write time.  A stale chat turn
                # can never replace a value that is no longer uniquely targeted.
                matches = _profile_edit_matches(existing, old)
                if not matches.get(section) or not isinstance(record_indexes, list) or not record_indexes:
                    continue
                matching_indexes = {match["index"] for match in matches[section]}
                selected_indexes = set(record_indexes)
                if not selected_indexes.issubset(matching_indexes):
                    continue
                # 'Both' means every match in the selected section(s), never
                # an implicit fan-out to records the user did not select.
                if all_matches and selected_indexes != matching_indexes:
                    continue
                if section == "Education":
                    records = list(existing.get("education") or [])
                    for index in record_indexes:
                        record = dict(records[index])
                        for key, record_value in record.items():
                            if old.lower() in str(record_value).lower():
                                record[key] = re.sub(re.escape(old), new, str(record_value), flags=re.I)
                        records[index] = record
                    set_clauses.append("education = CAST(:education AS json)")
                    params["education"] = json.dumps(records)
                    existing["education"] = records
                elif section == "Work Experience":
                    records = list(existing.get("work_experience") or [])
                    for index in record_indexes:
                        record = dict(records[index])
                        for key, record_value in record.items():
                            if old.lower() in str(record_value).lower():
                                record[key] = re.sub(re.escape(old), new, str(record_value), flags=re.I)
                        records[index] = record
                    set_clauses.append("work_experience = CAST(:work_experience AS json)")
                    params["work_experience"] = json.dumps(records)
                    existing["work_experience"] = records
        elif field == "skills":
            if not isinstance(value, list):
                continue
            merged = _merge_skills(
                existing.get("skills") or [],
                value,
                certifications=existing_raw.get("certifications") or [],
            )
            if merged != (existing.get("skills") or []):
                set_clauses.append("skills = CAST(:skills AS json)")
                params["skills"] = json.dumps(merged)
        elif field == "work_experience":
            if not isinstance(value, list):
                continue
            # New records need a usable identity and timeline.  Updates to an
            # existing record are handled through profile_record_replacements.
            value = [item for item in value if isinstance(item, dict) and all(
                str(item.get(key) or "").strip() for key in ("title", "company")
            )]
            if not value:
                continue
            merged = _merge_work_experience(existing.get("work_experience") or [], value)
            if merged != (existing.get("work_experience") or []):
                set_clauses.append("work_experience = CAST(:work_experience AS json)")
                params["work_experience"] = json.dumps(merged)
        elif field == "education":
            if not isinstance(value, list):
                continue
            value = [item for item in value if isinstance(item, dict) and all(
                str(item.get(key) or "").strip() for key in ("degree", "institution")
            )]
            if not value:
                continue
            merged = _merge_education(existing.get("education") or [], value)
            if merged != (existing.get("education") or []):
                set_clauses.append("education = CAST(:education AS json)")
                params["education"] = json.dumps(merged)
        elif field in ("headline", "current_role"):
            existing_role = (existing.get("current_role") or existing.get("headline") or "")
            new_role = str(value)
            if not current_role_set and new_role != existing_role:
                set_clauses.append('"current_role" = :current_role')
                params["current_role"] = new_role
                current_role_set = True
        elif field == "bio":
            new_bio = str(value)
            if new_bio != (existing.get("summary") or ""):
                set_clauses.append("summary = :bio")
                params["bio"] = new_bio
        elif field == "experience_years":
            try:
                new_years = float(value)
                existing_years = existing.get("experience_years")
                existing_years = float(existing_years) if existing_years is not None else None
                if new_years != existing_years:
                    set_clauses.append("experience_years = :experience_years")
                    params["experience_years"] = new_years
            except (TypeError, ValueError):
                pass
        elif field == "preferred_roles":
            if not isinstance(value, list):
                continue
            has_preference_payload = True
            merged_roles = _normalize_preferred_roles(
                (existing_raw.get("preferred_roles") or []) + value
            )
            existing_roles = _normalize_preferred_roles(existing_raw.get("preferred_roles") or [])
            if merged_roles != existing_roles:
                existing_raw["preferred_roles"] = merged_roles
                raw_data_changed = True
        elif field in ("preferred_locations", "preferred_industries", "employment_types"):
            if not isinstance(value, list):
                continue
            has_preference_payload = True
            old_values = _normalize_preference_list(existing_raw.get(field) or [])
            replace = field == "preferred_locations" and safe.get("replace_preferred_locations") is True
            merged_values = _normalize_preference_list(value if replace else old_values + value)
            if merged_values != old_values:
                existing_raw[field] = merged_values
                raw_data_changed = True
        elif field in ("remote_preference", "open_to_opportunities", "willing_to_relocate"):
            if field == "remote_preference":
                if not isinstance(value, str) or not value.strip():
                    continue
                value = value.strip()
            elif not isinstance(value, bool):
                continue
            has_preference_payload = True
            if existing_raw.get(field) != value:
                existing_raw[field] = value
                raw_data_changed = True
        elif field == "certifications":
            if not isinstance(value, list):
                continue
            merged_certs = _merge_certifications(
                _candidate_certification_sources(existing),
                value,
            )
            existing_certs = _candidate_certification_sources(existing)
            if merged_certs != existing_certs:
                existing_raw["certifications"] = merged_certs
                raw_data_changed = True
        elif field in ("availability", "notice_period"):
            if not isinstance(value, str):
                continue
            has_preference_payload = True
            availability_value = _normalize_availability_value(value)
            if not availability_value:
                continue
            if existing_raw.get("notice_period") != availability_value:
                existing_raw["notice_period"] = availability_value
                raw_data_changed = True
        elif field in ("salary_expectation", "expected_salary"):
            if not isinstance(value, str):
                continue
            salary_value = value.strip()
            if not salary_value:
                continue
            has_preference_payload = True
            if existing_raw.get("expected_salary") != salary_value:
                existing_raw["expected_salary"] = salary_value
                raw_data_changed = True
        elif field == "additional_information":
            # This is free-form profile metadata, not a candidates table column.
            # Keep it in raw_data so it follows the same storage/read path as
            # the other supplemental profile fields.
            if not isinstance(value, str):
                continue
            additional_value = value.strip()
            if not additional_value:
                continue
            if existing_raw.get("additional_information") != additional_value:
                existing_raw["additional_information"] = additional_value
                raw_data_changed = True
        elif field == "profile_deletions":
            # Handled separately below; must not be added to SQL SET clauses.
            continue
        else:
            new_value = str(value)
            if field == "location":
                existing_value = existing.get("location") or ""
            else:
                existing_value = existing.get(field) or ""
            if new_value != existing_value:
                set_clauses.append(f"{field} = :{field}")
                params[field] = new_value

    # ---------- Handle profile_deletions ----------
    deletions = safe.get("profile_deletions")
    if isinstance(deletions, dict):
        for del_field, del_items in deletions.items():
            if not isinstance(del_items, list):
                continue
            for item in del_items:
                if not isinstance(item, str) or not item.strip():
                    continue
                # Backend safety belt: a conversational request may arrive
                # without a preflight turn (older clients/retries).  Never let
                # the fuzzy list removers delete more than one record.
                if del_field in ("work_experience", "education", "projects"):
                    section = {"work_experience": "Work Experience", "education": "Education", "projects": "Projects"}[del_field]
                    record_matches = _profile_edit_matches(existing, item).get(section, [])
                    if len(record_matches) != 1:
                        not_found_deletions.setdefault(del_field, []).append(item)
                        continue
                if del_field == "skills":
                    current = existing.get("skills") or []
                    new_list, found = _remove_item_from_list(current, item)
                    if found:
                        set_clauses.append("skills = CAST(:skills AS json)")
                        params["skills"] = json.dumps(new_list)
                        existing["skills"] = new_list  # keep in sync for subsequent iterations
                        applied_deletions.setdefault("skills", []).append(item)
                elif del_field == "certifications":
                    current = _candidate_certification_sources(existing)
                    raw_current = list(existing_raw.get("certifications") or [])
                    new_list, found = _remove_item_from_list(current, item)
                    raw_new, raw_found = _remove_item_from_list(raw_current, item)
                    parsed_current = list(parsed_resume.get("certifications") or [])
                    parsed_new, parsed_found = _remove_item_from_list(parsed_current, item)
                    # Certifications may have originated from either raw_data or the
                    # parsed-resume snapshot.  Remove from both; otherwise the stale
                    # snapshot is merged back into the profile on refresh.
                    if found or raw_found or parsed_found:
                        existing_raw["certifications"] = raw_new if raw_found else new_list
                        raw_data_changed = True
                        if parsed_found:
                            parsed_resume["certifications"] = parsed_new
                            parsed_resume_changed = True
                        applied_deletions.setdefault("certifications", []).append(item)
                elif del_field == "preferred_roles":
                    current = existing_raw.get("preferred_roles") or []
                    new_list, found = _remove_item_from_list(current, item)
                    if found:
                        existing_raw["preferred_roles"] = new_list
                        raw_data_changed = True
                        has_preference_payload = True
                        applied_deletions.setdefault("preferred_roles", []).append(item)
                elif del_field == "work_experience":
                    current = existing.get("work_experience") or []
                    new_list, found = _remove_item_from_dict_list(current, item, ["title", "company"])
                    if found:
                        set_clauses.append("work_experience = CAST(:work_experience AS json)")
                        params["work_experience"] = json.dumps(new_list)
                        existing["work_experience"] = new_list
                        applied_deletions.setdefault("work_experience", []).append(item)
                elif del_field == "education":
                    current = existing.get("education") or []
                    new_list, found = _remove_item_from_dict_list(current, item, ["degree", "institution"])
                    if found:
                        set_clauses.append("education = CAST(:education AS json)")
                        params["education"] = json.dumps(new_list)
                        existing["education"] = new_list
                        applied_deletions.setdefault("education", []).append(item)
                elif del_field == "projects":
                    current = existing_raw.get("projects") or []
                    if current and isinstance(current[0], dict):
                        new_list, found = _remove_item_from_dict_list(current, item, ["name", "title", "description"])
                    else:
                        new_list, found = _remove_item_from_list(current, item)
                    if found:
                        existing_raw["projects"] = new_list
                        raw_data_changed = True
                        applied_deletions.setdefault("projects", []).append(item)
                elif del_field == "preferred_locations":
                    current = existing_raw.get("preferred_locations") or existing_raw.get("location_preferences") or []
                    new_list, found = _remove_item_from_list(current, item)
                    if found:
                        existing_raw["preferred_locations"] = new_list
                        existing_raw["location_preferences"] = new_list
                        raw_data_changed = True
                        applied_deletions.setdefault("preferred_locations", []).append(item)
                elif del_field in ("headline", "current_role"):
                    if not current_role_set:
                        set_clauses.append('"current_role" = :current_role')
                        params["current_role"] = ""
                        current_role_set = True
                    applied_deletions.setdefault(del_field, []).append(item)
                elif del_field == "bio":
                    set_clauses.append("summary = :bio")
                    params["bio"] = ""
                    applied_deletions.setdefault("bio", []).append(item)
                elif del_field == "location":
                    set_clauses.append("location = :location")
                    params["location"] = ""
                    applied_deletions.setdefault("location", []).append(item)
                elif del_field == "salary_expectation":
                    existing_raw["salary_expectation"] = ""
                    raw_data_changed = True
                    applied_deletions.setdefault("salary_expectation", []).append(item)
                elif del_field in ("availability", "notice_period"):
                    existing_raw["availability"] = ""
                    raw_data_changed = True
                    has_preference_payload = True
                    applied_deletions.setdefault(del_field, []).append(item)
                elif del_field == "additional_information":
                    updated_text, found = _remove_phrase_from_text(
                        existing_raw.get("additional_information"), item
                    )
                    if found:
                        existing_raw["additional_information"] = updated_text
                        raw_data_changed = True
                        applied_deletions.setdefault("additional_information", []).append(item)
                    else:
                        # This field is explicitly scoped: do not fall back to
                        # certifications, skills, or any other profile section.
                        not_found_deletions.setdefault("additional_information", []).append(item)
                elif del_field == "experience_years":
                    set_clauses.append("experience_years = :experience_years")
                    params["experience_years"] = None
                    applied_deletions.setdefault("experience_years", []).append(item)
                elif del_field == "_unknown":
                    # Best-effort: try all list fields
                    for try_field, try_col, try_match in [
                        ("skills", "skills", None),
                        ("certifications", None, None),
                        ("preferred_roles", None, None),
                        ("projects", None, None),
                        ("preferred_locations", None, None),
                    ]:
                        if try_field == "skills":
                            current = existing.get("skills") or []
                            new_list, found = _remove_item_from_list(current, item)
                            if found:
                                set_clauses.append("skills = CAST(:skills AS json)")
                                params["skills"] = json.dumps(new_list)
                                existing["skills"] = new_list
                                break
                        elif try_field == "certifications":
                            current = _candidate_certification_sources(existing)
                            new_list, found = _remove_item_from_list(current, item)
                            if found:
                                existing_raw["certifications"] = new_list
                                raw_data_changed = True
                                parsed_current = list(parsed_resume.get("certifications") or [])
                                parsed_new, parsed_found = _remove_item_from_list(parsed_current, item)
                                if parsed_found:
                                    parsed_resume["certifications"] = parsed_new
                                    parsed_resume_changed = True
                                applied_deletions.setdefault("certifications", []).append(item)
                                break
                        elif try_field == "preferred_roles":
                            current = existing_raw.get("preferred_roles") or []
                            new_list, found = _remove_item_from_list(current, item)
                            if found:
                                existing_raw["preferred_roles"] = new_list
                                raw_data_changed = True
                                has_preference_payload = True
                                break
                        elif try_field == "projects":
                            current = existing_raw.get("projects") or []
                            if current and isinstance(current[0], dict):
                                new_list, found = _remove_item_from_dict_list(current, item, ["name", "title", "description"])
                            else:
                                new_list, found = _remove_item_from_list(current, item)
                            if found:
                                existing_raw["projects"] = new_list
                                raw_data_changed = True
                                break
                        elif try_field == "preferred_locations":
                            current = existing_raw.get("preferred_locations") or existing_raw.get("location_preferences") or []
                            new_list, found = _remove_item_from_list(current, item)
                            if found:
                                existing_raw["preferred_locations"] = new_list
                                existing_raw["location_preferences"] = new_list
                                raw_data_changed = True
                                break

    if raw_data_changed:
        set_clauses.append("raw_data = CAST(:raw_data AS jsonb)")
        params["raw_data"] = json.dumps(existing_raw)

    if parsed_resume_changed:
        set_clauses.append("parsed_resume_json = CAST(:parsed_resume_json AS jsonb)")
        params["parsed_resume_json"] = json.dumps(parsed_resume)

    if not set_clauses and not has_preference_payload:
        return {"updated": False, "deleted": {}, "not_found": not_found_deletions}

    persisted = False
    if set_clauses:
        set_clauses.append("updated_at = now()")
        set_clauses.append("updated_by_source = 'eve_chat'")

        async with SessionLocal() as db:
            await db.execute(
                text(f"UPDATE candidates SET {', '.join(set_clauses)} WHERE id = :cid"),
                params,
            )
            await db.commit()
        persisted = True

    preference_payload = {
        "preferred_roles": _normalize_preferred_roles(existing_raw.get("preferred_roles") or []),
        # Chat updates before the canonical-preferences migration legitimately
        # stored these values under the intake/resume aliases.  Include them in
        # the upsert so a later chat update backfills the authoritative row.
        "preferred_locations": (
            existing_raw.get("preferred_locations")
            or existing_raw.get("location_preferences")
            or []
        ),
        "preferred_industries": (
            existing_raw.get("preferred_industries")
            or existing_raw.get("target_industries")
            or []
        ),
        "employment_types": existing_raw.get("employment_types") or [],
        "remote_preference": existing_raw.get("remote_preference"),
        "notice_period": existing_raw.get("notice_period") or existing_raw.get("availability"),
        "expected_salary": existing_raw.get("expected_salary") or existing_raw.get("salary_expectation"),
        "willing_to_relocate": existing_raw.get("willing_to_relocate"),
    }
    if any(value not in (None, "", []) for value in preference_payload.values()):
        await _upsert_candidate_preferences(
            candidate_id,
            preference_payload,
        )

    return {
        "updated": persisted or has_preference_payload,
        "deleted": applied_deletions,
        "not_found": not_found_deletions,
    }


_JOB_SEARCH_PHRASES = (
    "find me jobs", "show me jobs", "get me jobs", "give me jobs",
    "show me matches", "find matches", "job matches", "matching jobs",
    "jobs that match", "match my profile", "roles that suit", "suitable roles",
    "what jobs", "any jobs", "search jobs", "look for jobs",
    "recommend jobs", "job recommendations", "show roles", "find roles",
)

_PREFERENCE_UPDATE_PHRASES = (
    "interested in", "looking for", "want to work", "want a", "want to be",
    "prefer", "mainly interested", "mostly interested", "focus on", "focused on",
    "switch to", "move into", "transition to", "targeting", "seeking",
)


def _is_job_search_request(text: str) -> bool:
    t = text.lower()
    return any(phrase in t for phrase in _JOB_SEARCH_PHRASES)


def _is_preference_update_with_job_search(text: str) -> bool:
    """Return True when the message updates a role preference AND requests job matches."""
    t = text.lower()
    has_preference = any(phrase in t for phrase in _PREFERENCE_UPDATE_PHRASES)
    has_job_search = _is_job_search_request(t) or any(
        w in t for w in ("show me", "matching", "matches", "jobs", "roles")
    )
    return has_preference and has_job_search


async def _extract_preferred_roles_from_message(message: str) -> list[str]:
    """Use LLM to extract preferred role(s) from a candidate preference statement."""
    resp = await openai_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": (
                "Extract the job role(s) the candidate wants to work in from the message. "
                "Return ONLY valid JSON: {\"preferred_roles\": [\"role1\", \"role2\"]}. "
                "Use concise role titles (e.g. 'Python Backend Developer'). "
                "Return an empty list if no clear role is mentioned."
            )},
            {"role": "user", "content": message},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    roles = data.get("preferred_roles") or []
    return [r for r in roles if isinstance(r, str) and r.strip()]


async def _update_preferred_roles(candidate_id: str, new_roles: list[str]) -> None:
    """Overwrite preferred_roles in candidate raw_data with the new list."""
    if not new_roles:
        return
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT raw_data FROM candidates WHERE id = :cid LIMIT 1"),
            {"cid": candidate_id},
        )
        result = row.fetchone()
    raw = {}
    if result and result[0]:
        try:
            raw = result[0] if isinstance(result[0], dict) else json.loads(result[0])
        except Exception:
            raw = {}
    raw["preferred_roles"] = new_roles
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE candidates SET raw_data = CAST(:rd AS jsonb), updated_at = now() WHERE id = :cid"),
            {"rd": json.dumps(raw), "cid": candidate_id},
        )
        await db.commit()
    logger.info("[chat] Updated preferred_roles for candidate %s: %s", candidate_id, new_roles)


def _format_jobs_for_context(jobs: list) -> str:
    if not jobs:
        return "No matching jobs found in the database at this time."
    lines = [f"Found {len(jobs)} matching job(s) from the database:\n"]
    for i, j in enumerate(jobs[:10], 1):  # cap at 10 for context length
        score = f"{j['match_score']:.0%}" if j.get("match_score") is not None else "N/A"
        lines.append(
            f"{i}. {j['title']} at {j['company']} — {j['location'] or 'Location not specified'}\n"
            f"   Salary: {j['salary'] or 'Not specified'} | Match: {score}\n"
            f"   {(j['description'] or '')[:200].strip()}"
        )
    return "\n".join(lines)


def _format_voice_intake_resume_context(resume: dict) -> str:
    lines = [
        f"- Status: {resume.get('status', 'in_progress')}",
        f"- Progress: {int(resume.get('progress') or 0)} completed question(s)",
    ]
    if resume.get("latest_completed_question"):
        lines.append(f"- Latest completed question: {resume['latest_completed_question']}")
    if resume.get("latest_completed_answer"):
        lines.append(f"- Latest completed answer: {resume['latest_completed_answer']}")
    if resume.get("current_question"):
        lines.append(f"- Current unanswered question: {resume['current_question']}")
    if resume.get("next_question"):
        lines.append(f"- Next question to ask: {resume['next_question']}")
    if resume.get("completed_turns"):
        turns = resume["completed_turns"][-3:]
        formatted = []
        for pair in turns:
            q = _clean_str(pair.get("question"))
            a = _clean_str(pair.get("answer"))
            if q and a:
                formatted.append(f"Q: {q} | A: {a}")
        if formatted:
            lines.append("- Completed turns: " + " || ".join(formatted))
    if resume.get("employment_gaps"):
        answered = []
        pending = []
        for gap in resume.get("employment_gaps") or []:
            if not isinstance(gap, dict):
                continue
            question = _clean_str(gap.get("question"))
            answer = _clean_str(gap.get("answer"))
            if answer:
                answered.append(f"{question} -> {answer}")
            elif question:
                pending.append(question)
        if answered:
            lines.append("- Employment gaps explained: " + " || ".join(answered[-2:]))
        if pending:
            lines.append("- Employment gap follow-up pending: " + " || ".join(pending[-2:]))
    return "\n".join(lines)


@api_router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages is empty")

    last_user = next((m for m in reversed(request.messages) if m.role == "user"), None)
    if not last_user:
        raise HTTPException(status_code=400, detail="No user message provided")

    # Build full candidate context
    profile_context = "No profile loaded yet."
    missing_fields: list = []
    persisted_window: list[dict] = []
    voice_resume: Optional[dict] = None
    frontend_profile: Optional[dict] = None
    candidate_row_for_edit: Optional[dict] = None
    missing_preference_fields: list[str] = []
    if request.candidate_id:
        try:
            row = await _get_candidate_row(request.candidate_id)
            candidate_row_for_edit = row
            raw_data = row.get("raw_data") or {}
            if isinstance(raw_data, str):
                try:
                    raw_data = json.loads(raw_data)
                except Exception:
                    raw_data = {}
            frontend_profile = _normalize_for_frontend(row)
            frontend_profile["raw_data"] = raw_data
            # candidate_preferences is authoritative for canonical work
            # preferences. Load it before generating either chat guidance or
            # the multi-field extraction target list.
            async with SessionLocal() as db:
                prefs_result = await db.execute(
                    text("SELECT * FROM candidate_preferences WHERE candidate_id = :cid LIMIT 1"),
                    {"cid": request.candidate_id},
                )
                prefs_row = prefs_result.mappings().fetchone()
            frontend_profile["_prefs_row"] = dict(prefs_row) if prefs_row else None
            profile_context, missing_fields = _build_profile_context(frontend_profile)
            missing_preference_fields = _missing_canonical_preference_fields(
                row, frontend_profile["_prefs_row"]
            )
            missing_fields.extend(
                _CANONICAL_PREFERENCE_LABELS[field]
                for field in missing_preference_fields
            )
            voice_resume = frontend_profile.get("voice_intake_resume") or _build_voice_intake_resume(frontend_profile)
            if voice_resume:
                profile_context += "\n\nVOICE INTAKE RESUME:\n" + _format_voice_intake_resume_context(voice_resume)
            persisted_window = await _load_chat_window(request.candidate_id)
        except HTTPException:
            pass

    # Resolve profile mutations before the LLM sees them.  The model is useful
    # for extraction, but it must never choose an ambiguous database target.
    incoming = [{"role": m.role, "content": m.content} for m in request.messages]
    if persisted_window and incoming:
        incoming_set = {(m["role"], m["content"]) for m in incoming}
        combined = [m for m in persisted_window if (m["role"], m["content"]) not in incoming_set] + incoming
    else:
        combined = incoming
    if request.candidate_id and candidate_row_for_edit:
        preflight = _chat_profile_preflight(last_user.content, candidate_row_for_edit, combined)
        if preflight:
            updates = preflight.get("updates")
            if updates:
                try:
                    apply_result = await _apply_profile_updates(request.candidate_id, updates)
                    if not apply_result.get("updated") or not await _verify_profile_update_persisted(request.candidate_id, updates):
                        return ChatResponse(reply="I couldn't update your profile, so no confirmation was made.", session_id=request.session_id)
                    asyncio.ensure_future(_trigger_matching(request.candidate_id))
                except Exception:
                    logger.exception("Profile preflight update failed")
                    return ChatResponse(reply="I couldn't update your profile right now, so no changes were made.", session_id=request.session_id)
            reply = preflight["reply"]
            try:
                await _save_chat_window(request.candidate_id, request.session_id, combined + [{"role": "assistant", "content": reply}])
            except Exception as e:
                logger.warning("Chat history persistence failed for candidate %s: %s", request.candidate_id, e)
            return ChatResponse(reply=reply, session_id=request.session_id, profile_updates=updates)

    # If the user is explicitly asking for job matches, retrieve real jobs first
    job_context = ""
    if request.candidate_id and _is_job_search_request(last_user.content):
        try:
            candidate_row = await _get_candidate_row(request.candidate_id)
            if not _voice_intake_completed_for_matching(candidate_row):
                job_context = (
                    "\n\nPersonalized job recommendations are unavailable until "
                    "you complete Voice Intake."
                )
                raise HTTPException(
                    status_code=409,
                    detail="Voice Intake must be completed before personalized recommendations are available.",
                )
            # Check if this is a preference update + job search — refresh matching with new prefs
            if _is_preference_update_with_job_search(last_user.content):
                new_roles = await _extract_preferred_roles_from_message(last_user.content)
                if new_roles:
                    await _update_preferred_roles(request.candidate_id, new_roles)
                    # Reload candidate row with updated preferences before matching
                    candidate_row = await _get_candidate_row(request.candidate_id)
                    from candidate_job_matching_service import refresh_candidate_job_matches
                    await refresh_candidate_job_matches(request.candidate_id, candidate_row, SessionLocal)
                    logger.info("[chat] Re-ran matching after preference update for candidate %s", request.candidate_id)
                else:
                    candidate_row = await _get_candidate_row(request.candidate_id)
            else:
                candidate_row = await _get_candidate_row(request.candidate_id)

            if await _effective_profile_strength_percent(request.candidate_id, candidate_row) < 90:
                job_context = "\n\nJob matches are unavailable until Profile Strength reaches 90%."
                raise HTTPException(status_code=403, detail="Profile Strength below job visibility threshold")

            # Ensure recommendations exist (runs matching if none yet)
            async with SessionLocal() as db:
                count_row = await db.execute(
                    text("SELECT COUNT(*) FROM candidate_job_recommendations WHERE candidate_id = :cid"),
                    {"cid": request.candidate_id},
                )
                rec_count = count_row.scalar() or 0
            if rec_count == 0:
                from candidate_job_matching_service import refresh_candidate_job_matches
                await refresh_candidate_job_matches(request.candidate_id, candidate_row, SessionLocal)
            # Fetch top recommendations joined with job details
            async with SessionLocal() as db:
                rows = await db.execute(
                    text("""
                        SELECT
                            cjr.match_score,
                            jd.title,
                            jd.company_name,
                            jd.location,
                            jd.salary_range,
                            jd.description
                        FROM candidate_job_recommendations cjr
                        LEFT JOIN job_descriptions jd ON jd.id = cjr.job_id
                        WHERE cjr.candidate_id = :cid
                          AND cjr.hidden_at IS NULL
                        ORDER BY cjr.recommendation_rank ASC NULLS LAST, cjr.match_score DESC NULLS LAST
                        LIMIT 10
                    """),
                    {"cid": request.candidate_id},
                )
                job_rows = rows.mappings().fetchall()
            jobs = [
                {
                    "title": r["title"] or "",
                    "company": r["company_name"] or "",
                    "location": r["location"] or "",
                    "salary": r["salary_range"] or "",
                    "description": r["description"] or "",
                    "match_score": float(r["match_score"]) if r["match_score"] is not None else None,
                }
                for r in job_rows
            ]
            job_context = "\n\nREAL JOB MATCHES FROM DATABASE:\n" + _format_jobs_for_context(jobs)
            logger.info("[chat] Injected %d real job matches for candidate %s", len(jobs), request.candidate_id)
        except HTTPException as e:
            if e.status_code not in (403, 409):
                logger.warning("[chat] Job retrieval failed for candidate %s: %s", request.candidate_id, e)
                job_context = "\n\nJob search attempted but no results could be retrieved at this time."
        except Exception as e:
            logger.warning("[chat] Job retrieval/matching failed for candidate %s: %s", request.candidate_id, e)
            job_context = "\n\nJob search attempted but no results could be retrieved at this time."

    system_prompt = EVE_SYSTEM_TEMPLATE.format(
        profile_context=profile_context + job_context,
        missing_fields=", ".join(missing_fields) if missing_fields else "None",
        profile_completion_guidance=_build_profile_completion_guidance(frontend_profile) if frontend_profile else "No profile loaded.",
    )

    # Build message list: use the incoming request messages as the source of truth for the
    # current conversation turn. The persisted window is only used to backfill history that
    # the frontend did not send (i.e. messages older than the current request window).
    messages = [{"role": "system", "content": system_prompt}] + combined[-CHAT_WINDOW_SIZE:]

    try:
        resp = await openai_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.7,
        )
        raw_reply = resp.choices[0].message.content or ""
    except Exception as e:
        logger.exception("LLM chat failure")
        raise HTTPException(status_code=502, detail=f"LLM error: {str(e)}")

    clean_reply, profile_updates = _extract_profile_updates(raw_reply, last_user.content)

    # Scan the candidate's answer against ALL missing fields, not just the one asked.
    # This ensures a single answer that covers multiple questions saves all of them.
    if request.candidate_id and (missing_fields or missing_preference_fields) and last_user.content.strip():
        # Skip [PROFILE_QUESTION] instructions — they are not candidate answers
        candidate_text = last_user.content
        if not candidate_text.startswith("[PROFILE_QUESTION]"):
            try:
                multi_updates = await _extract_multi_field_updates_from_answer(
                    candidate_text, missing_fields + missing_preference_fields
                )
                if multi_updates:
                    profile_updates = _merge_profile_updates(profile_updates or {}, multi_updates) or None
            except Exception as _e:
                logger.warning("[chat-multi-field] merge failed: %s", _e)

    # Apply profile updates to PostgreSQL
    if profile_updates and request.candidate_id:
        try:
            apply_result = await _apply_profile_updates(request.candidate_id, profile_updates)
            requested_deletions = profile_updates.get("profile_deletions") or {}
            applied_deletions = apply_result.get("deleted") or {}
            not_found_deletions = apply_result.get("not_found") or {}
            if not apply_result.get("updated") and not applied_deletions:
                clean_reply = "I couldn't update your profile, so no changes were made."
                profile_updates = None
            elif requested_deletions and not applied_deletions:
                # The LLM writes its reply before persistence. Do not tell the
                # candidate an item was removed unless the database changed.
                missing_additional = not_found_deletions.get("additional_information") or []
                if missing_additional:
                    clean_reply = (
                        "I couldn't find "
                        f"{', '.join(missing_additional)} in your Additional Information, "
                        "so no change was made."
                    )
                else:
                    clean_reply = "I couldn't find that item in your saved profile, so no change was made."
                profile_updates = None
            elif not_found_deletions.get("additional_information"):
                missing = ", ".join(not_found_deletions["additional_information"])
                clean_reply = (
                    f"{clean_reply.rstrip()} I couldn't find {missing} in your "
                    "Additional Information, so it was left unchanged."
                )
            asyncio.ensure_future(_trigger_matching(request.candidate_id))
        except Exception as e:
            logger.exception("Profile update failed: %s", e)
            clean_reply = "I couldn't update your profile right now, so no changes were made. Please try again."
            profile_updates = None

    # A direct candidate statement can demonstrate a skill already on their
    # profile even when it does not produce a normal profile field update.
    # Use the pre-turn profile so an LLM-extracted new skill cannot qualify.
    if request.candidate_id and frontend_profile:
        try:
            await _record_demonstrated_skill_usage(
                request.candidate_id,
                frontend_profile.get("skills") or frontend_profile.get("keySkills") or [],
                last_user.content,
                "eve_chat",
            )
        except Exception as e:
            logger.warning("Chat demonstrated-skill persistence failed: %s", e)

    if request.candidate_id:
        try:
            await _advance_voice_intake_from_chat(request.candidate_id, last_user.content)
        except Exception as e:
            logger.warning("Chat voice intake advance failed: %s", e)

    # Persist the exact completed turn synchronously.  Deferred saves could
    # finish out of order and let a later request load/replay a stale assistant
    # response as the current conversation state.
    if request.candidate_id:
        clean_combined = [
            m for m in combined
            if not (m.get("role") == "user" and m.get("content", "").startswith("[PROFILE_QUESTION]"))
        ]
        updated_window = clean_combined + [{"role": "assistant", "content": clean_reply}]
        try:
            await _save_chat_window(request.candidate_id, request.session_id, updated_window)
        except Exception as e:
            logger.warning("Chat history persistence failed for candidate %s: %s", request.candidate_id, e)

    return ChatResponse(
        reply=clean_reply,
        session_id=request.session_id,
        profile_updates=profile_updates,
    )


# ---------- Voice intake models ----------

class VoiceNote(BaseModel):
    role: str  # "assistant" | "user"
    text: str
    final: bool = True


class VoiceCandidateIntakeRequest(BaseModel):
    transcript: str
    voice_notes: Optional[List[VoiceNote]] = None
    candidate_id: str  # validated server-side against DB


class VoiceCandidateIntakeProgressRequest(BaseModel):
    transcript: Optional[str] = None
    voice_notes: Optional[List[VoiceNote]] = None
    candidate_id: str


# ---------- Voice intake migration (idempotent) ----------

CREATE_VOICE_INTAKES_TABLE = """
CREATE TABLE IF NOT EXISTS candidate_voice_intakes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id    UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    transcript      TEXT NOT NULL,
    voice_notes     JSONB,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
)
"""

CREATE_VOICE_INTAKES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_cvi_candidate ON candidate_voice_intakes(candidate_id)
"""

ALTER_CANDIDATE_JOB_RECS_ADD_REASON = """
ALTER TABLE candidate_job_recommendations
ADD COLUMN IF NOT EXISTS hidden_reason TEXT
"""

CREATE_DAILY_JOB_ACCESS_TABLE = """
CREATE TABLE IF NOT EXISTS candidate_daily_job_access (
    candidate_id       UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    recommendation_id  UUID NOT NULL REFERENCES candidate_job_recommendations(id) ON DELETE CASCADE,
    access_date        DATE NOT NULL,
    accessed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (candidate_id, recommendation_id, access_date)
)
"""

CREATE_DAILY_JOB_ACCESS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_cdja_candidate_date
ON candidate_daily_job_access (candidate_id, access_date)
"""

FREE_DAILY_RESUME_FIX_CREDITS = 10
INITIAL_RESUME_FIX_CREDITS = 100
RESUME_FIX_CREDIT_COST = 3

CREATE_RESUME_FIX_CREDITS_TABLE = """
CREATE TABLE IF NOT EXISTS candidate_resume_fix_credit_claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    usage_date DATE NOT NULL,
    credit_source TEXT NOT NULL DEFAULT 'daily',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at TIMESTAMPTZ
)
"""

CREATE_RESUME_FIX_CREDIT_BALANCES_TABLE = """
CREATE TABLE IF NOT EXISTS candidate_resume_fix_credit_balances (
    candidate_id UUID PRIMARY KEY REFERENCES candidates(id) ON DELETE CASCADE,
    starter_credits_remaining INTEGER NOT NULL DEFAULT 100,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (starter_credits_remaining >= 0)
)
"""

ALTER_RESUME_FIX_CREDITS_ADD_CONSUMED_AT = """
ALTER TABLE candidate_resume_fix_credit_claims
ADD COLUMN IF NOT EXISTS consumed_at TIMESTAMPTZ
"""

ALTER_RESUME_FIX_CREDITS_ADD_SOURCE = """
ALTER TABLE candidate_resume_fix_credit_claims
ADD COLUMN IF NOT EXISTS credit_source TEXT NOT NULL DEFAULT 'daily'
"""

CREATE_RESUME_FIX_CREDITS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_crfcc_candidate_date
ON candidate_resume_fix_credit_claims (candidate_id, usage_date)
"""

CREATE_APPLICATION_RESUMES_TABLE = """
CREATE TABLE IF NOT EXISTS candidate_application_resumes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    recommendation_id UUID NOT NULL REFERENCES candidate_job_recommendations(id) ON DELETE CASCADE,
    job_id UUID REFERENCES job_descriptions(id) ON DELETE SET NULL,
    company_name TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, recommendation_id)
)
"""

CREATE_APPLICATION_RESUMES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_car_candidate ON candidate_application_resumes (candidate_id, created_at DESC)
"""


async def _ensure_voice_intake_table():
    async with SessionLocal() as db:
        await db.execute(text(CREATE_VOICE_INTAKES_TABLE))
        await db.execute(text(CREATE_VOICE_INTAKES_INDEX))
        await db.commit()


async def _ensure_schema():
    async with SessionLocal() as db:
        await db.execute(text(CREATE_VOICE_INTAKES_TABLE))
        await db.execute(text(CREATE_VOICE_INTAKES_INDEX))
        await db.execute(text(ALTER_CANDIDATE_JOB_RECS_ADD_REASON))
        await db.execute(text(CREATE_DAILY_JOB_ACCESS_TABLE))
        await db.execute(text(CREATE_DAILY_JOB_ACCESS_INDEX))
        await db.execute(text(CREATE_RESUME_FIX_CREDITS_TABLE))
        await db.execute(text(CREATE_RESUME_FIX_CREDIT_BALANCES_TABLE))
        await db.execute(text(ALTER_RESUME_FIX_CREDITS_ADD_CONSUMED_AT))
        await db.execute(text(ALTER_RESUME_FIX_CREDITS_ADD_SOURCE))
        await db.execute(text(CREATE_RESUME_FIX_CREDITS_INDEX))
        await db.execute(text(CREATE_APPLICATION_RESUMES_TABLE))
        await db.execute(text(CREATE_APPLICATION_RESUMES_INDEX))
        await db.execute(text(CREATE_CHAT_SESSIONS_TABLE))
        await db.execute(text(CREATE_CHAT_SESSIONS_IDX))
        await db.commit()


@app.on_event("startup")
async def on_startup():
    await _ensure_schema()
    asyncio.ensure_future(_retry_worker())
    from app.job_ingestion.scheduler import start_scheduler
    start_scheduler()


@app.on_event("shutdown")
async def on_shutdown():
    from app.job_ingestion.scheduler import stop_scheduler
    stop_scheduler()

# ---------- Voice extraction prompt ----------

VOICE_EXTRACT_SYSTEM = """You are an expert recruiter assistant. Extract structured candidate information from this voice intake transcript.
Return ONLY valid JSON with these exact keys (omit keys where no information was provided):
{
  "summary": "",
  "role_preference_bio": "",
  "skills": [],
  "experience_years": null,
  "availability": "",
  "remote_preference": "",
  "preferred_industries": [],
  "employment_types": [],
  "preferred_locations": [],
  "expected_salary": "",
  "willing_to_relocate": null,
  "location": "",
  "preferred_roles": [],
  "current_role": "",
  "current_company": "",
  "work_experience": [{"title":"","company":"","start_date":"","end_date":"","description":""}],
  "projects": [{"title":"","description":"","technologies":[]}],
  "education": [{"degree":"","institution":""}],
  "certifications": [],
  "additional_information": "",
  "confidence": 0.0
}
Only include fields where the candidate actually provided information.
Do NOT invent or hallucinate information.
For "remote_preference", use exactly one of "Remote", "Hybrid", "On-site", or "Flexible" when stated. For "employment_types" use a list such as ["Full-time"] or ["Contract"]. "willing_to_relocate" must be true or false only when the candidate explicitly states it; otherwise omit it.
For work_experience start_date and end_date: extract the exact month and year the candidate states (e.g. "January 2025"). Use "Present" for end_date when the candidate says "to present", "currently", or "till now". Leave start_date/end_date empty only when the candidate did not mention dates.
For "role_preference_bio": if the candidate mentions the type of roles they are looking for or their career preferences, write a concise bio sentence capturing that preference (e.g. "Looking for Python Backend roles involving FastAPI and AI"). Do NOT include specific company names. Leave empty if no role preference was mentioned.
For "certifications": extract ALL certification names the candidate mentions anywhere in the transcript, even if mentioned incidentally (e.g. "I have AWS certification", "I am certified in PMP", "I hold a Google Cloud cert"). Each certification must be a separate string in the list. Do NOT omit certifications mentioned in passing.
For "education": a degree at/from a university or college is always Education. Capture the degree and institution; never place it in certifications unless the candidate also explicitly states a certification.
"current_role" must be an actual job title, never a skill, technology, database, company, or date range. "location" must be an actual geographic location or "Remote", never an education/employment date or timeline.
For "projects", extract only projects the candidate explicitly says they worked on or built. Preserve the stated project/product name, responsibilities, and technologies. Do not infer projects from general role duties, and do not create a title when none was stated.
Return only the JSON object."""


async def _extract_voice_info(transcript: str) -> dict:
    """Use LLM to extract structured candidate info from voice transcript."""
    try:
        resp = await openai_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": VOICE_EXTRACT_SYSTEM},
                {"role": "user", "content": transcript[:8000]},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        return _sanitize_profile_field_mapping(json.loads(raw))
    except Exception as e:
        logger.warning("Voice extraction LLM failed: %s", e)
        return {}


# ---------- Safe profile merge ----------

def _merge_list(existing: list, new_items: list) -> list:
    """Generic merge with case-insensitive dedup for strings; always append dicts."""
    if not new_items:
        return existing
    merged = list(existing)
    seen = {str(x).lower() for x in existing if not isinstance(x, dict)}
    for item in new_items:
        if isinstance(item, dict):
            merged.append(item)
        elif str(item).lower() not in seen:
            merged.append(item)
            seen.add(str(item).lower())
    return merged


_CERTIFICATION_BOILERPLATE_WORDS = {
    "cert",
    "certificate",
    "certificates",
    "certification",
    "certifications",
    "certified",
    "course",
    "courses",
    "credential",
    "credentials",
    "training",
}


def _normalize_profile_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _normalize_profile_key(value: Any) -> str:
    return _normalize_profile_text(value).lower()


def _certification_relaxed_key(value: Any) -> str:
    text = re.sub(r"[^\w\s]+", " ", _normalize_profile_key(value))
    tokens = [token for token in text.split() if token not in _CERTIFICATION_BOILERPLATE_WORDS]
    return " ".join(tokens) or text


def _looks_like_certification(value: Any) -> bool:
    text = _normalize_profile_key(value)
    return bool(
        re.search(
            r"\b(?:cert|certificate|certificates|certification|certifications|certified|course|courses|credential|credentials|training|license|licence)\b",
            text,
        )
    )


def _normalize_certifications(certifications: Any) -> list[str]:
    if not isinstance(certifications, list):
        return []
    normalized: list[str] = []
    seen_strict: set[str] = set()
    seen_relaxed: set[str] = set()
    for cert in certifications:
        cleaned = _normalize_profile_text(cert)
        if not cleaned:
            continue
        # Drop bare conversational filler words (e.g. "any", "yes", "some")
        if cleaned.lower() in _CONVERSATIONAL_FILLER_WORDS:
            continue
        strict_key = _normalize_profile_key(cleaned)
        relaxed_key = _certification_relaxed_key(cleaned)
        if strict_key in seen_strict or relaxed_key in seen_relaxed:
            continue
        seen_strict.add(strict_key)
        seen_relaxed.add(relaxed_key)
        normalized.append(cleaned)
    return normalized


def _candidate_certification_sources(candidate: dict, extra: Any = None) -> list[str]:
    """Collect certifications from all persisted candidate sources plus optional new values."""
    sources: list[Any] = []

    raw_data = _parse_raw_data(candidate.get("raw_data"))
    if isinstance(raw_data, dict):
        sources.extend(raw_data.get("certifications") or [])

    parsed_resume = _parse_raw_data(candidate.get("parsed_resume_json"))
    if isinstance(parsed_resume, dict):
        sources.extend(parsed_resume.get("certifications") or [])

    if isinstance(extra, list):
        sources.extend(extra)
    elif extra is not None:
        sources.append(extra)

    return _normalize_certifications(sources)


def _merge_certifications(existing: Any, new_items: Any) -> list[str]:
    """Merge certification lists with normalized de-duplication."""
    return _normalize_certifications([*(existing or []), *(new_items or [])])


def _skill_needs_certification_filter(skill_text: str, cert_text: str) -> bool:
    skill_key = _normalize_profile_key(skill_text)
    cert_key = _normalize_profile_key(cert_text)
    if skill_key == cert_key:
        return True

    skill_relaxed = _certification_relaxed_key(skill_text)
    cert_relaxed = _certification_relaxed_key(cert_text)
    if skill_relaxed != cert_relaxed:
        return False

    # Keep the filter conservative so we do not swallow unrelated skills like "Docker"
    # simply because a certificate is titled "Docker Course".
    return len(cert_relaxed.split()) >= 2


# This deliberately conservative vocabulary repairs historical values such as
# ``Node.jsFrontend Development`` without guessing at free-form skill phrases.
_CONCATENATED_SKILL_ALIASES = (
    ("Google Cloud Platform", "Google Cloud Platform"), ("Object-Oriented Programming", "Object-Oriented Programming"),
    ("Frontend Development", "Frontend Development"), ("Backend Development", "Backend Development"),
    ("Full Stack Development", "Full Stack Development"), ("AI Applications", "AI Applications"),
    ("Database Design", "Database Design"), ("Problem Solving", "Problem Solving"),
    ("Machine Learning", "Machine Learning"), ("Deep Learning", "Deep Learning"), ("Data Analysis", "Data Analysis"),
    ("Data Science", "Data Science"), ("Project Management", "Project Management"), ("Product Management", "Product Management"),
    ("Software Development", "Software Development"), ("React Native", "React Native"),
    ("React.js", "React.js"), ("React JS", "React.js"), ("Node.js", "Node.js"), ("Node JS", "Node.js"),
    ("TypeScript", "TypeScript"), ("JavaScript", "JavaScript"), ("PostgreSQL", "PostgreSQL"), ("MongoDB", "MongoDB"),
    ("Kubernetes", "Kubernetes"), ("FastAPI", "FastAPI"), ("Spring Boot", "Spring Boot"), ("REST APIs", "REST APIs"),
    ("GraphQL", "GraphQL"), ("Next.js", "Next.js"), ("HTML5", "HTML5"),
    ("Express.js", "Express.js"), ("ExpressJS", "Express.js"), ("Flask", "Flask"), ("CSS3", "CSS3"),
    ("Docker", "Docker"),
    ("Angular", "Angular"), ("Vue.js", "Vue.js"), ("Python", "Python"), ("Java", "Java"),
    ("SQL", "SQL"), ("C++", "C++"), ("C#", "C#"), ("AWS", "AWS"), ("Azure", "Azure"),
)
_CONCATENATED_SKILL_CANONICAL = {alias.casefold(): canonical for alias, canonical in _CONCATENATED_SKILL_ALIASES}
_CONCATENATED_SKILL_PATTERN = re.compile(
    "|".join(re.escape(alias) for alias, _ in sorted(_CONCATENATED_SKILL_ALIASES, key=lambda item: len(item[0]), reverse=True)),
    re.IGNORECASE,
)

# Spaces are meaningful inside skill names, but lost separators can be safely
# recovered around technical tokens (acronyms/versioned names and camel-cased
# product names). The surrounding words remain one phrase.
_LEGACY_TECHNICAL_SKILL_START = re.compile(
    r"(?<![A-Za-z0-9+#.])(?:[A-Z]{2,}\d*|[A-Z][A-Za-z]*[a-z][A-Z][A-Za-z0-9+#.]*)\b"
)


def _split_legacy_technical_boundaries(text_value: str) -> list[str]:
    """Recover lost legacy separators without treating ordinary spaces as delimiters."""
    starts = list(_LEGACY_TECHNICAL_SKILL_START.finditer(text_value))
    if len(starts) < 2:
        return [text_value]
    parts = [text_value[match.start():next_match.start()].strip() for match, next_match in zip(starts, starts[1:])]
    parts.append(text_value[starts[-1].start():].strip())
    recovered: list[str] = []
    for part in parts:
        # Keep a trailing title-cased phrase together (e.g. "Computer Vision")
        # while separating it from the preceding technical token.
        trailing_phrase = re.fullmatch(
            r"((?:[A-Z]{2,}\d*|[A-Z][A-Za-z]*[a-z][A-Z][A-Za-z0-9+#.]*))\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
            part,
        )
        if trailing_phrase:
            recovered.extend([trailing_phrase.group(1), trailing_phrase.group(2)])
        elif part:
            recovered.append(part)
    return recovered


def _split_skill_value(value: Any) -> list[str]:
    """Turn a skill value into individual skills without splitting phrases."""
    if isinstance(value, dict):
        value = value.get("name")
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [part for item in value for part in _split_skill_value(item)]
    text_value = _normalize_profile_text(value)
    if not text_value:
        return []
    # Preserve a complete, known multi-word alias before attempting structural
    # legacy repair (for example, ``Node JS`` is one skill, not two acronyms).
    if text_value.casefold() in _CONCATENATED_SKILL_CANONICAL:
        return [_CONCATENATED_SKILL_CANONICAL[text_value.casefold()]]
    # Slash is intentionally not a delimiter: it is meaningful in CI/CD.
    pieces = [piece.strip() for piece in re.split(r"[,;|\n\r\u2022]+", text_value) if piece.strip()]
    if len(pieces) != 1:
        return [part for piece in pieces for part in _split_skill_value(piece)]
    matches = list(_CONCATENATED_SKILL_PATTERN.finditer(text_value))
    # Split only a complete sequence of known skills. This supports values with
    # spaces or no delimiter while preserving unknown legitimate phrases.
    if len(matches) >= 2 and not _CONCATENATED_SKILL_PATTERN.sub("", text_value).strip():
        return [_CONCATENATED_SKILL_CANONICAL[match.group(0).casefold()] for match in matches]
    return _split_legacy_technical_boundaries(text_value)


def _normalize_skills(skills: Any, certifications: Any = None) -> list[str]:
    if not isinstance(skills, (list, tuple, set, str, dict)):
        return []

    normalized_certs = _normalize_certifications(certifications or [])
    normalized: list[str] = []
    seen: set[str] = set()

    for item in _split_skill_value(skills):
        cleaned = _normalize_profile_text(item)
        if not cleaned:
            continue
        # Apply the same canonical aliases to individually supplied values as
        # to values split from a legacy concatenated string.
        cleaned = _CONCATENATED_SKILL_CANONICAL.get(cleaned.casefold(), cleaned)

        key = _normalize_profile_key(cleaned)
        if key in seen:
            continue

        if any(
            _skill_needs_certification_filter(cleaned, cert)
            and (_looks_like_certification(cleaned) or _looks_like_certification(cert))
            for cert in normalized_certs
        ):
            continue

        seen.add(key)
        normalized.append(cleaned)

    return normalized


def _merge_skills(existing: Any, new_items: Any, certifications: Any = None) -> list[str]:
    """Merge skill lists with case-insensitive and whitespace-normalized deduplication."""
    return _normalize_skills([existing, new_items], certifications=certifications)


_EXPERIENCE_TITLE_STOPWORDS = {
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
}


def _experience_text_tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9+#.]+", _normalize_profile_key(value))
        if token and token not in _EXPERIENCE_TITLE_STOPWORDS
    }


def _experience_text_key(value: Any) -> str:
    return re.sub(r"[^\w\s+#.]+", " ", _normalize_profile_key(value))


def _experience_text_matches(existing: Any, new: Any) -> bool:
    existing_text = _normalize_profile_text(existing)
    new_text = _normalize_profile_text(new)
    if not existing_text or not new_text:
        return False

    existing_key = _experience_text_key(existing_text)
    new_key = _experience_text_key(new_text)
    if existing_key == new_key:
        return True
    if existing_key in new_key or new_key in existing_key:
        return True

    existing_tokens = _experience_text_tokens(existing_text)
    new_tokens = _experience_text_tokens(new_text)
    if not existing_tokens or not new_tokens:
        return False

    overlap = existing_tokens & new_tokens
    smallest = min(len(existing_tokens), len(new_tokens))
    largest = max(len(existing_tokens), len(new_tokens))
    return len(overlap) >= smallest and len(overlap) >= 2 or (len(overlap) >= 2 and len(overlap) / largest >= 0.66)


def _parse_experience_window(item: dict) -> tuple[Optional[int], Optional[int], bool]:
    start = _parse_experience_date(item.get("start_date") or item.get("startDate"), "start")
    end = _parse_experience_date(item.get("end_date") or item.get("endDate"), "end")
    open_ended = _is_open_ended_experience_value(item.get("end_date") or item.get("endDate"))

    dates_text = _normalize_experience_text(item.get("dates") or item.get("duration") or "")
    if dates_text:
        separator = re.search(r"\s+[\u2013\u2014-]\s+", dates_text)
        if separator:
            left, right = [part.strip() for part in re.split(r"\s+[\u2013\u2014-]\s+", dates_text, maxsplit=1)]
            if start is None:
                start = _parse_experience_date(left, "start")
            if right:
                if _is_open_ended_experience_value(right):
                    open_ended = True
                    end = None
                elif end is None:
                    end = _parse_experience_date(right, "end")
        else:
            if start is None:
                start = _parse_experience_date(dates_text, "start")
            if end is None and not open_ended:
                end = _parse_experience_date(dates_text, "end")
            if _is_open_ended_experience_value(dates_text):
                open_ended = True

    return start, end, open_ended


def _experience_entries_compatible(existing: dict, new_item: dict) -> bool:
    existing_title = _normalize_profile_text(existing.get("title") or existing.get("role") or "")
    new_title = _normalize_profile_text(new_item.get("title") or new_item.get("role") or "")
    existing_company = _normalize_profile_text(existing.get("company") or existing.get("company_name") or "")
    new_company = _normalize_profile_text(new_item.get("company") or new_item.get("company_name") or "")

    # A named employer is part of a job's identity.  Do not use title similarity to
    # merge two records with different companies: that can attach one employer's
    # resume description to a voice-reported job at another employer.
    if existing_company and new_company:
        ec_key = _normalize_profile_key(existing_company)
        nc_key = _normalize_profile_key(new_company)
        if ec_key != nc_key:
            return False
    if existing_title and new_title and not _experience_text_matches(existing_title, new_title):
        return False

    existing_start, existing_end, existing_open_ended = _parse_experience_window(existing)
    new_start, new_end, new_open_ended = _parse_experience_window(new_item)

    existing_has_dates = existing_start is not None or existing_end is not None or existing_open_ended
    new_has_dates = new_start is not None or new_end is not None or new_open_ended

    if not existing_has_dates or not new_has_dates:
        return True

    if existing_start is not None and existing_end is not None and new_start is not None and new_end is not None:
        return not (existing_end < new_start or new_end < existing_start)

    # Resume parsers and Voice Intake often report the same range at different
    # precision (for example, "2022 - 2024" versus "Jan 2022 - Jun 2024").
    # Do not require the normalized timestamps to be byte-for-byte equal when
    # the two complete ranges overlap.  For partial ranges, a conflicting known
    # boundary still represents a distinct stint at the same employer/role.
    if existing_start is not None and new_start is not None and existing_start != new_start:
        return False

    if existing_end is not None and new_end is not None and existing_end != new_end:
        return False

    return True


def _experience_match_score(existing: dict, new_item: dict) -> int:
    if not _experience_entries_compatible(existing, new_item):
        return -1

    score = 0
    existing_title = _normalize_profile_text(existing.get("title") or existing.get("role") or "")
    new_title = _normalize_profile_text(new_item.get("title") or new_item.get("role") or "")
    existing_company = _normalize_profile_text(existing.get("company") or existing.get("company_name") or "")
    new_company = _normalize_profile_text(new_item.get("company") or new_item.get("company_name") or "")

    if existing_company and new_company:
        if _experience_text_matches(existing_company, new_company):
            score += 5
        elif existing_company == new_company:
            score += 6

    if existing_title and new_title:
        if _experience_text_matches(existing_title, new_title):
            score += 5
        elif existing_title == new_title:
            score += 6

    existing_start, existing_end, existing_open_ended = _parse_experience_window(existing)
    new_start, new_end, new_open_ended = _parse_experience_window(new_item)
    if existing_start is not None and new_start is not None:
        if existing_start == new_start:
            score += 3
        else:
            score += 1
    if existing_end is not None and new_end is not None:
        if existing_end == new_end:
            score += 2
        else:
            score += 1
    if existing_open_ended and new_open_ended:
        score += 2
    if (existing_start is not None or existing_end is not None or existing_open_ended) and (
        new_start is not None or new_end is not None or new_open_ended
    ):
        score += 1

    return score


def _synthesized_experience_dates(entry: dict) -> str:
    start_label = _normalize_profile_text(entry.get("start_date") or entry.get("startDate"))
    if not start_label:
        return ""
    end_label = _normalize_profile_text(entry.get("end_date") or entry.get("endDate"))
    return " Ã¢â‚¬â€ ".join(filter(None, [start_label, end_label or "Present"]))


def _dedupe_experience_description(value: Any) -> str:
    """Normalize whitespace and retain each repeated description fragment once."""
    text_value = _normalize_profile_text(value)
    if not text_value:
        return ""

    deduped: list[str] = []
    seen: set[str] = set()
    # Accept both normal prose and parser/LLM concatenation without whitespace
    # after the sentence terminator.  Newline bullets are handled separately so
    # distinct responsibilities remain distinct while repeats are removed.
    for fragment in re.split(r"(?<=[.!?;])\s*|\n+|(?:^|\s)[•*-]\s+", text_value):
        cleaned = re.sub(r"\s+([.!?])", r"\1", _normalize_profile_text(fragment))
        key = re.sub(r"[^\w\s]+", " ", cleaned.lower())
        key = re.sub(r"\s+", " ", key).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
    return " ".join(deduped)


def _merge_experience_field(target: dict, source: dict, field: str) -> None:
    existing_value = _normalize_profile_text(target.get(field))
    new_value = _normalize_profile_text(source.get(field))
    if not new_value:
        return

    if field in {"start_date", "startDate"}:
        if existing_value and _parse_experience_date(existing_value, "start") is not None:
            return
        if _parse_experience_date(new_value, "start") is not None:
            target[field] = new_value
        return

    if field in {"end_date", "endDate"}:
        if existing_value:
            existing_is_present = _is_open_ended_experience_value(existing_value)
            new_is_present = _is_open_ended_experience_value(new_value)
            existing_parsed = _parse_experience_date(existing_value, "end")
            new_parsed = _parse_experience_date(new_value, "end")
            if existing_is_present and new_parsed is not None:
                target[field] = new_value
                return
            if existing_is_present and not new_is_present:
                return
            if existing_parsed is not None:
                return
        if _is_open_ended_experience_value(new_value) or _parse_experience_date(new_value, "end") is not None:
            target[field] = new_value
        return

    if field in {"dates", "duration"}:
        if not existing_value:
            start, end, open_ended = _parse_experience_window(source)
            if start is not None or end is not None or open_ended or new_value:
                target[field] = new_value
        return

    if not existing_value:
        target[field] = new_value
        return

    if field in {"title", "company"} and _experience_text_matches(existing_value, new_value):
        if len(new_value) > len(existing_value) and existing_value.lower() in new_value.lower():
            target[field] = new_value
        return

    if field in {"description", "summary"}:
        if _normalize_profile_key(existing_value) == _normalize_profile_key(new_value):
            target[field] = _dedupe_experience_description(existing_value)
            return
        target[field] = _dedupe_experience_description(f"{existing_value} {new_value}")


def _merge_work_experience(existing: list, new_items: list) -> list:
    """Canonicalize and merge work history without re-appending saved jobs."""
    # Process the persisted records as well as the incoming records through the
    # same matcher.  Previously this copied ``existing`` verbatim and only
    # deduplicated against ``new_items``; once a duplicate reached storage, later
    # Voice Intake/profile saves preserved it forever.
    merged: list[dict] = []
    for item in [*(existing or []), *(new_items or [])]:
        if not isinstance(item, dict):
            continue

        match_index = None
        best_score = -1
        for idx, existing_item in enumerate(merged):
            score = _experience_match_score(existing_item, item)
            if score > best_score:
                best_score = score
                match_index = idx
        if best_score < 0:
            match_index = None

        if match_index is None:
            next_item = dict(item)
            derived_dates = _synthesized_experience_dates(next_item)
            if derived_dates:
                next_item["dates"] = derived_dates
            merged.append(next_item)
            continue

        target = merged[match_index]
        for field in (
            "title",
            "company",
            "dates",
            "duration",
            "start_date",
            "startDate",
            "end_date",
            "endDate",
            "description",
            "summary",
            "location",
        ):
            _merge_experience_field(target, item, field)

        derived_dates = _synthesized_experience_dates(target)
        if derived_dates:
            target["dates"] = derived_dates

    for entry in merged:
        for field in ("description", "summary"):
            if field in entry:
                entry[field] = _dedupe_experience_description(entry[field])
    return merged


def _normalize_projects(items: Any) -> list[dict]:
    """Keep explicitly supplied project records in one durable profile shape.

    This deliberately does not mine ordinary responsibility prose: a project is
    promoted only when an extractor or existing structured payload supplied a
    title/name.  That prevents manufacturing project evidence from a job title.
    """
    if not isinstance(items, list):
        return []
    normalized: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            title = _normalize_profile_text(item)
            record = {"title": title} if title else {}
        elif isinstance(item, dict):
            title = _normalize_profile_text(item.get("title") or item.get("name") or item.get("project_name"))
            description = _normalize_profile_text(item.get("description") or item.get("summary") or item.get("responsibilities"))
            technologies = item.get("technologies") or item.get("skills") or []
            if isinstance(technologies, str):
                technologies = [technologies]
            technologies = [
                _normalize_profile_text(value) for value in technologies
                if _normalize_profile_text(value)
            ] if isinstance(technologies, list) else []
            record = {"title": title} if title else {}
            if description:
                record["description"] = description
            if technologies:
                record["technologies"] = list(dict.fromkeys(technologies))
        else:
            continue
        title = record.get("title", "")
        if not title:
            continue
        key = _normalize_profile_key(title)
        if key in seen:
            existing = next(p for p in normalized if _normalize_profile_key(p.get("title")) == key)
            if not existing.get("description") and record.get("description"):
                existing["description"] = record["description"]
            existing_tech = existing.get("technologies") or []
            incoming_tech = record.get("technologies") or []
            if incoming_tech:
                existing["technologies"] = list(dict.fromkeys([*existing_tech, *incoming_tech]))
            continue
        seen.add(key)
        normalized.append(record)
    return normalized


def _merge_projects(existing: Any, incoming: Any) -> list[dict]:
    """Merge candidate-provided project records without duplicating titles."""
    return _normalize_projects([*_normalize_projects(existing), *_normalize_projects(incoming)])


def _merge_education(existing: list, new_items: list) -> list:
    """Merge education lists, deduplicating by normalized degree + institution."""
    if not new_items:
        return existing

    def _education_key(entry: dict) -> str:
        degree = _normalize_profile_key(entry.get("degree") or entry.get("field_of_study") or "")
        institution = _normalize_profile_key(entry.get("institution") or entry.get("school") or "")
        return f"{institution}|{degree}"

    def _merge_education_entry(target: dict, source: dict) -> dict:
        merged_entry = dict(target)
        for field in ("degree", "institution", "dates", "duration", "start_date", "end_date", "field_of_study", "location", "description"):
            existing_value = _normalize_profile_text(merged_entry.get(field))
            new_value = _normalize_profile_text(source.get(field))
            if not existing_value and new_value:
                merged_entry[field] = new_value
        return merged_entry

    merged = [dict(e) for e in existing if isinstance(e, dict)]
    index_by_key: dict[str, int] = {}
    for idx, entry in enumerate(merged):
        index_by_key[_education_key(entry)] = idx

    for item in new_items:
        if not isinstance(item, dict):
            continue
        key = _education_key(item)
        if key in index_by_key:
            idx = index_by_key[key]
            merged[idx] = _merge_education_entry(merged[idx], item)
            continue
        merged.append(dict(item))
        index_by_key[key] = len(merged) - 1

    return merged


def _is_more_specific_role(existing_role: str, voice_role: str) -> bool:
    """
    Return True if voice_role appears to be a more specific version of existing_role.
    Heuristic: voice role is longer and contains the existing role words.
    """
    if not voice_role or not existing_role:
        return False
    existing_words = set(existing_role.lower().split())
    voice_words = set(voice_role.lower().split())
    # Voice role must contain all words from existing role and add at least one more
    return existing_words.issubset(voice_words) and len(voice_words) > len(existing_words)


def _format_years_of_experience(value: Any) -> str:
    try:
        years = float(value)
    except (TypeError, ValueError):
        return ""
    if years.is_integer():
        return f"{int(years)} years of experience"
    return f"{years:g} years of experience"


def _summary_natural_join(items: list[str]) -> str:
    cleaned = [_normalize_profile_text(item) for item in items if _normalize_profile_text(item)]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def _summary_clause_tokens(text: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9+#.]+", _normalize_profile_key(text))
        if token and token not in {
            "a",
            "an",
            "and",
            "as",
            "for",
            "in",
            "is",
            "looking",
            "of",
            "on",
            "open",
            "roles",
            "the",
            "to",
            "with",
            "within",
            "work",
            "role",
            "roles",
            "target",
            "targeting",
        }
    }
    return tokens


def _summary_clause_is_redundant(existing_clauses: list[str], clause: str) -> bool:
    cleaned = _normalize_profile_text(clause).rstrip(".")
    if not cleaned:
        return True
    clause_key = _normalize_profile_key(cleaned)
    clause_tokens = _summary_clause_tokens(cleaned)
    for existing in existing_clauses:
        existing_key = _normalize_profile_key(existing)
        if not existing_key:
            continue
        if clause_key == existing_key or clause_key in existing_key or existing_key in clause_key:
            return True
        existing_tokens = _summary_clause_tokens(existing)
        if clause_tokens and existing_tokens:
            overlap = clause_tokens & existing_tokens
            smaller = min(len(clause_tokens), len(existing_tokens))
            if smaller and len(overlap) >= max(2, smaller - 1):
                return True
    return False


def _append_summary_clause(existing_clauses: list[str], clause: str, seen: set[str]) -> None:
    cleaned = _normalize_profile_text(clause).rstrip(".")
    if not cleaned:
        return
    key = _normalize_profile_key(cleaned)
    if not key or key in seen or _summary_clause_is_redundant(existing_clauses, cleaned):
        return
    existing_clauses.append(cleaned)
    seen.add(key)


def _sanitize_summary_role(value: Any) -> str:
    text = _normalize_profile_text(value)
    if not text:
        return ""
    text = re.sub(r"\s+(?:at|@)\s+[^,;|]+$", "", text, flags=re.I)
    text = re.sub(r"\s*\([^)]*\)$", "", text).strip(" -|,")
    return text.strip()


def _sanitize_summary_focus(value: Any) -> str:
    text = _normalize_profile_text(value)
    if not text:
        return ""
    text = re.sub(
        r"\b\d+(?:\.\d+)?\s*(?:\+)?\s*(?:years?|months?)\b(?:\s+of\s+experience)?",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\s{2,}", " ", text).strip(" ,;:-")
    return text


def _summary_skills(existing_skills: Any, voice_skills: Any = None, limit: int = 4) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for source in (existing_skills, voice_skills or []):
        if not isinstance(source, list):
            continue
        for skill in source:
            cleaned = _normalize_profile_text(skill)
            if not cleaned:
                continue
            key = _normalize_profile_key(cleaned)
            if key in seen:
                continue
            seen.add(key)
            selected.append(cleaned)
            if len(selected) >= limit:
                return selected
    return selected


def _preferred_roles_phrase(roles: Any) -> str:
    normalized = _normalize_preferred_roles(roles)
    if not normalized:
        return ""
    return _summary_natural_join(normalized)


def _build_merged_candidate_summary(existing: dict, voice: dict) -> str:
    """
    Build a concise summary from the merged profile so the final summary keeps
    resume information and appends newly learned Voice Intake details.
    """
    clauses: list[str] = []
    seen: set[str] = set()

    current_role = _sanitize_summary_role(existing.get("current_role") or existing.get("headline"))
    voice_current_role = _sanitize_summary_role(voice.get("current_role"))
    voice_current_company = _normalize_profile_text(voice.get("current_company"))
    has_explicit_voice_current_job = bool(voice_current_role and voice_current_company)
    profile_skills = _summary_skills(existing.get("skills") or [], voice.get("skills") or [])
    intro_bits: list[str] = []
    if current_role:
        if has_explicit_voice_current_job:
            intro_bits.append(f"{current_role} at {voice_current_company}")
        else:
            intro_bits.append(current_role)
    if has_explicit_voice_current_job:
        years_of_experience = _format_years_of_experience(existing.get("experience_years"))
        if years_of_experience:
            intro_bits.append(f"with {years_of_experience}")
    if profile_skills:
        if current_role:
            intro_bits.append(f"with strengths in {_summary_natural_join(profile_skills)}")
        else:
            intro_bits.append(f"Strengths include {_summary_natural_join(profile_skills)}")
    if intro_bits:
        _append_summary_clause(clauses, " ".join(intro_bits), seen)

    # When Voice Intake explicitly identifies a current employer, retain a concise
    # historical role from the resume without blending its details into that job.
    if has_explicit_voice_current_job:
        current_job_key = (
            _normalize_profile_key(current_role),
            _normalize_profile_key(voice_current_company),
        )
        for entry in existing.get("work_experience") or []:
            if not isinstance(entry, dict):
                continue
            title = _sanitize_summary_role(entry.get("title") or entry.get("role"))
            company = _normalize_profile_text(entry.get("company") or entry.get("company_name"))
            if not title or not company:
                continue
            if (_normalize_profile_key(title), _normalize_profile_key(company)) == current_job_key:
                continue
            _append_summary_clause(clauses, f"Previous experience includes {title} at {company}", seen)
            break

    raw_data = _parse_raw_data(existing.get("raw_data"))
    voice_focus = _sanitize_summary_focus(
        voice.get("role_preference_bio")
        or voice.get("summary")
        or voice.get("additional_information")
        or raw_data.get("additional_information")
    )
    preferred_roles_source = voice.get("preferred_roles") or raw_data.get("preferred_roles") or []
    preferred_roles = _preferred_roles_phrase(preferred_roles_source)
    if voice_focus:
        _append_summary_clause(clauses, voice_focus, seen)
    if preferred_roles:
        _append_summary_clause(clauses, f"Looking for {preferred_roles} roles", seen)

    if not clauses:
        return ""

    return ". ".join(clauses).strip() + "."


def _merge_voice_into_profile(existing: dict, voice: dict) -> dict:
    """
    Safely merge voice-extracted data into existing candidate profile.
    - Fills missing fields from voice
    - Merges skills (deduped), work_experience (deduped by company+title), education (deduped)
    - Treats an explicitly stated voice current role and company as authoritative
    - Stores availability, preferred_roles, certifications, additional_information in raw_data
    """
    merged = dict(existing)
    voice = _sanitize_profile_field_mapping(voice)

    # Invalid legacy scalar values should not block a later, valid voice
    # answer from filling the field. Do not alter valid existing data.
    if merged.get("current_role") and not _is_actual_job_role(merged.get("current_role")):
        merged["current_role"] = ""
    if merged.get("location") and not _is_actual_location(merged.get("location")):
        merged["location"] = ""

    # An explicit current role/company pair from Voice Intake describes the
    # candidate's latest employment, so it takes precedence over resume scalars.
    # This is deliberately limited to the pair: other profile fields retain their
    # existing merge behavior.
    voice_role = (voice.get("current_role") or "").strip()
    voice_company = (voice.get("current_company") or "").strip()
    existing_role = (merged.get("current_role") or "").strip()
    if voice_role and voice_company:
        merged["current_role"] = voice_role
        merged["current_company"] = voice_company
    else:
        # Fill missing scalar fields when Voice Intake does not supply an explicit
        # current-job pair.
        for key in ("current_company", "location"):
            if voice.get(key) and not merged.get(key):
                merged[key] = voice[key]

    # Without a company, retain the conservative historical role behavior.
    if voice_role and not voice_company:
        if not existing_role:
            merged["current_role"] = voice_role
        elif _is_more_specific_role(existing_role, voice_role):
            merged["current_role"] = voice_role

    # Location is independent from current employment and can still fill a gap.
    if voice.get("location") and not merged.get("location"):
        merged["location"] = voice["location"]

    # experience_years: fill if missing
    if voice.get("experience_years") and not merged.get("experience_years"):
        try:
            merged["experience_years"] = float(voice["experience_years"])
        except (TypeError, ValueError):
            pass

    # Store voice-only fields in raw_data (no new DB columns needed)
    existing_raw = merged.get("raw_data") or {}
    if isinstance(existing_raw, str):
        try:
            existing_raw = json.loads(existing_raw)
        except Exception:
            existing_raw = {}
    raw_data = dict(existing_raw)

    if voice.get("availability"):
        raw_data["availability"] = _normalize_availability_value(voice["availability"]) or str(voice["availability"]).strip()
    if voice.get("salary_expectation"):
        raw_data["salary_expectation"] = str(voice["salary_expectation"]).strip()
    # Keep each stated preference in raw_data as a durable fallback for older
    # candidates, while _upsert_candidate_preferences writes the canonical row.
    preference_fields = (
        "remote_preference", "preferred_industries", "employment_types",
        "preferred_locations", "expected_salary", "willing_to_relocate",
    )
    for field in preference_fields:
        value = voice.get(field)
        if value is not None and value != "" and value != []:
            raw_data[field] = value
    if voice.get("preferred_roles"):
        existing_pr = raw_data.get("preferred_roles") or []
        seen_pr = {r.lower() for r in existing_pr}
        for r in voice["preferred_roles"]:
            if r.lower() not in seen_pr:
                existing_pr.append(r)
                seen_pr.add(r.lower())
        raw_data["preferred_roles"] = existing_pr
    raw_data["certifications"] = _candidate_certification_sources(
        merged,
        voice.get("certifications") or [],
    )
    if voice.get("additional_information"):
        raw_data["additional_information"] = voice["additional_information"]
    if raw_data.get("projects") or voice.get("projects"):
        raw_data["projects"] = _merge_projects(
            raw_data.get("projects"), voice.get("projects")
        )

    # Merge lists after raw_data is normalized so certifications can be excluded
    # from the skill list when they are clearly certifications.
    merged["skills"] = _merge_skills(
        merged.get("skills") or [],
        voice.get("skills") or [],
        certifications=raw_data.get("certifications") or [],
    )
    merged["work_experience"] = _merge_work_experience(
        merged.get("work_experience") or [], voice.get("work_experience") or []
    )
    merged["education"] = _merge_education(
        merged.get("education") or [], voice.get("education") or []
    )

    merged["raw_data"] = raw_data
    merged["summary"] = _build_merged_candidate_summary(merged, voice)
    return merged


def _normalize_preferred_roles(roles: Any) -> list[str]:
    """Normalize role preference strings for idempotent persistence."""
    if not isinstance(roles, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for role in roles:
        if not isinstance(role, str):
            continue
        cleaned = role.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(cleaned)
    return normalized


def _normalize_preference_list(values: Any) -> list[str]:
    """Normalize a preference list without manufacturing a preference."""
    if isinstance(values, str):
        values = [values]
    return _normalize_preferred_roles(values)


def _availability_to_notice_period(availability: Any) -> str:
    """Map the extracted availability answer onto the preferences schema."""
    if not isinstance(availability, str):
        return ""
    return availability.strip()


async def _upsert_candidate_preferences(candidate_id: str, voice: dict) -> None:
    """
    Persist voice-derived preferences into the existing candidate_preferences table.

    This keeps the canonical preferences row aligned with the profile payload
    while remaining idempotent across repeated cumulative submissions.
    """
    preferred_roles = _normalize_preferred_roles(voice.get("preferred_roles"))
    preferred_locations = _normalize_preference_list(voice.get("preferred_locations"))
    preferred_industries = _normalize_preference_list(voice.get("preferred_industries"))
    employment_types = _normalize_preference_list(voice.get("employment_types"))
    remote_preference = _clean_str(voice.get("remote_preference"))
    expected_salary = _clean_str(voice.get("expected_salary") or voice.get("salary_expectation"))
    willing_to_relocate = voice.get("willing_to_relocate")
    notice_period = _availability_to_notice_period(voice.get("notice_period") or voice.get("availability"))
    if not any((preferred_roles, preferred_locations, preferred_industries,
                employment_types, remote_preference, expected_salary,
                notice_period, willing_to_relocate is not None)):
        return

    async with SessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT id, preferred_roles, preferred_locations, preferred_industries, "
                "employment_types, remote_preference, expected_salary, willing_to_relocate, notice_period FROM candidate_preferences "
                "WHERE candidate_id = :cid LIMIT 1"
            ),
            {"cid": candidate_id},
        )
        existing = row.mappings().fetchone()

    if existing:
        existing_roles = _normalize_preferred_roles(existing.get("preferred_roles") or [])
        merged_roles = _normalize_preferred_roles(existing_roles + preferred_roles)
        list_fields = {
            "preferred_locations": preferred_locations,
            "preferred_industries": preferred_industries,
            "employment_types": employment_types,
        }
        merged_notice_period = notice_period or (existing.get("notice_period") or "")

        set_parts = []
        params: dict[str, Any] = {"cid": candidate_id}
        if merged_roles != existing_roles:
            set_parts.append("preferred_roles = CAST(:preferred_roles AS jsonb)")
            params["preferred_roles"] = json.dumps(merged_roles)
        if merged_notice_period != (existing.get("notice_period") or ""):
            set_parts.append("notice_period = :notice_period")
            params["notice_period"] = merged_notice_period
        for field, incoming in list_fields.items():
            old = _normalize_preference_list(existing.get(field) or [])
            combined = _normalize_preference_list(old + incoming)
            if combined != old:
                set_parts.append(f"{field} = CAST(:{field} AS jsonb)")
                params[field] = json.dumps(combined)
        for field, incoming in (("remote_preference", remote_preference), ("expected_salary", expected_salary)):
            if incoming and incoming != (existing.get(field) or ""):
                set_parts.append(f"{field} = :{field}")
                params[field] = incoming
        if willing_to_relocate is not None and willing_to_relocate != existing.get("willing_to_relocate"):
            set_parts.append("willing_to_relocate = :willing_to_relocate")
            params["willing_to_relocate"] = bool(willing_to_relocate)
        if not set_parts:
            return
        set_parts.append("updated_at = now()")
        async with SessionLocal() as db:
            await db.execute(
                text(f"UPDATE candidate_preferences SET {', '.join(set_parts)} WHERE candidate_id = :cid"),
                params,
            )
            await db.commit()
        return

    insert_params = {
        "id": str(uuid.uuid4()),
        "cid": candidate_id,
        "preferred_roles": json.dumps(preferred_roles),
        "preferred_locations": json.dumps(preferred_locations),
        "preferred_industries": json.dumps(preferred_industries),
        "employment_types": json.dumps(employment_types),
        "remote_preference": remote_preference or None,
        "expected_salary": expected_salary or None,
        "willing_to_relocate": willing_to_relocate,
        "notice_period": notice_period or None,
    }
    async with SessionLocal() as db:
        await db.execute(
            text("""
                INSERT INTO candidate_preferences
                    (id, candidate_id, preferred_roles, preferred_locations, preferred_industries,
                     employment_types, remote_preference, expected_salary, willing_to_relocate,
                     notice_period, created_at, updated_at)
                VALUES
                    (:id, :cid, CAST(:preferred_roles AS jsonb), CAST(:preferred_locations AS jsonb),
                     CAST(:preferred_industries AS jsonb), CAST(:employment_types AS jsonb),
                     :remote_preference, :expected_salary, :willing_to_relocate, :notice_period, now(), now())
            """),
            insert_params,
        )
        await db.commit()


async def _persist_voice_intake_profile_state(
    candidate_id: str,
    candidate: dict,
    voice_data: dict,
    voice_intake_state: dict,
) -> dict:
    """
    Persist the canonical profile, preferences, and voice intake resume in sync.

    The same helper is used by both the final intake endpoint and the progress
    endpoint so repeated cumulative submissions remain idempotent.
    """
    voice_data = _sanitize_profile_field_mapping(voice_data)
    merged = _merge_voice_into_profile(candidate, voice_data)

    update_params: dict[str, Any] = {"cid": candidate_id}
    set_clauses: list[str] = []

    for field, col in [
        ("summary", "summary"),
        ("current_role", '"current_role"'),
        ("current_company", "current_company"),
        ("location", "location"),
    ]:
        if merged.get(field) != candidate.get(field):
            set_clauses.append(f"{col} = :{field}")
            update_params[field] = merged[field]

    if merged.get("experience_years") != candidate.get("experience_years"):
        set_clauses.append("experience_years = :experience_years")
        update_params["experience_years"] = merged.get("experience_years")

    for field in ("skills", "work_experience", "education"):
        if merged.get(field) != candidate.get(field):
            set_clauses.append(f"{field} = CAST(:{field} AS json)")
            update_params[field] = json.dumps(merged.get(field) or [])

    merged_raw = merged.get("raw_data") or {}
    # The canonical profile intentionally merges resume and intake fields.
    # Save the intake extraction separately so the onboarding recap can remain
    # grounded only in what the candidate said, including after a refresh.
    merged_raw["voice_intake_summary"] = {
        **(voice_data if isinstance(voice_data, dict) else {}),
        "voice_intake_resume": voice_intake_state,
    }
    merged_raw["voice_intake"] = voice_intake_state
    # Store only direct candidate assertions of having used a skill that was
    # already present before this intake.  The extracted voice skills and
    # project technology lists remain claimed/corroborated evidence.
    voice_usage_text = "\n".join(
        _normalize_profile_text(turn.get("answer"))
        for turn in (voice_intake_state.get("completed_turns") or [])
        if isinstance(turn, dict)
    )


def _updated_resume_pdf_path(candidate_id: str) -> Path:
    """Return the candidate-owned path for the Resume Editor PDF artifact."""
    return (_candidate_storage_dir(candidate_id) / "updated_resume.pdf").resolve()


def _application_resume_filename(company_name: str, candidate_name: str) -> str:
    """Human-readable, filesystem-safe name for a company-specific resume."""
    def part(value: str) -> str:
        value = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
        return value or "unknown"
    return f"{part(company_name)}_{part(candidate_name)}.pdf"


def _application_resume_storage_key(recommendation_id: str, filename: str) -> str:
    """Return the DB value, relative to the candidate document root."""
    return f"application_resumes/{recommendation_id}/{filename}"


def _resolve_application_resume_storage_key(candidate_id: str, storage_key: Any) -> Optional[Path]:
    """Resolve a canonical, slash-delimited relative application-resume key."""
    if not isinstance(storage_key, str) or not storage_key.strip():
        return None
    if storage_key.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\\\/]", storage_key):
        return None
    # Path() treats a Windows separator as a normal character on Linux, so
    # normalize both styles before applying the candidate-volume boundary.
    parts = [part for part in re.split(r"[\\\\/]+", storage_key.strip()) if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    # New rows store a candidate-relative canonical key.  Old rows were
    # relative directly to the application-resume volume; retain that read
    # compatibility without permitting a path outside the candidate volume.
    if parts[0] == "application_resumes":
        parts = parts[1:]
    if not parts:
        return None
    root = (_candidate_storage_dir(candidate_id) / "application_resumes").resolve()
    path = (root.joinpath(*parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _resolve_application_resume_path(
    candidate_id: str, storage_key: Any, *, recommendation_id: Optional[str] = None,
    resume_id: Optional[str] = None, filename: Optional[str] = None,
    recover_legacy: bool = False, diagnostics: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    """Resolve the one candidate/job-specific application-resume location.

    New rows use ``<recommendation id>/<display filename>``.  That makes the
    DB reference portable and prevents two applications with equal display
    filenames from sharing a file.  Legacy deployments used ``<rec>.pdf`` or
    stale absolute paths, so View can recover only those candidate-owned,
    deterministic alternatives.
    """
    root = (_candidate_storage_dir(candidate_id) / "application_resumes").resolve()
    canonical_relative_path = _application_resume_storage_key(recommendation_id, filename) if recommendation_id and filename else None
    if diagnostics is not None:
        diagnostics.update({
            "persistent_root": str(root),
            "canonical_relative_path": canonical_relative_path,
            "legacy_fallbacks": [],
        })
    path = _resolve_application_resume_storage_key(candidate_id, storage_key)
    # Old rows may contain a mounted-volume absolute path. Their basename is
    # still a useful legacy key, but never an authority outside DOCS_DIR.
    if not path:
        path = _resolve_candidate_document_path(candidate_id, "application_resumes", storage_key)
    if path and path.exists():
        if diagnostics is not None:
            diagnostics["selected_path"] = str(path)
        return path
    if not recover_legacy:
        return path

    keys = []
    if recommendation_id and filename:
        keys.append(_application_resume_storage_key(recommendation_id, filename))
    if recommendation_id:
        keys.append(f"{recommendation_id}.pdf")  # pre-canonical storage
    if resume_id and filename:
        keys.append(_application_resume_storage_key(resume_id, filename))
    if resume_id:
        keys.append(f"{resume_id}.pdf")
    for key in keys:
        recovered = _resolve_application_resume_storage_key(candidate_id, key)
        if diagnostics is not None:
            diagnostics["legacy_fallbacks"].append(str(recovered) if recovered else f"invalid:{key}")
        if recovered and recovered.exists() and recovered.is_file():
            if diagnostics is not None:
                diagnostics["selected_path"] = str(recovered)
            return recovered

    # Do not search by filename.  Even a unique current match could belong to
    # a different recommendation after data cleanup or a prior generic-name
    # bug.  Recovery must remain constrained to explicit recommendation or
    # application-resume IDs above.
    if diagnostics is not None:
        diagnostics["selected_path"] = str(path) if path else None
    return path


def _application_resume_path(candidate_id: str, recommendation_id: str, filename: str) -> Path:
    """Canonical resolver used by both application-resume save and View."""
    path = _resolve_application_resume_path(
        candidate_id, _application_resume_storage_key(recommendation_id, filename)
    )
    if not path:
        raise ValueError("Invalid application resume storage key")
    return path


def _resume_editor_pdf_profile(values: dict, raw_data: dict) -> dict:
    """Build the PDF input directly from the canonical Resume Editor save values."""
    return {
        "name": values.get("name", ""),
        "email": values.get("email", ""),
        "phone": values.get("phone", ""),
        "location": values.get("location", ""),
        "headline": values.get("headline", ""),
        "bio": values.get("bio", ""),
        "keySkills": values.get("skills", []),
        "work_experience": values.get("work_experience", []),
        "education": values.get("education", []),
        "certifications": values.get("certifications", []),
        "projects": values.get("projects", []),
        "raw_data": raw_data,
    }


@api_router.get("/candidate/{candidate_id}/resume/updated/download")
async def download_updated_resume(candidate_id: str):
    """Download the PDF generated by the most recent Resume Editor save."""
    candidate = await _get_candidate_row(candidate_id)
    raw_data = _parse_raw_data(candidate.get("raw_data"))
    stored_path = raw_data.get("updated_resume_file_path")
    file_path = Path(stored_path).resolve() if isinstance(stored_path, str) and stored_path else None
    expected_path = _updated_resume_pdf_path(candidate_id)
    if file_path != expected_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Updated resume PDF not found.")
    filename = _pdf_filename(_pdf_safe_text(candidate.get("name")) or f"candidate_{candidate_id}", candidate_id)
    return FileResponse(
        str(file_path), media_type=_document_media_type(file_path), filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.get("/candidate/{candidate_id}/jobs/{rec_id}/resume/download")
async def download_application_resume(candidate_id: str, rec_id: str):
    """Persist and download the edited PDF for the selected application."""
    candidate = await _get_candidate_row(candidate_id)
    # Ownership and job/company lookup are server-authoritative; never trust a
    # company name supplied by the browser for a persisted document.
    job = await _get_job_match_improvement_row(candidate_id, rec_id)
    raw_data = _parse_raw_data(candidate.get("raw_data"))
    source = raw_data.get("updated_resume_file_path")
    source_path = Path(source).resolve() if isinstance(source, str) and source else None
    if source_path != _updated_resume_pdf_path(candidate_id) or not source_path.exists():
        raise HTTPException(status_code=404, detail="Updated resume PDF not found.")
    # The ownership-checked JOIN returns job_descriptions.company_name.  Do
    # not read a presentation alias: it was the route by which valid jobs were
    # previously persisted as generic ``company_<candidate>.pdf`` artifacts.
    company_name = str(job.get("company_name") or "").strip() or "Company"
    filename = _application_resume_filename(company_name, str(candidate.get("name") or "Candidate"))
    storage_key = _application_resume_storage_key(rec_id, filename)
    destination = _application_resume_path(candidate_id, rec_id, filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Persist the exact generated artifact before returning its download.  A
    # same-volume replace prevents View from ever observing a partial PDF.
    generated_pdf = source_path.read_bytes()
    temporary_destination = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_destination.write_bytes(generated_pdf)
        os.replace(temporary_destination, destination)
    finally:
        if temporary_destination.exists():
            temporary_destination.unlink()
    logger.info(
        "[application-resume-save] candidate_id=%s recommendation_id=%s company_name=%r "
        "generated_pdf_written_to=%s canonical_relative_path=%r persistent_root=%s "
        "final_absolute_path=%s exists_after_write=%s bytes=%s",
        candidate_id, rec_id, company_name, destination, storage_key,
        (_candidate_storage_dir(candidate_id) / "application_resumes").resolve(), destination,
        destination.exists(), destination.stat().st_size if destination.exists() else None,
    )
    async with SessionLocal() as db:
        await db.execute(text("""
            INSERT INTO candidate_application_resumes
                (candidate_id, recommendation_id, job_id, company_name, file_name, file_path)
            VALUES (:cid, :rid, :jid, :company, :filename, :path)
            ON CONFLICT (candidate_id, recommendation_id) DO UPDATE SET
                company_name=EXCLUDED.company_name, file_name=EXCLUDED.file_name,
                file_path=EXCLUDED.file_path, updated_at=now()
        """), {"cid": candidate_id, "rid": rec_id, "jid": str(job.get("job_id") or "") or None,
               "company": company_name, "filename": filename, "path": storage_key})
        await db.commit()
    return FileResponse(str(destination), media_type="application/pdf", filename=filename,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    merged_raw = _append_demonstrated_skill_evidence(
        merged_raw, candidate.get("skills") or [], voice_usage_text, "eve_voice"
    )
    set_clauses.append("raw_data = CAST(:raw_data AS jsonb)")
    update_params["raw_data"] = json.dumps(merged_raw)

    set_clauses.append("updated_at = now()")
    set_clauses.append("updated_by_source = 'eve_voice'")
    async with SessionLocal() as db:
        await db.execute(
            text(f"UPDATE candidates SET {', '.join(set_clauses)} WHERE id = :cid"),
            update_params,
        )
        await db.commit()

    await _upsert_candidate_preferences(candidate_id, voice_data)
    return merged


# ---------- Voice intake endpoint ----------

@api_router.post("/voice/candidate-intake")
async def candidate_voice_intake(request: VoiceCandidateIntakeRequest):
    """
    Receive voice intake transcript, extract structured info, merge into candidate profile.
    candidate_id is validated against the DB — never trusted blindly from the browser.
    """
    # 1. Validate candidate exists
    candidate = await _get_candidate_row(request.candidate_id)

    transcript = (request.transcript or "").strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="Transcript is empty.")

    # 2. Idempotency: check for recent duplicate (same candidate, same transcript hash)
    transcript_hash = hashlib.sha256(transcript.encode()).hexdigest()
    async with SessionLocal() as db:
        dup = await db.execute(
            text("""
                SELECT id FROM candidate_voice_intakes
                WHERE candidate_id = :cid
                  AND md5(transcript) = md5(:transcript)
                  AND created_at > now() - interval '10 minutes'
                LIMIT 1
            """),
            {"cid": request.candidate_id, "transcript": transcript},
        )
        if dup.fetchone():
            logger.info("Duplicate voice intake ignored for candidate %s", request.candidate_id)
            return {"status": "duplicate", "candidate_id": request.candidate_id}

    # 3. Persist intake record (status=pending)
    intake_id = str(uuid.uuid4())
    voice_notes = _normalize_voice_notes(request.voice_notes, transcript)
    async with SessionLocal() as db:
        await db.execute(
            text("""
                INSERT INTO candidate_voice_intakes
                    (id, candidate_id, transcript, voice_notes, status, created_at)
                VALUES (:id, :cid, :transcript, CAST(:notes AS jsonb), 'processing', now())
            """),
            {
                "id": intake_id,
                "cid": request.candidate_id,
                "transcript": transcript,
                "notes": json.dumps(voice_notes),
            },
        )
        await db.commit()

    existing_candidate = await _get_candidate_row(request.candidate_id)
    existing_raw = _parse_raw_data(existing_candidate.get("raw_data"))
    existing_vi = _parse_raw_data(existing_raw.get("voice_intake"))
    completed_turns, pending_question = _voice_intake_turn_pairs(voice_notes, transcript)
    llm_analysis = await _llm_analyze_intake(existing_candidate, completed_turns, pending_question)
    voice_intake_state = _build_voice_intake_resume_from_notes(
        voice_notes, transcript, existing_vi,
        candidate_profile=existing_candidate,
        llm_analysis=llm_analysis,
    )

    # 4. Extract structured info via LLM
    voice_data_source = _voice_intake_turns_to_transcript(voice_intake_state.get("completed_turns") or [])
    voice_data = await _extract_voice_info(voice_data_source or transcript)

    # 5. Merge into existing profile, preferences, and resume state.
    merged = await _persist_voice_intake_profile_state(
        request.candidate_id,
        existing_candidate,
        voice_data,
        voice_intake_state,
    )

    # 6. Mark intake as completed only when the actual intake questions have all been answered.
    async with SessionLocal() as db:
        await db.execute(
            text("""
                UPDATE candidate_voice_intakes
                SET status = :status,
                    completed_at = CASE WHEN :status = 'completed' THEN now() ELSE completed_at END
                WHERE id = :id
            """),
        {"id": intake_id, "status": voice_intake_state["status"]},
        )
        await db.commit()

    logger.info(
        "Voice intake saved for candidate %s (intake %s, status=%s)",
        request.candidate_id,
        intake_id,
        voice_intake_state["status"],
    )

    if voice_intake_state["status"] == "completed":
        asyncio.ensure_future(_trigger_matching(request.candidate_id))

    # Return updated profile so dashboard can refresh immediately
    updated_candidate = await _get_candidate_row(request.candidate_id)
    updated_profile_row = dict(updated_candidate)
    updated_profile_row["candidate_certificates"] = await _load_candidate_certificates(request.candidate_id)
    updated_profile = _normalize_for_frontend(updated_profile_row)
    updated_profile["voice_intake_resume"] = voice_intake_state

    return {
        "status": voice_intake_state["status"],
        "intake_id": intake_id,
        "candidate_id": request.candidate_id,
        "fields_updated": [
            *[
                field for field in ("summary", "current_role", "current_company", "location", "experience_years", "skills", "work_experience", "education")
                if merged.get(field) != candidate.get(field)
            ],
            *(
                ["projects"]
                if _parse_raw_data(merged.get("raw_data")).get("projects")
                != _parse_raw_data(candidate.get("raw_data")).get("projects")
                else []
            ),
        ],
        "profile": updated_profile,
        "voice_intake_state": voice_intake_state,
    }


@api_router.post("/voice/candidate-intake/progress")
async def candidate_voice_intake_progress(request: VoiceCandidateIntakeProgressRequest):
    """Persist an in-progress voice intake snapshot without completing the profile merge."""
    candidate = await _get_candidate_row(request.candidate_id)
    transcript = (request.transcript or "").strip()
    voice_notes = _normalize_voice_notes(request.voice_notes, transcript)

    existing_raw = _parse_raw_data(candidate.get("raw_data"))
    existing_vi = _parse_raw_data(existing_raw.get("voice_intake"))
    completed_turns, pending_question = _voice_intake_turn_pairs(voice_notes, transcript)
    llm_analysis = await _llm_analyze_intake(candidate, completed_turns, pending_question)
    resume = _build_voice_intake_resume_from_notes(
        voice_notes, transcript, existing_vi,
        candidate_profile=candidate,
        llm_analysis=llm_analysis,
    )
    if not resume.get("status"):
        resume["status"] = "in_progress"

    await _save_voice_intake_resume(request.candidate_id, resume)
    voice_data_source = _voice_intake_turns_to_transcript(resume.get("completed_turns") or [])
    voice_data = await _extract_voice_info(voice_data_source or transcript) if (voice_data_source or transcript) else {}
    await _persist_voice_intake_profile_state(request.candidate_id, candidate, voice_data, resume)
    return {
        "status": "saved",
        "candidate_id": request.candidate_id,
        "voice_intake_resume": resume,
    }


# ---------- Mutual Interest endpoints ----------

@api_router.get("/candidate/{candidate_id}/opportunities")
async def get_opportunities(candidate_id: str):
    """
    Return recruiter-interested job opportunities for the given candidate.
    Scoped strictly by candidate_id — never returns another candidate's data.
    """
    candidate = await _get_candidate_row(candidate_id)
    if await _effective_profile_strength_percent(candidate_id, candidate) < 90:
        raise HTTPException(
            status_code=403,
            detail={"code": "profile_strength_required", "message": "Profile Strength must be at least 90% to view opportunities."},
        )
    async with SessionLocal() as db:
        rows = await db.execute(
            text("""
                SELECT
                    rir.id,
                    rir.job_id,
                    rir.request_status,
                    rir.recruiter_message,
                    rir.candidate_response,
                    rir.candidate_response_at,
                    rir.created_at,
                    jd.title,
                    jd.company_name,
                    jd.location,
                    jd.description,
                    jd.requirements,
                    jd.skills
                FROM recruiter_interest_requests rir
                LEFT JOIN job_descriptions jd ON jd.id = rir.job_id
                WHERE rir.candidate_id = :cid
                  AND rir.request_status IN ('interested', 'pending')
                ORDER BY rir.created_at DESC
            """),
            {"cid": candidate_id},
        )
        results = rows.mappings().fetchall()
    return [
        {
            "id": str(r["id"]),
            "job_id": str(r["job_id"]) if r["job_id"] else None,
            "request_status": r["request_status"],
            "recruiter_message": r["recruiter_message"],
            "candidate_response": r["candidate_response"],
            "candidate_response_at": r["candidate_response_at"].isoformat() if r["candidate_response_at"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "job": {
                "title": r["title"],
                "company": r["company_name"],
                "location": r["location"],
                "description": r["description"],
                "requirements": r["requirements"],
                "skills": r["skills"],
            },
        }
        for r in results
    ]


class OpportunityResponseIn(BaseModel):
    response: Literal["interested", "not_interested"]


class JobDismissRequest(BaseModel):
    reason: Optional[str] = None


@api_router.post("/candidate/{candidate_id}/opportunities/{rec_id}/respond")
async def respond_to_opportunity(candidate_id: str, rec_id: str, body: OpportunityResponseIn):
    """
    Store the candidate's interest/rejection for a specific opportunity.
    Enforces candidate_id ownership — a candidate cannot respond on behalf of another.
    Prevents duplicate responses.
    On first response, durably enqueues an eve_outbound_events row and triggers delivery to Adam.
    """
    await _get_candidate_row(candidate_id)

    async with SessionLocal() as db:
        row = await db.execute(
            text("""
                SELECT id, candidate_response, adam_event_id, job_id, agency_id
                FROM recruiter_interest_requests
                WHERE id = :rid AND candidate_id = :cid
                LIMIT 1
            """),
            {"rid": rec_id, "cid": candidate_id},
        )
        rec = row.mappings().fetchone()
        if not rec:
            raise HTTPException(status_code=404, detail="Opportunity not found.")
        if rec["candidate_response"] is not None:
            return {"status": "already_responded", "candidate_response": rec["candidate_response"]}

        # Update candidate response state (existing logic — unchanged)
        await db.execute(
            text("""
                UPDATE recruiter_interest_requests
                SET candidate_response = :resp,
                    candidate_response_at = now(),
                    updated_at = now()
                WHERE id = :rid AND candidate_id = :cid
            """),
            {"resp": body.response, "rid": rec_id, "cid": candidate_id},
        )
        if body.response == "interested":
            if rec["job_id"]:
                await db.execute(
                    text("""
                        UPDATE candidate_job_recommendations
                        SET tracked_at = now()
                        WHERE candidate_id = :cid AND job_id = :jid AND tracked_at IS NULL
                    """),
                    {"cid": candidate_id, "jid": str(rec["job_id"])},
                )

        # Enqueue outbound event to Adam — only if adam_event_id is present
        eve_event_id: Optional[str] = None
        if rec["adam_event_id"] and rec["job_id"] and rec["agency_id"]:
            eve_event_id = str(uuid.uuid4())
            await db.execute(
                text("""
                    INSERT INTO eve_outbound_events
                        (eve_event_id, adam_event_id, candidate_id, job_id, agency_id,
                         response, status, attempt_count, next_retry_at, created_at)
                    VALUES
                        (:eid, :aeid, :cid, :jid, :aid,
                         :resp, 'pending', 0, now(), now())
                """),
                {
                    "eid": eve_event_id,
                    "aeid": str(rec["adam_event_id"]),
                    "cid": candidate_id,
                    "jid": str(rec["job_id"]),
                    "aid": str(rec["agency_id"]),
                    "resp": body.response,
                },
            )

        await db.commit()

    # Trigger immediate delivery attempt (fire-and-forget; retry worker handles failures)
    if eve_event_id:
        asyncio.ensure_future(_attempt_delivery(eve_event_id))

    return {"status": "ok", "candidate_response": body.response}


# ---------- Jobs endpoints ----------

FREE_DAILY_JOB_LIMIT = 3
# The daily allowance resets at midnight in the product timezone, not at the
# database server's midnight.
PRODUCT_TIMEZONE = os.environ.get("PRODUCT_TIMEZONE", "Asia/Kolkata")


def _product_current_date():
    """Return the product calendar date stored in daily job-access records."""
    return datetime.now(ZoneInfo(PRODUCT_TIMEZONE)).date()


def _has_active_subscription(candidate: dict) -> bool:
    """Read subscription state from the existing candidate record/profile payload."""
    raw_data = _parse_raw_data(candidate.get("raw_data"))
    subscription = _parse_raw_data(raw_data.get("subscription"))
    sources = (candidate, raw_data, subscription)
    if any(source.get(key) is True for source in sources for key in ("subscription_active", "is_subscribed", "is_active")):
        return True
    statuses = [candidate.get("subscription_status"), raw_data.get("subscription_status"), subscription.get("status")]
    return any(str(status).lower() in {"active", "trialing", "trial"} for status in statuses if status is not None)


def _daily_job_limit_reached(used: int, request_more: bool) -> bool:
    return request_more and used >= FREE_DAILY_JOB_LIMIT


async def _claim_resume_fix_credits(candidate_id: str, candidate: dict, usage_date=None) -> dict:
    """Atomically reserve three credits for one free-plan resume-fix click."""
    if _has_active_subscription(candidate):
        return {"claim_id": None, "remaining_credits": None, "is_subscribed": True}

    usage_date = usage_date or _product_current_date()
    async with SessionLocal() as db:
        # A candidate can have several browser tabs open, so all balance reads
        # and deductions happen under one lock rather than trusting the UI.
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {
            "lock_key": f"resume-fix-credits:{candidate_id}"
        })
        # The row is created lazily so candidates created before this feature
        # receive the same starter grant exactly once.
        await db.execute(text("""
            INSERT INTO candidate_resume_fix_credit_balances (candidate_id, starter_credits_remaining)
            VALUES (:cid, :starter_credits)
            ON CONFLICT (candidate_id) DO NOTHING
        """), {"cid": candidate_id, "starter_credits": INITIAL_RESUME_FIX_CREDITS})
        starter_result = await db.execute(text("""
            SELECT starter_credits_remaining
            FROM candidate_resume_fix_credit_balances
            WHERE candidate_id = :cid
        """), {"cid": candidate_id})
        starter_remaining = starter_result.scalar() or 0

        if starter_remaining >= RESUME_FIX_CREDIT_COST:
            updated = await db.execute(text("""
                UPDATE candidate_resume_fix_credit_balances
                SET starter_credits_remaining = starter_credits_remaining - :cost
                WHERE candidate_id = :cid
                  AND starter_credits_remaining >= :cost
                RETURNING starter_credits_remaining
            """), {"cid": candidate_id, "cost": RESUME_FIX_CREDIT_COST})
            remaining = updated.scalar()
            if remaining is None:
                # Defensive recheck for a non-standard database isolation mode.
                raise HTTPException(status_code=403, detail={
                    "code": "resume_fix_credits_insufficient",
                    "message": "Upgrade your plan to keep using Fix My Resume.",
                    "remaining_credits": 0,
                })
            inserted = await db.execute(text("""
                INSERT INTO candidate_resume_fix_credit_claims (candidate_id, usage_date, credit_source)
                VALUES (:cid, :usage_date, 'starter') RETURNING id
            """), {"cid": candidate_id, "usage_date": usage_date})
            claim_id = str(inserted.scalar())
            # Surface the newly active daily balance immediately after the
            # last usable starter action, rather than showing an unusable 1–2.
            if remaining < RESUME_FIX_CREDIT_COST:
                daily_result = await db.execute(text("""
                    SELECT COUNT(*) FROM candidate_resume_fix_credit_claims
                    WHERE candidate_id = :cid AND usage_date = :usage_date AND credit_source = 'daily'
                """), {"cid": candidate_id, "usage_date": usage_date})
                daily_used = (daily_result.scalar() or 0) * RESUME_FIX_CREDIT_COST
                await db.commit()
                return {
                    "claim_id": claim_id,
                    "remaining_credits": max(0, FREE_DAILY_RESUME_FIX_CREDITS - daily_used),
                    "credit_phase": "daily",
                    "is_subscribed": False,
                }
            await db.commit()
            return {
                "claim_id": claim_id,
                "remaining_credits": remaining,
                "credit_phase": "starter",
                "is_subscribed": False,
            }

        # A leftover one or two starter credits cannot fund a 3-credit action.
        # At that point the non-carrying daily allowance becomes the usable pool.
        result = await db.execute(text("""
            SELECT COUNT(*) FROM candidate_resume_fix_credit_claims
            WHERE candidate_id = :cid AND usage_date = :usage_date AND credit_source = 'daily'
        """), {"cid": candidate_id, "usage_date": usage_date})
        used_credits = (result.scalar() or 0) * RESUME_FIX_CREDIT_COST
        remaining = max(0, FREE_DAILY_RESUME_FIX_CREDITS - used_credits)
        if remaining < RESUME_FIX_CREDIT_COST:
            raise HTTPException(status_code=403, detail={
                "code": "resume_fix_credits_insufficient",
                "message": "Upgrade your plan to keep using Fix My Resume.",
                "remaining_credits": remaining,
            })
        inserted = await db.execute(text("""
            INSERT INTO candidate_resume_fix_credit_claims (candidate_id, usage_date, credit_source)
            VALUES (:cid, :usage_date, 'daily') RETURNING id
        """), {"cid": candidate_id, "usage_date": usage_date})
        claim_id = inserted.scalar()
        await db.commit()
    return {
        "claim_id": str(claim_id),
        "remaining_credits": remaining - RESUME_FIX_CREDIT_COST,
        "credit_phase": "daily",
        "is_subscribed": False,
    }


RAZORPAY_PLAN_AMOUNT_PAISE = 300000
RAZORPAY_PLAN_NAME = "Eve Candidate — 3 months"


async def _ensure_candidate_billing_tables() -> None:
    """Keep the candidate payment ledger separate from recruiter subscriptions."""
    async with SessionLocal() as db:
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS candidate_subscriptions (
              id uuid PRIMARY KEY, candidate_id uuid NOT NULL, plan_name text NOT NULL,
              amount_paise integer NOT NULL, status text NOT NULL, starts_at timestamptz,
              expires_at timestamptz, razorpay_order_id text UNIQUE, razorpay_payment_id text UNIQUE,
              created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        await db.execute(text("""
            CREATE TABLE IF NOT EXISTS candidate_payment_attempts (
              id uuid PRIMARY KEY, candidate_id uuid NOT NULL, subscription_id uuid NOT NULL,
              amount_paise integer NOT NULL, currency text NOT NULL DEFAULT 'INR', status text NOT NULL,
              razorpay_order_id text UNIQUE NOT NULL, razorpay_payment_id text UNIQUE,
              razorpay_signature text, receipt text, provider_payload jsonb,
              created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
            )
        """))
        await db.commit()


def _razorpay_credentials() -> tuple[str, str]:
    key_id, key_secret = os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")
    if not key_id or not key_secret:
        raise HTTPException(status_code=503, detail="Payments are not configured.")
    return key_id, key_secret


async def _activate_candidate_subscription(candidate_id: str, order_id: str, payment_id: str, signature: str, payload: dict) -> dict:
    """Idempotently activate only after a server-side signature check."""
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=90)
    async with SessionLocal() as db:
        attempt = await db.execute(text("""
            UPDATE candidate_payment_attempts SET status = 'paid', razorpay_payment_id = :payment_id,
              razorpay_signature = :signature, provider_payload = CAST(:payload AS jsonb), updated_at = now()
            WHERE candidate_id = :cid AND razorpay_order_id = :order_id
            RETURNING subscription_id
        """), {"cid": candidate_id, "order_id": order_id, "payment_id": payment_id,
               "signature": signature, "payload": json.dumps(payload, default=str)})
        subscription_id = attempt.scalar()
        if not subscription_id:
            raise HTTPException(status_code=400, detail="Unknown payment order.")
        await db.execute(text("""
            UPDATE candidate_subscriptions SET status = 'active', starts_at = COALESCE(starts_at, :starts_at),
              expires_at = GREATEST(COALESCE(expires_at, :starts_at), :expires_at),
              razorpay_payment_id = :payment_id, updated_at = now()
            WHERE id = :subscription_id
        """), {"subscription_id": subscription_id, "starts_at": now, "expires_at": expires, "payment_id": payment_id})
        await db.commit()
    return {"status": "active", "starts_at": now.isoformat(), "expires_at": expires.isoformat()}


@api_router.post("/candidate/{candidate_id}/billing/orders")
async def create_candidate_billing_order(candidate_id: str):
    candidate = await _get_candidate_row(candidate_id)
    if _has_active_subscription(candidate):
        raise HTTPException(status_code=409, detail="An active subscription already exists.")
    await _ensure_candidate_billing_tables()
    key_id, key_secret = _razorpay_credentials()
    receipt = f"eve_{candidate_id.replace('-', '')[:18]}_{uuid.uuid4().hex[:10]}"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post("https://api.razorpay.com/v1/orders", auth=(key_id, key_secret), json={
            "amount": RAZORPAY_PLAN_AMOUNT_PAISE, "currency": "INR", "receipt": receipt,
            "notes": {"candidate_id": candidate_id, "plan": "candidate_3_month"},
        })
    if response.is_error:
        logger.error("Razorpay order creation failed: %s", response.text)
        raise HTTPException(status_code=502, detail="Unable to start payment. Please try again.")
    order = response.json()
    subscription_id = str(uuid.uuid4())
    async with SessionLocal() as db:
        await db.execute(text("""INSERT INTO candidate_subscriptions
            (id, candidate_id, plan_name, amount_paise, status, razorpay_order_id)
            VALUES (:id, :cid, :plan, :amount, 'pending', :order_id)"""),
            {"id": subscription_id, "cid": candidate_id, "plan": RAZORPAY_PLAN_NAME, "amount": RAZORPAY_PLAN_AMOUNT_PAISE, "order_id": order["id"]})
        await db.execute(text("""INSERT INTO candidate_payment_attempts
            (id, candidate_id, subscription_id, amount_paise, status, razorpay_order_id, receipt, provider_payload)
            VALUES (:id, :cid, :subscription_id, :amount, 'created', :order_id, :receipt, CAST(:payload AS jsonb))"""),
            {"id": str(uuid.uuid4()), "cid": candidate_id, "subscription_id": subscription_id,
             "amount": RAZORPAY_PLAN_AMOUNT_PAISE, "order_id": order["id"], "receipt": receipt, "payload": json.dumps(order)})
        await db.commit()
    return {"key_id": key_id, "order_id": order["id"], "amount": RAZORPAY_PLAN_AMOUNT_PAISE,
            "currency": "INR", "name": "Eve", "description": "₹3,000 / 3 months", "prefill": {"name": candidate.get("name") or "", "email": candidate.get("email") or ""}}


@api_router.post("/candidate/{candidate_id}/billing/verify")
async def verify_candidate_billing_payment(candidate_id: str, body: RazorpayVerificationRequest):
    await _ensure_candidate_billing_tables()
    key_id, secret = _razorpay_credentials()
    expected = hmac.new(secret.encode(), f"{body.razorpay_order_id}|{body.razorpay_payment_id}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, body.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature.")
    async with httpx.AsyncClient(timeout=15) as client:
        payment_response = await client.get(f"https://api.razorpay.com/v1/payments/{body.razorpay_payment_id}", auth=(key_id, secret))
    if payment_response.is_error:
        raise HTTPException(status_code=502, detail="Unable to confirm payment status.")
    payment = payment_response.json()
    if payment.get("order_id") != body.razorpay_order_id or payment.get("status") != "captured" or payment.get("amount") != RAZORPAY_PLAN_AMOUNT_PAISE or payment.get("currency") != "INR":
        raise HTTPException(status_code=400, detail="Payment is not captured for this subscription.")
    return await _activate_candidate_subscription(candidate_id, body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature, payment)


@api_router.post("/webhooks/razorpay")
async def razorpay_webhook(request: StarletteRequest):
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not webhook_secret or not hmac.compare_digest(hmac.new(webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest(), signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")
    event = json.loads(raw_body)
    payment = event.get("payload", {}).get("payment", {}).get("entity", {})
    await _ensure_candidate_billing_tables()
    if event.get("event") == "payment.failed" and payment.get("order_id"):
        async with SessionLocal() as db:
            await db.execute(text("UPDATE candidate_payment_attempts SET status = 'failed', provider_payload = CAST(:payload AS jsonb), updated_at = now() WHERE razorpay_order_id = :order_id"), {"order_id": payment["order_id"], "payload": json.dumps(event)})
            await db.commit()
    elif event.get("event") == "payment.captured" and payment.get("order_id") and payment.get("id"):
        async with SessionLocal() as db:
            owner = await db.execute(text("SELECT candidate_id FROM candidate_payment_attempts WHERE razorpay_order_id = :order_id"), {"order_id": payment["order_id"]})
            candidate_id = owner.scalar()
        if candidate_id:
            await _activate_candidate_subscription(str(candidate_id), payment["order_id"], payment["id"], "webhook", event)
    return {"ok": True}


@api_router.get("/candidate/{candidate_id}/billing")
async def get_candidate_billing(candidate_id: str):
    await _get_candidate_row(candidate_id)
    await _ensure_candidate_billing_tables()
    async with SessionLocal() as db:
        sub = (await db.execute(text("""SELECT plan_name, status, starts_at, expires_at, razorpay_payment_id
            FROM candidate_subscriptions WHERE candidate_id = :cid ORDER BY created_at DESC LIMIT 1"""), {"cid": candidate_id})).mappings().fetchone()
        history = (await db.execute(text("""SELECT status, amount_paise, razorpay_order_id, razorpay_payment_id, receipt, created_at
            FROM candidate_payment_attempts WHERE candidate_id = :cid ORDER BY created_at DESC"""), {"cid": candidate_id})).mappings().fetchall()
    active = bool(sub and sub["status"] == "active" and sub["expires_at"] and sub["expires_at"] > datetime.now(timezone.utc))
    return {"status": "Active" if active else "Free", "plan": "₹3,000 / 3 months" if active else "Free",
            "subscription_start": sub["starts_at"].isoformat() if active and sub["starts_at"] else None,
            "subscription_expiry": sub["expires_at"].isoformat() if active and sub["expires_at"] else None,
            "payment_status": "paid" if active else (sub["status"] if sub else "not_started"),
            "payment_id": sub["razorpay_payment_id"] if sub else None,
            "history": [dict(row) for row in history]}


async def _get_resume_fix_credit_balance(candidate_id: str, candidate: dict, usage_date=None) -> dict:
    """Return the backend-authoritative starter or current daily balance."""
    if _has_active_subscription(candidate):
        return {"remaining_credits": None, "is_subscribed": True}

    usage_date = usage_date or _product_current_date()
    async with SessionLocal() as db:
        await db.execute(text("""
            INSERT INTO candidate_resume_fix_credit_balances (candidate_id, starter_credits_remaining)
            VALUES (:cid, :starter_credits)
            ON CONFLICT (candidate_id) DO NOTHING
        """), {"cid": candidate_id, "starter_credits": INITIAL_RESUME_FIX_CREDITS})
        starter_result = await db.execute(text("""
            SELECT starter_credits_remaining
            FROM candidate_resume_fix_credit_balances
            WHERE candidate_id = :cid
        """), {"cid": candidate_id})
        starter_remaining = starter_result.scalar() or 0
        if starter_remaining >= RESUME_FIX_CREDIT_COST:
            await db.commit()
            return {
                "remaining_credits": starter_remaining,
                "credit_phase": "starter",
                "is_subscribed": False,
            }
        result = await db.execute(text("""
            SELECT COUNT(*) FROM candidate_resume_fix_credit_claims
            WHERE candidate_id = :cid AND usage_date = :usage_date AND credit_source = 'daily'
        """), {"cid": candidate_id, "usage_date": usage_date})
        used_credits = (result.scalar() or 0) * RESUME_FIX_CREDIT_COST
        await db.commit()
    return {
        "remaining_credits": max(0, FREE_DAILY_RESUME_FIX_CREDITS - used_credits),
        "credit_phase": "daily",
        "is_subscribed": False,
    }


async def _validate_resume_fix_credit_claim(candidate_id: str, candidate: dict, claim_id: Optional[str]) -> None:
    """Ensure free-plan saves came from a charged Fix My Resume click."""
    if _has_active_subscription(candidate):
        return
    if not claim_id:
        raise HTTPException(status_code=403, detail={
            "code": "resume_fix_credits_insufficient",
            "message": "Upgrade your plan to keep using Fix My Resume.",
        })
    async with SessionLocal() as db:
        result = await db.execute(text("""
            UPDATE candidate_resume_fix_credit_claims SET consumed_at = now()
            WHERE id = :claim_id AND candidate_id = :cid AND consumed_at IS NULL
            RETURNING id
        """), {"claim_id": claim_id, "cid": candidate_id})
        if result.scalar() is None:
            raise HTTPException(status_code=403, detail={
                "code": "resume_fix_credits_insufficient",
                "message": "Upgrade your plan to keep using Fix My Resume.",
            })
        await db.commit()


@api_router.post("/candidate/{candidate_id}/jobs/{rec_id}/resume-fix-credit-claim")
async def claim_resume_fix_credits(candidate_id: str, rec_id: str):
    """Charge a free candidate before opening a job-scoped resume editor."""
    candidate = await _get_candidate_row(candidate_id)
    await _get_job_match_improvement_row(candidate_id, rec_id)  # ownership check
    return await _claim_resume_fix_credits(candidate_id, candidate)


@api_router.get("/candidate/{candidate_id}/resume-fix-credits")
async def get_resume_fix_credit_balance(candidate_id: str):
    """Expose the backend-authoritative daily Fix My Resume balance."""
    candidate = await _get_candidate_row(candidate_id)
    return await _get_resume_fix_credit_balance(candidate_id, candidate)


async def _claim_daily_job_access(
    candidate_id: str, candidate: dict, request_more: bool = False, access_date=None
) -> bool:
    """Persist and enforce the free daily job allowance. Returns whether it applies."""
    if _has_active_subscription(candidate):
        return False

    access_date = access_date or _product_current_date()
    async with SessionLocal() as db:
        # Serialise claims for one candidate/day so separate devices cannot each
        # receive a different free batch.
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"), {
            "lock_key": f"daily-job-access:{candidate_id}"
        })
        used_row = await db.execute(text("""
            SELECT COUNT(*) FROM candidate_daily_job_access
            WHERE candidate_id = :cid AND access_date = :access_date
        """), {"cid": candidate_id, "access_date": access_date})
        used = used_row.scalar() or 0
        if _daily_job_limit_reached(used, request_more):
            raise HTTPException(
                status_code=403,
                detail={"code": "daily_job_limit_reached", "message": "Unlock more jobs by subscribing."},
            )

        slots = max(0, FREE_DAILY_JOB_LIMIT - used)
        if slots:
            candidates = await db.execute(text("""
                SELECT cjr.id
                FROM candidate_job_recommendations cjr
                WHERE cjr.candidate_id = :cid AND cjr.hidden_at IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM candidate_daily_job_access access
                    WHERE access.candidate_id = cjr.candidate_id
                      AND access.recommendation_id = cjr.id
                  )
                ORDER BY cjr.recommendation_rank ASC NULLS LAST,
                         cjr.match_score DESC NULLS LAST
                LIMIT :slots
            """), {"cid": candidate_id, "slots": slots})
            for row in candidates.fetchall():
                await db.execute(text("""
                    INSERT INTO candidate_daily_job_access
                        (candidate_id, recommendation_id, access_date)
                    VALUES (:cid, :rid, :access_date)
                    ON CONFLICT (candidate_id, recommendation_id, access_date) DO NOTHING
                """), {"cid": candidate_id, "rid": str(row[0]), "access_date": access_date})
        await db.commit()
    return True

@api_router.get("/candidate/{candidate_id}/jobs")
async def get_candidate_jobs(candidate_id: str, request_more: bool = False, response: Response = None):
    """Return semantic job recommendations for this candidate, joined with job_descriptions."""
    candidate = await _get_candidate_row(candidate_id)
    if not _voice_intake_completed_for_matching(candidate):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "voice_intake_required",
                "message": "Complete Voice Intake before personalized job recommendations are available.",
            },
        )
    if await _effective_profile_strength_percent(candidate_id, candidate) < 90:
        raise HTTPException(
            status_code=403,
            detail={"code": "profile_strength_required", "message": "Profile Strength must be at least 90% to view jobs."},
        )

    # A free candidate only needs a new match run after exhausting every visible
    # recommendation.  Historical daily-access rows deliberately make a
    # recommendation ineligible for another free allocation, even on a later
    # calendar date.
    async with SessionLocal() as db:
        available_row = await db.execute(
            text(f"""
                SELECT COUNT(*)
                FROM candidate_job_recommendations cjr JOIN job_descriptions jd ON jd.id = cjr.job_id
                WHERE cjr.candidate_id = :cid
                  AND cjr.hidden_at IS NULL
                  AND {candidate_visible_where('jd')}
                  AND NOT EXISTS (
                    SELECT 1
                    FROM candidate_daily_job_access access
                    WHERE access.candidate_id = cjr.candidate_id
                      AND access.recommendation_id = cjr.id
                  )
            """),
            {"cid": candidate_id},
        )
        unaccessed_visible_count = available_row.scalar() or 0

    if not _has_active_subscription(candidate) and unaccessed_visible_count == 0:
        try:
            from candidate_job_matching_service import refresh_candidate_job_matches
            await refresh_candidate_job_matches(candidate_id, candidate, SessionLocal)
        except Exception as e:
            logger.warning("[matching] On-demand matching failed for %s: %s", candidate_id, e)

    # The free-plan allowance controls which recommendations may be opened,
    # not how many matches the candidate can see are available.
    async with SessionLocal() as db:
        total_row = await db.execute(
            text(f"""
                SELECT COUNT(*)
                FROM candidate_job_recommendations cjr JOIN job_descriptions jd ON jd.id = cjr.job_id
                WHERE cjr.candidate_id = :cid AND cjr.hidden_at IS NULL
                  AND {candidate_visible_where('jd')}
            """),
            {"cid": candidate_id},
        )
        total_matching_jobs = total_row.scalar() or 0

    # Capture once so a request which happens to span midnight has one coherent
    # product-calendar date for both its claim and response.
    access_date = _product_current_date()
    limited = await _claim_daily_job_access(candidate_id, candidate, request_more, access_date)

    async with SessionLocal() as db:
        rows = await db.execute(
            text(f"""
                SELECT
                    cjr.id AS rec_id,
                    cjr.job_id,
                    cjr.match_score,
                    cjr.recommendation_rank,
                    cjr.match_reason,
                    cjr.tracked_at,
                    cjr.applied_at,
                    cjr.hidden_at,
                    cjr.viewed_at,
                    cjr.status      AS application_status,
                    cjr.agency_id   AS application_agency_id,
                    cjr.job_role    AS application_job_role,
                    jd.title,
                    jd.company_name,
                    jd.location,
                    jd.salary_range,
                    jd.employment_type,
                    jd.remote_policy,
                    jd.experience_level,
                    jd.experience_required,
                    jd.created_at,
                    jd.skills_required,
                    jd.description,
                    jd.requirements,
                    jd.skills,
                    jd.company_logo_url,
                    jd.job_url
                FROM candidate_job_recommendations cjr
                LEFT JOIN job_descriptions jd ON jd.id = cjr.job_id
                WHERE cjr.candidate_id = :cid
                  AND cjr.hidden_at IS NULL
                  AND {candidate_visible_where('jd')}
                -- Return the complete ranked list so the client can render
                -- locked placeholders.  The access predicate is projected
                -- below instead of filtering the rows out: free candidates
                -- must not receive details for rows they have not claimed.
                ORDER BY cjr.recommendation_rank ASC NULLS LAST, cjr.match_score DESC NULLS LAST
            """),
            {"cid": candidate_id},
        )
        results = rows.mappings().fetchall()
    if response is not None:
        response.headers["X-Total-Matching-Jobs"] = str(total_matching_jobs)
    accessible_ids = set()
    if limited:
        async with SessionLocal() as db:
            access_rows = await db.execute(text("""
                SELECT recommendation_id
                FROM candidate_daily_job_access
                WHERE candidate_id = :cid AND access_date = :access_date
            """), {"cid": candidate_id, "access_date": access_date})
            accessible_ids = {str(row[0]) for row in access_rows.fetchall()}

    def serialize_job(r):
        accessible = not limited or str(r["rec_id"]) in accessible_ids
        if not accessible:
            # Deliberately omit all job metadata.  These identifiers exist
            # only to keep the UI list stable; the locked card renders generic
            # content and cannot invoke job actions.
            return {
                "id": str(r["rec_id"]),
                "locked": True,
                "recommendation_rank": r["recommendation_rank"],
            }
        return {
            "id": str(r["rec_id"]),
            "job_id": str(r["job_id"]) if r["job_id"] else None,
            "title": r["title"] or "",
            "company": r["company_name"] or "",
            "location": r["location"] or "",
            "salary": r["salary_range"] or "",
            "salary_range": r["salary_range"] or "",
            "employment_type": r["employment_type"] or None,
            "remote_policy": r["remote_policy"] or None,
            "experience_level": r["experience_level"] or r["experience_required"] or None,
            "posted_at": r["created_at"].isoformat() if r["created_at"] is not None and hasattr(r["created_at"], "isoformat") else r["created_at"],
            "created_at": r["created_at"].isoformat() if r["created_at"] is not None and hasattr(r["created_at"], "isoformat") else r["created_at"],
            "skills_required": r["skills_required"] or r["skills"] or [],
            "description": r["description"] or "",
            "requirements": r["requirements"] or "",
            "skills": r["skills"] or [],
            "logo": r["company_logo_url"] or None,
            "match_score": float(r["match_score"]) if r["match_score"] is not None else None,
            "recommendation_rank": r["recommendation_rank"],
            "match_reason": r["match_reason"],
            "tracked": r["tracked_at"] is not None,
            "applied": r["applied_at"] is not None,
            "viewed": r["viewed_at"] is not None,
            "application_status": r["application_status"],
            "job_url": r["job_url"] or None,
            "locked": False,
        }
    return [serialize_job(r) for r in results]


def _job_required_skills(
    job_skills: Any,
    skills_required: Any = None,
    description: Any = None,
    requirements: Any = None,
    structured_data: Any = None,
    job_fields: Any = None,
) -> list[str]:
    """Read the selected job's declared skills, with its JD as a fallback.

    ``skills`` is a legacy column.  ATS ingestion writes ``skills_required``
    instead, so gap guidance must consider both fields.  This helper is
    intentionally display-only; the matcher continues to own score logic.
    """
    declared: list[Any] = []

    # These are attributes, rather than a vocabulary of technologies: ATSes use
    # them at arbitrary nesting levels for their normalized skill payloads.
    skill_field_names = {
        "skills", "skill", "skills_required", "required_skills", "requiredskills",
        "technical_skills", "technicalskills", "key_skills", "keyskills",
        "technologies", "technology", "frameworks", "languages", "tools",
        "qualifications_skills", "normalized_skills",
    }

    def add(value: Any, *, allowed: bool = True) -> None:
        if value is None:
            return
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError):
                decoded = value
            if decoded is not value:
                add(decoded, allowed=allowed)
            else:
                # Corrupt serialized ATS payloads are not a skill label.
                if allowed and not value.lstrip().startswith(("{", "[")):
                    declared.extend(_normalize_skills(value))
        elif isinstance(value, dict):
            for key, child in value.items():
                # Traverse nested normalized/ATS payloads, but only admit
                # values that live beneath an explicitly skill-shaped field.
                # A skill-shaped key may occur below arbitrary ATS wrapper
                # objects (for example job.qualification.required_skills).
                # Reaching that key authorizes its values even when its
                # parents were only containers rather than skill fields.
                child_allowed = allowed or str(key).replace("-", "_").casefold() in skill_field_names
                if child_allowed or isinstance(child, (dict, list, tuple, set)):
                    add(child, allowed=child_allowed)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, dict):
                    if allowed:
                        add(item.get("name") or item.get("skill") or item.get("title"))
                    else:
                        add(item, allowed=False)
                else:
                    add(item, allowed=allowed)

    # Prefer structured/declared ATS fields over inference from prose.
    add(job_skills)
    add(skills_required)
    add(structured_data, allowed=False)
    add(job_fields, allowed=False)

    # ATS/voice structured fields are often partial (for example they may
    # contain one primary language while the JD lists its framework and data
    # stores).  Add explicitly-required JD skills to those declared fields;
    # do not let a non-empty partial payload suppress the JD.  This is
    # guidance only and intentionally leaves match scoring unchanged.
    try:
        declared.extend(_extract_required_skills_from_jd(
            "\n".join(part for part in (str(requirements or ""), str(description or "")) if part)
        ))
    except Exception:
        pass

    return _normalize_skills(declared)


def _candidate_profile_skills(candidate: dict) -> list[str]:
    """Collect all persisted candidate skill evidence, without mining prose."""
    parsed = _parse_raw_data(candidate.get("parsed_resume_json"))
    raw = _parse_raw_data(candidate.get("raw_data"))
    skill_keys = {"skills", "skill", "technical_skills", "key_skills", "competencies",
                  "technologies", "technology", "tools", "frameworks", "languages"}
    evidence: list[Any] = [candidate.get("skills") or []]

    def collect(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                normalized_key = str(child_key).replace("-", "_").casefold()
                if normalized_key in skill_keys:
                    evidence.append(child)
                # Voice/intake data frequently nests its explicit skill fields.
                if isinstance(child, (dict, list, tuple, set)):
                    collect(child, normalized_key)

    collect(parsed)
    collect(raw)
    return _normalize_skills(evidence)


_NON_SKILL_REQUIREMENT = re.compile(
    r"\b(?:location|remote|hybrid|onsite|salary|compensation|travel|clearance|"
    r"citizenship|eligible|eligibility|authorization|visa|degree|education|bachelor|"
    r"master|phd|years? of experience|equal opportunity)\b", re.I)


def _extract_required_skills_from_jd(job_text: str) -> list[str]:
    """Extract items from explicit required/qualification portions of a JD.

    This intentionally does not scan arbitrary company/product prose.  It is a
    fallback only when the job does not supply structured skill fields.
    """
    # ATS feeds commonly store an entire rich-text JD in one database string.
    # Turn the structural HTML/Markdown delimiters into lines before looking
    # for a qualifications section; otherwise a valid section and every one
    # of its bullets are invisible to the line-based parser below.
    text_value = html.unescape(str(job_text or ""))
    # Some imports retain JSON-style line separators as literal characters.
    # Treat those exactly like physical line breaks before parsing sections.
    text_value = text_value.replace("\\n", "\n").replace("\\r", "\r")
    text_value = re.sub(r"</?(?:p|li|ul|ol|h[1-6]|br)\b[^>]*>", "\n", text_value, flags=re.I)
    text_value = re.sub(r"<[^>]+>", "", text_value)
    text_value = re.sub(r"\*\*([^*]+)\*\*", r"\n\1\n", text_value)
    text_value = re.sub(r"(?<!^)[ \t]+[*•][ \t]+", "\n", text_value)
    lines = [line.strip(" \t-•*") for line in text_value.splitlines()]
    collecting = False
    extracted: list[str] = []
    # Some ATS feeds label their explicit skill list "Competencies" instead
    # of "Requirements".  Treat that as the same authoritative JD section;
    # this is still intentionally not a scan of arbitrary role prose.
    markers = re.compile(r"\b(?:requirements?|required qualifications?|must[- ]have|minimum qualifications?|what you (?:need|bring)|competencies)\b", re.I)
    stop = re.compile(r"\b(?:preferred|nice to have|benefits|why join|what we offer|about (?:us|the role)|responsibilities)\b", re.I)
    for line in lines:
        if not line:
            continue
        if markers.search(line):
            collecting = True
            line = re.sub(r"^.*?(?:requirements?|required qualifications?|must[- ]have|minimum qualifications?|what you (?:need|bring)|competencies)\s*:? ?", "", line, flags=re.I)
        elif collecting and stop.search(line):
            break
        if not collecting:
            continue
        # Preserve examples in an otherwise descriptive competency item.  For
        # example, ``relational databases (viz: PostgreSQL, SQL)`` should
        # contribute the explicitly named technologies, not the prose label.
        line = re.sub(r"[^,;]*\(\s*(?:e\.?g\.?|eg|viz\.?)\s*:?\s*([^)]+)\)", r"\1", line, flags=re.I)
        for item in re.split(r"[,;•]|\band\b", line, flags=re.I):
            item = re.sub(r"^(?:(?:strong|proven|demonstrated)\s+)?(?:hands[- ]on\s+)?(?:experience|proficiency|knowledge|expertise|familiarity)\s+(?:with|of|in\s+(?:designing|building)\s+|designing\s+|building\s+)\s*", "", item.strip(" .:-()"), flags=re.I)
            item = re.sub(r"^\d+\+?\s+years?\s+(?:of\s+)?(?:experience\s+)?(?:with|in)\s+", "", item, flags=re.I)
            item = re.sub(r"^(?:the|a|an)\s+", "", item, flags=re.I)
            item = re.sub(r"\s+(?:framework|technology|platform|tool)s?$", "", item, flags=re.I)
            if 1 < len(item) <= 60 and not _NON_SKILL_REQUIREMENT.search(item):
                # A bullet can contain a short skill list; preserve technical
                # multi-word names while rejecting sentence-like prose.
                if len(item.split()) <= 5:
                    extracted.append(item)
    return _normalize_skills(extracted)


def _job_missing_requirements(job_skills: Any, requirements: Any, candidate: dict, experience_required: Any = None,
                              *, skills_required: Any = None, description: Any = None, structured_data: Any = None,
                              job_fields: Any = None) -> dict:
    """Explain selected-job gaps without altering matcher scores."""
    def clean(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def skill_key(value: Any) -> str:
        """Return a comparison key while retaining meaningful skill symbols.

        Dots, whitespace, underscores, and hyphens are presentation variants
        for names such as ``React.js``, ``React JS``, and ``ReactJS``.  Keep
        symbols such as ``+`` and ``#`` so distinct skills (for example C++
        and C#) are not collapsed together.
        """
        return re.sub(r"[\s._-]+", "", clean(value).casefold())

    def skill_name(skill: Any) -> str:
        return clean(skill.get("name") if isinstance(skill, dict) else skill)

    # Include canonical profile skills plus the uploaded-resume/voice snapshots
    # that are actual candidate evidence but may not yet have been merged.
    current_skills = _candidate_profile_skills(candidate)
    known = {skill_key(skill_name(skill)) for skill in current_skills if skill_key(skill_name(skill))}
    missing_skills = []
    seen_job_skills = set()
    required_skills = _job_required_skills(
        job_skills, skills_required, description, requirements, structured_data, job_fields
    )
    for raw_skill in required_skills:
        skill = skill_name(raw_skill)
        key = skill_key(skill)
        if skill and key and key not in known and key not in seen_job_skills:
            missing_skills.append(skill)
            seen_job_skills.add(key)
    # Requirement prose is supplied as context for confirmation; it is never added to a profile.
    requirement_text = clean(requirements)
    requirement_lines = [line.strip(" -•\t") for line in re.split(r"[\r\n]+|(?<=[.!?])\s+", requirement_text)
                         if len(line.strip(" -•\t")) > 3]
    # Lever and other ATS feeds can provide qualification lists only inside
    # the full JD, leaving the legacy ``requirements`` column empty.  When
    # that happens, return the same extracted requirement labels used for the
    # display-only missing-skill guidance instead of an empty companion list.
    if not requirement_lines:
        requirement_lines = required_skills
    # Experience requirements are guidance only.  They are deliberately not
    # inferred into the profile or treated as candidate-provided evidence.
    experience_requirement = clean(experience_required)
    return {
        "missing_skills": missing_skills,
        "requirements": requirement_lines[:8],
        "experience_requirement": experience_requirement or None,
    }


async def _get_job_match_improvement_row(candidate_id: str, rec_id: str) -> dict:
    """Load the selected recommendation's job context after verifying ownership."""
    async with SessionLocal() as db:
        result = await db.execute(text(f"""
            SELECT cjr.match_score, jd.*
            FROM candidate_job_recommendations cjr
            JOIN job_descriptions jd ON jd.id = cjr.job_id
            WHERE cjr.id = :rid AND cjr.candidate_id = :cid
              AND {candidate_visible_where('jd')}
            LIMIT 1
        """), {"rid": rec_id, "cid": candidate_id})
        row = result.mappings().fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    return dict(row)


def _resume_editor_payload(candidate: dict) -> dict:
    """The editable representation of the candidate's uploaded resume.

    parsed_resume_json is the persisted parse of the uploaded file.  The
    canonical columns are used as fallbacks for older resumes that predate a
    particular parser field, so this endpoint never invents a second profile.
    """
    parsed = _parse_raw_data(candidate.get("parsed_resume_json"))
    raw = _parse_raw_data(candidate.get("raw_data"))
    def value(resume_key: str, candidate_key: str, default=""):
        return parsed.get(resume_key) if parsed.get(resume_key) not in (None, "") else candidate.get(candidate_key, default)
    return {
        "name": value("name", "name"), "email": value("email", "email"),
        "phone": value("phone", "phone"), "location": value("location", "location"),
        "headline": value("headline", "current_role"),
        "bio": value("bio", "summary") or value("summary", "summary"),
        # Skills have historically been enriched after the resume was parsed
        # (chat, voice intake, etc.).  The editable document must therefore
        # start with the union, not let an old parse hide canonical skills.
        "skills": _merge_skills(candidate.get("skills") or [], parsed.get("skills") or []),
        "work_experience": parsed.get("work_experience") if isinstance(parsed.get("work_experience"), list) else (candidate.get("work_experience") or []),
        "education": parsed.get("education") if isinstance(parsed.get("education"), list) else (candidate.get("education") or []),
        "certifications": parsed.get("certifications") if isinstance(parsed.get("certifications"), list) else (raw.get("certifications") or []),
        "projects": parsed.get("projects") if isinstance(parsed.get("projects"), list) else (raw.get("projects") or []),
        "experience_years": value("experience_years", "experience_years", None),
    }


async def _save_resume_editor_updates(candidate_id: str, updates: dict) -> dict:
    """Synchronize one edited resume into the canonical Eve profile.

    The same values are persisted to parsed_resume_json. Skills are merged
    with the canonical list so an older parsed-resume snapshot cannot erase
    skills added elsewhere in the Eve profile.
    """
    if not isinstance(updates, dict):
        return {"updated": False, "changed": []}
    candidate = await _get_candidate_row(candidate_id)
    editable = {"name", "email", "phone", "location", "headline", "bio", "skills", "work_experience", "education", "certifications", "projects", "experience_years"}
    supplied = {key: value for key, value in updates.items() if key in editable}
    if not supplied:
        raise HTTPException(status_code=422, detail="No resume changes were provided.")
    parsed = _parse_raw_data(candidate.get("parsed_resume_json"))
    raw = _parse_raw_data(candidate.get("raw_data"))
    next_values = _resume_editor_payload(candidate)
    next_values.update(supplied)
    for field in ("name", "email", "phone", "location", "headline", "bio"):
        next_values[field] = str(next_values.get(field) or "").strip()
    for field in ("skills", "work_experience", "education", "certifications", "projects"):
        if not isinstance(next_values.get(field), list):
            raise HTTPException(status_code=422, detail=f"{field} must be a list.")
    # The editor sends the complete skills document. Do not merge stale
    # canonical values back into it; that was preserving legacy concatenations.
    next_values["skills"] = _normalize_skills(
        next_values["skills"], certifications=next_values["certifications"]
    )
    next_values["certifications"] = _normalize_certifications(next_values["certifications"])
    next_values["projects"] = _normalize_projects(next_values["projects"])
    try:
        next_values["experience_years"] = float(next_values["experience_years"]) if next_values["experience_years"] not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="experience_years must be numeric.")
    parsed.update({
        "name": next_values["name"], "email": next_values["email"], "phone": next_values["phone"],
        "location": next_values["location"], "headline": next_values["headline"], "current_role": next_values["headline"],
        "bio": next_values["bio"], "summary": next_values["bio"], "skills": next_values["skills"],
        "work_experience": next_values["work_experience"], "education": next_values["education"],
        "certifications": next_values["certifications"], "projects": next_values["projects"],
        "experience_years": next_values["experience_years"],
    })
    raw["certifications"] = next_values["certifications"]
    raw["projects"] = next_values["projects"]
    # The download button serves this exact artifact, rather than rebuilding a
    # document from a later profile read.  It is generated from the same
    # canonical values that are about to be written to candidates.skills.
    updated_resume_path = _updated_resume_pdf_path(candidate_id)
    updated_resume_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_bytes = _build_candidate_profile_pdf(_resume_editor_pdf_profile(next_values, raw))
    updated_resume_path.write_bytes(pdf_bytes)
    raw["updated_resume_file_path"] = str(updated_resume_path)
    changed = [key for key in supplied if next_values.get(key) != _resume_editor_payload(candidate).get(key)]
    async with SessionLocal() as db:
        await db.execute(text("""
            UPDATE candidates SET name=:name, email=:email, phone=:phone, location=:location,
              "current_role"=:headline, summary=:bio, skills=CAST(:skills AS json),
              work_experience=CAST(:work_experience AS json), education=CAST(:education AS json),
              experience_years=:experience_years, raw_data=CAST(:raw_data AS jsonb),
              parsed_resume_json=CAST(:parsed_resume_json AS jsonb), updated_at=now(), updated_by_source='resume_editor'
            WHERE id=:cid
        """), {**next_values, "skills": json.dumps(next_values["skills"]), "work_experience": json.dumps(next_values["work_experience"]), "education": json.dumps(next_values["education"]), "raw_data": json.dumps(raw), "parsed_resume_json": json.dumps(parsed), "cid": candidate_id})
        await db.commit()
    return {"updated": bool(changed), "changed": changed}


@api_router.get("/candidate/{candidate_id}/jobs/{rec_id}/match-improvement")
async def get_job_match_improvement(candidate_id: str, rec_id: str):
    """Return only the selected job's gaps, scoped to an existing recommendation."""
    candidate = await _get_candidate_row(candidate_id)
    row = await _get_job_match_improvement_row(candidate_id, rec_id)
    # Return the canonical payload used by GET /profile so callers can refresh
    # their profile without trusting their local editor snapshot.
    profile = await _get_candidate_profile_payload(candidate_id)
    gaps = _job_missing_requirements(
        row["skills"], row["requirements"], candidate, row["experience_required"],
        skills_required=row.get("skills_required"), description=row.get("description"),
        structured_data=row.get("structured_data"), job_fields=row,
    )
    response_payload = {
        "match_score": float(row["match_score"]) if row["match_score"] is not None else None,
        "resume": _resume_editor_payload(candidate),
        "job_url": row.get("job_url") or None,
        **gaps,
    }
    # This trace deliberately follows the UI request boundary.  It lets a
    # production report be tied to the selected recommendation, the exact DB
    # row, the extraction result, and the JSON handed back to the modal.
    logger.info("match_improvement_trace=%s", json.dumps({
        "implementation": "match-improvement-jd-competencies-v1",
        "candidate_id": candidate_id,
        "recommendation_id": rec_id,
        "selected_job_id": str(row.get("id") or ""),
        "backend_endpoint": "/api/candidate/{candidate_id}/jobs/{rec_id}/match-improvement",
        "job_from_db": {
            "title": row.get("title"),
            "skills": row.get("skills"),
            "skills_required": row.get("skills_required"),
            "structured_data": row.get("structured_data"),
            "requirements": row.get("requirements"),
            "description": row.get("description"),
        },
        "extracted_required_skills": _job_required_skills(
            row.get("skills"), row.get("skills_required"), row.get("description"),
            row.get("requirements"), row.get("structured_data"), row,
        ),
        "candidate_skills": _candidate_profile_skills(candidate),
        "missing_skills": gaps["missing_skills"],
        "api_response": response_payload,
    }, default=str))
    return response_payload


@api_router.post("/candidate/{candidate_id}/jobs/{rec_id}/match-improvement")
async def improve_job_match(candidate_id: str, rec_id: str, request: JobMatchImprovementRequest):
    """Persist candidate-confirmed profile edits, then reuse the normal matcher."""
    guidance = await get_job_match_improvement(candidate_id, rec_id)  # ownership check; no daily-limit mutation
    job_context = await _get_job_match_improvement_row(candidate_id, rec_id)
    previous_score = guidance["match_score"]
    before = await _get_candidate_row(candidate_id)
    await _validate_resume_fix_credit_claim(candidate_id, before, request.fix_credit_claim_id)
    # Save the complete, candidate-edited uploaded-resume representation into
    # both its parsed snapshot and the canonical candidate profile.
    profile_updates = request.profile_updates
    update_result = await _save_resume_editor_updates(candidate_id, profile_updates)
    candidate = await _get_candidate_row(candidate_id)
    # Re-read the canonical profile after the resume transaction commits.  This
    # is deliberately separate from parsed_resume_json so the response matches
    # GET /profile and includes canonical fields such as merged skills.
    profile = await _get_candidate_profile_payload(candidate_id)
    try:
        from candidate_job_matching_service import refresh_candidate_job_match
        await refresh_candidate_job_match(candidate_id, rec_id, candidate, SessionLocal)
    except Exception as exc:
        logger.warning("[matching] Match refresh failed after candidate update for %s: %s", candidate_id, exc)
        raise HTTPException(status_code=503, detail="Your profile was updated, but the match could not be recalculated yet.")
    async with SessionLocal() as db:
        result = await db.execute(text("""
            SELECT match_score FROM candidate_job_recommendations
            WHERE id = :rid AND candidate_id = :cid LIMIT 1
        """), {"rid": rec_id, "cid": candidate_id})
        score = result.scalar()
    current_skills = {str(skill).strip().lower() for skill in (before.get("skills") or []) if str(skill).strip()}
    changed_skills = [str(skill) for skill in (candidate.get("skills") or []) if str(skill).strip().lower() not in current_skills]
    remaining = _job_missing_requirements(
        # Re-use the selected job's guidance rather than creating a new job or
        # substituting a recommendation from a fresh retrieval.
        job_context["skills"],
        job_context["requirements"],
        candidate,
        job_context["experience_required"],
        skills_required=job_context.get("skills_required"),
        description=job_context.get("description"),
        structured_data=job_context.get("structured_data"), job_fields=job_context,
    )
    return {
        "updated": bool(update_result.get("updated")),
        "previous_match_score": previous_score,
        "match_score": float(score) if score is not None else None,
        "changed_skills": changed_skills,
        "changes_applied": update_result.get("changed", []),
        "remaining_missing_skills": remaining["missing_skills"],
        "remaining_requirements": remaining["requirements"],
        "experience_requirement": remaining["experience_requirement"],
        "profile": profile,
        # API is already included by the frontend client base URL.  Returning
        # an API-prefixed path here produced /api/api/... and a 404.
        "resume_download_url": f"/candidate/{candidate_id}/jobs/{rec_id}/resume/download",
    }


@api_router.get("/candidate/{candidate_id}/tracked-jobs")
async def get_tracked_jobs(candidate_id: str):
    """Return only jobs the candidate has explicitly tracked."""
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        rows = await db.execute(
            text(f"""
                SELECT
                    cjr.id AS rec_id,
                    cjr.job_id,
                    cjr.match_score,
                    cjr.tracked_at,
                    jd.title,
                    jd.company_name,
                    jd.location,
                    jd.salary_range,
                    jd.description,
                    jd.requirements,
                    jd.skills,
                    jd.company_logo_url
                FROM candidate_job_recommendations cjr
                LEFT JOIN job_descriptions jd ON jd.id = cjr.job_id
                WHERE cjr.candidate_id = :cid
                  AND cjr.tracked_at IS NOT NULL
                  AND {candidate_visible_where('jd')}
                ORDER BY cjr.tracked_at DESC
            """),
            {"cid": candidate_id},
        )
        results = rows.mappings().fetchall()
    return [
        {
            "id": str(r["rec_id"]),
            "job_id": str(r["job_id"]) if r["job_id"] else None,
            "title": r["title"] or "",
            "company": r["company_name"] or "",
            "location": r["location"] or "",
            "salary": r["salary_range"] or "",
            "description": r["description"] or "",
            "logo": r["company_logo_url"] or None,
            "match_score": float(r["match_score"]) if r["match_score"] is not None else None,
            "tracked": True,
        }
        for r in results
    ]


@api_router.post("/candidate/{candidate_id}/jobs/{rec_id}/track")
async def track_job(candidate_id: str, rec_id: str):
    """Mark a recommendation as tracked by this candidate."""
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT id FROM candidate_job_recommendations WHERE id = :rid AND candidate_id = :cid LIMIT 1"),
            {"rid": rec_id, "cid": candidate_id},
        )
        if not row.fetchone():
            raise HTTPException(status_code=404, detail="Recommendation not found.")
        await db.execute(
            text("UPDATE candidate_job_recommendations SET tracked_at = now() WHERE id = :rid AND candidate_id = :cid"),
            {"rid": rec_id, "cid": candidate_id},
        )
        await db.commit()
    return {"status": "tracked"}


@api_router.delete("/candidate/{candidate_id}/jobs/{rec_id}/track")
async def untrack_job(candidate_id: str, rec_id: str):
    """Remove tracking for a recommendation."""
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT id FROM candidate_job_recommendations WHERE id = :rid AND candidate_id = :cid LIMIT 1"),
            {"rid": rec_id, "cid": candidate_id},
        )
        if not row.fetchone():
            raise HTTPException(status_code=404, detail="Recommendation not found.")
        await db.execute(
            text("UPDATE candidate_job_recommendations SET tracked_at = NULL WHERE id = :rid AND candidate_id = :cid"),
            {"rid": rec_id, "cid": candidate_id},
        )
        await db.commit()
    return {"status": "untracked"}


@api_router.post("/candidate/{candidate_id}/jobs/{rec_id}/view")
async def view_job(candidate_id: str, rec_id: str):
    """Mark a recommendation as viewed by this candidate."""
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT id FROM candidate_job_recommendations WHERE id = :rid AND candidate_id = :cid LIMIT 1"),
            {"rid": rec_id, "cid": candidate_id},
        )
        if not row.fetchone():
            raise HTTPException(status_code=404, detail="Recommendation not found.")
        await db.execute(
            text("UPDATE candidate_job_recommendations SET viewed_at = COALESCE(viewed_at, now()) WHERE id = :rid AND candidate_id = :cid"),
            {"rid": rec_id, "cid": candidate_id},
        )
        await db.commit()
    return {"status": "viewed"}


@api_router.post("/candidate/{candidate_id}/jobs/{rec_id}/dismiss")
async def dismiss_job(candidate_id: str, rec_id: str, body: Optional[JobDismissRequest] = None):
    """Hide a recommendation (Not for me)."""
    await _get_candidate_row(candidate_id)
    hidden_reason = _clean_str(body.reason) if body else ""
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT id FROM candidate_job_recommendations WHERE id = :rid AND candidate_id = :cid LIMIT 1"),
            {"rid": rec_id, "cid": candidate_id},
        )
        if not row.fetchone():
            raise HTTPException(status_code=404, detail="Recommendation not found.")
        await db.execute(
            text("""
                UPDATE candidate_job_recommendations
                SET hidden_at = now(),
                    hidden_reason = COALESCE(:reason, hidden_reason)
                WHERE id = :rid AND candidate_id = :cid
            """),
            {"rid": rec_id, "cid": candidate_id, "reason": hidden_reason or None},
        )
        await db.commit()
    return {"status": "dismissed"}


@api_router.post("/candidate/{candidate_id}/jobs/{rec_id}/apply")
async def apply_job(candidate_id: str, rec_id: str):
    """Record application: sets applied_at, updated_at, status, agency_id, job_role on this
    recommendation row only. Never touches candidates.job_id / agency_id / stage.
    One candidate may apply to many jobs; each row is independent.
    """
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT id, job_id FROM candidate_job_recommendations WHERE id = :rid AND candidate_id = :cid LIMIT 1"),
            {"rid": rec_id, "cid": candidate_id},
        )
        rec = row.mappings().fetchone()
        if not rec:
            raise HTTPException(status_code=404, detail="Recommendation not found.")

        # Fetch agency_id and title from the job — never from the candidate row
        job_agency_id = None
        job_role = None
        if rec["job_id"]:
            jd_row = await db.execute(
                text("SELECT agency_id, title FROM job_descriptions WHERE id = :jid LIMIT 1"),
                {"jid": str(rec["job_id"])},
            )
            jd = jd_row.mappings().fetchone()
            if jd:
                job_agency_id = str(jd["agency_id"]) if jd["agency_id"] else None
                job_role = jd["title"]

        await db.execute(
            text("""
                UPDATE candidate_job_recommendations
                SET applied_at  = COALESCE(applied_at, now()),
                    tracked_at  = COALESCE(tracked_at, now()),
                    status      = 'applied',
                    agency_id   = COALESCE(agency_id, :agency_id),
                    job_role    = COALESCE(job_role, :job_role),
                    updated_at  = now()
                WHERE id = :rid AND candidate_id = :cid
            """),
            {"rid": rec_id, "cid": candidate_id,
             "agency_id": job_agency_id, "job_role": job_role},
        )
        await db.commit()
    return {"status": "applied"}


# ---------- Vapi browser config endpoint ----------

@api_router.get("/config/vapi")
async def get_vapi_config():
    """Return browser-safe Vapi configuration. Never exposes private keys."""
    public_key = os.environ.get("VAPI_PUBLIC_KEY", "")
    assistant_id = os.environ.get("EVE_VAPI_ASSISTANT_ID", "")
    if not public_key or not assistant_id:
        raise HTTPException(status_code=503, detail="Voice intake is not configured.")
    return {"publicKey": public_key, "assistantId": assistant_id}


# ---------- LinkedIn OAuth ----------

import secrets
import urllib.parse
import httpx

LINKEDIN_CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
LINKEDIN_REDIRECT_URI = os.environ.get("LINKEDIN_REDIRECT_URI", "http://localhost:3000")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:3000/auth/google/callback")


@api_router.get("/auth/linkedin/init")
async def linkedin_init():
    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(status_code=503, detail="LinkedIn OAuth is not configured.")
    state = secrets.token_urlsafe(16)
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": LINKEDIN_REDIRECT_URI,
        "state": state,
        "scope": "openid profile email",
    })
    auth_url = f"https://www.linkedin.com/oauth/v2/authorization?{params}"
    return {"auth_url": auth_url, "state": state}


@api_router.get("/auth/linkedin/callback")
async def linkedin_callback(code: str, state: str):
    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(status_code=503, detail="LinkedIn OAuth is not configured.")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://www.linkedin.com/oauth/v2/accessToken",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": LINKEDIN_REDIRECT_URI,
                "client_id": LINKEDIN_CLIENT_ID,
                "client_secret": LINKEDIN_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            print("========== LINKEDIN TOKEN ERROR ==========")
            print("STATUS:", token_resp.status_code)
            print("RESPONSE:", token_resp.text)
            print("REDIRECT URI USED:", LINKEDIN_REDIRECT_URI)
            print("CLIENT ID PRESENT:", bool(LINKEDIN_CLIENT_ID))
            print("CLIENT SECRET PRESENT:", bool(LINKEDIN_CLIENT_SECRET))
            print("==========================================")
            raise HTTPException(status_code=400, detail="LinkedIn token exchange failed.")
        access_token = token_resp.json().get("access_token")

        userinfo_resp = await client.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch LinkedIn profile.")
        userinfo = userinfo_resp.json()

    linkedin_id = userinfo.get("sub", "")
    name = userinfo.get("name", "")
    email = userinfo.get("email", "")
    picture = userinfo.get("picture", "")

    candidate_id = None
    needs_onboarding = False
    async with SessionLocal() as db:
        if email:
            row = await db.execute(
                text("SELECT id, name, phone, summary, parsing_status FROM candidates WHERE email = :email LIMIT 1"),
                {"email": email},
            )
            existing = row.mappings().fetchone()
            if existing:
                candidate_id = str(existing["id"])
                # Onboarding is complete only when key profile fields are populated
                is_complete = bool(
                    existing["name"]
                    and existing["phone"]
                    and existing["summary"]
                    and existing["parsing_status"] == "completed"
                )
                needs_onboarding = not is_complete

    # Test candidate: always route to onboarding, preserving existing candidate_id
    test_email = os.environ.get("TEST_CANDIDATE_EMAIL", "").strip()
    if test_email and email.strip().lower() == test_email.lower():
        needs_onboarding = True

    profile_param = urllib.parse.quote_plus(
        json.dumps({"name": name, "email": email, "picture": picture, "linkedin_id": linkedin_id})
    )

    candidate_token = _issue_candidate_session_token(candidate_id) if candidate_id else ""
    if candidate_id and not needs_onboarding:
        redirect_url = f"{FRONTEND_URL}/dashboard?candidate_id={candidate_id}&candidate_token={candidate_token}"
    elif candidate_id and needs_onboarding:
        redirect_url = f"{FRONTEND_URL}/onboarding?candidate_id={candidate_id}&candidate_token={candidate_token}&linkedin_profile={profile_param}&needs_onboarding=true"
    else:
        redirect_url = f"{FRONTEND_URL}/onboarding?linkedin_profile={profile_param}"

    return RedirectResponse(url=redirect_url, status_code=302)


# ---------- Google OAuth ----------

@api_router.get("/auth/google/init")
async def google_init():
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")
    state = secrets.token_urlsafe(16)
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "state": state,
        "scope": "openid profile email",
        "access_type": "online",
    })
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"
    return {"auth_url": auth_url, "state": state}


@api_router.get("/auth/google/callback")
async def google_callback(code: str, state: str):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured.")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Google token exchange failed.")
        access_token = token_resp.json().get("access_token")

        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to fetch Google profile.")
        userinfo = userinfo_resp.json()

    google_id = userinfo.get("sub", "")
    name = userinfo.get("name", "")
    email = userinfo.get("email", "")
    picture = userinfo.get("picture", "")

    candidate_id = None
    needs_onboarding = False
    async with SessionLocal() as db:
        if email:
            row = await db.execute(
                text("SELECT id, name, phone, summary, parsing_status FROM candidates WHERE email = :email LIMIT 1"),
                {"email": email},
            )
            existing = row.mappings().fetchone()
            if existing:
                candidate_id = str(existing["id"])
                is_complete = bool(
                    existing["name"]
                    and existing["phone"]
                    and existing["summary"]
                    and existing["parsing_status"] == "completed"
                )
                needs_onboarding = not is_complete

    test_email = os.environ.get("TEST_CANDIDATE_EMAIL", "").strip()
    if test_email and email.strip().lower() == test_email.lower():
        needs_onboarding = True

    profile_param = urllib.parse.quote_plus(
        json.dumps({"name": name, "email": email, "picture": picture, "google_id": google_id})
    )

    candidate_token = _issue_candidate_session_token(candidate_id) if candidate_id else ""
    if candidate_id and not needs_onboarding:
        redirect_url = f"{FRONTEND_URL}/dashboard?candidate_id={candidate_id}&candidate_token={candidate_token}"
    elif candidate_id and needs_onboarding:
        redirect_url = f"{FRONTEND_URL}/onboarding?candidate_id={candidate_id}&candidate_token={candidate_token}&linkedin_profile={profile_param}&needs_onboarding=true"
    else:
        redirect_url = f"{FRONTEND_URL}/onboarding?linkedin_profile={profile_param}"

    return RedirectResponse(url=redirect_url, status_code=302)


# ---------- Service-to-service auth ----------

# Adam → Eve: Adam must present this token
EVE_INTERNAL_TOKEN = os.environ.get("EVE_INTERNAL_TOKEN", "")
# Eve → Adam: Eve presents this token when calling Adam
ADAM_INTERNAL_TOKEN = os.environ.get("ADAM_INTERNAL_TOKEN", "")


def _verify_eve_token(authorization: str = "") -> None:
    """Verify inbound requests from Adam (Adam → Eve direction)."""
    if not EVE_INTERNAL_TOKEN:
        raise HTTPException(status_code=503, detail="EVE_INTERNAL_TOKEN not configured")
    if not authorization.startswith("Bearer ") or authorization[7:] != EVE_INTERNAL_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------- Adam → Eve: candidate notification (slot booking / second round) ----------

class CandidateNotificationIn(BaseModel):
    event_id: str
    candidate_id: str
    job_id: str
    agency_id: str
    notification_type: Literal["interview_slot_booking", "second_round_invite"]
    title: str
    message: str
    booking_url: Optional[str] = None
    expires_at: Optional[str] = None
    round_name: Optional[str] = None
    scheduled_at: Optional[str] = None
    meeting_url: Optional[str] = None
    location: Optional[str] = None
    instructions: Optional[str] = None


@api_router.post("/internal/candidate-notification", status_code=201)
async def internal_candidate_notification(
    body: CandidateNotificationIn,
    authorization: Optional[str] = Header(default=None),
):
    """
    Adam → Eve: create a candidate-facing notification for slot booking or second round.
    Idempotent on event_id.
    """
    _verify_eve_token(authorization or "")

    async with SessionLocal() as db:
        # Idempotency check
        existing = await db.execute(
            text("SELECT id FROM candidate_activity_feed WHERE event_id = :eid LIMIT 1"),
            {"eid": body.event_id},
        )
        row = existing.fetchone()
        if row:
            return {"status": "duplicate", "notification_id": str(row[0])}

        # Validate candidate
        cand = await db.execute(
            text("SELECT id FROM candidates WHERE id = :cid LIMIT 1"),
            {"cid": body.candidate_id},
        )
        if not cand.fetchone():
            raise HTTPException(status_code=404, detail="candidate not found")

        # Validate job
        job = await db.execute(
            text("SELECT id, agency_id FROM job_descriptions WHERE id = :jid LIMIT 1"),
            {"jid": body.job_id},
        )
        job_row = job.mappings().fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="job not found")

        # Validate agency
        agency = await db.execute(
            text("SELECT id FROM agencies WHERE id = :aid LIMIT 1"),
            {"aid": body.agency_id},
        )
        if not agency.fetchone():
            raise HTTPException(status_code=404, detail="agency not found")

        # Validate job belongs to agency
        if str(job_row["agency_id"]) != str(body.agency_id):
            raise HTTPException(status_code=422, detail="job does not belong to agency")

        # Build type-specific metadata
        if body.notification_type == "interview_slot_booking":
            metadata = {k: v for k, v in {
                "booking_url": body.booking_url,
                "expires_at": body.expires_at,
            }.items() if v is not None}
        else:
            metadata = {k: v for k, v in {
                "round_name": body.round_name,
                "scheduled_at": body.scheduled_at,
                "meeting_url": body.meeting_url,
                "location": body.location,
                "instructions": body.instructions,
            }.items() if v is not None}

        notif_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO candidate_activity_feed
                    (id, candidate_id, activity_type, title, description,
                     metadata, is_read, event_id, created_at)
                VALUES
                    (:id, :cid, :atype, :title, :desc,
                     CAST(:meta AS jsonb), FALSE, :eid, now())
            """),
            {
                "id": notif_id,
                "cid": body.candidate_id,
                "atype": body.notification_type,
                "title": body.title,
                "desc": body.message,
                "meta": json.dumps(metadata),
                "eid": body.event_id,
            },
        )
        await db.commit()

    logger.info("[internal] candidate-notification created id=%s type=%s", notif_id, body.notification_type)
    return {"status": "created", "notification_id": notif_id}


@api_router.get("/candidate/{candidate_id}/notifications")
async def get_candidate_notifications(candidate_id: str):
    """Return candidate_activity_feed entries for this candidate, newest first."""
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        rows = await db.execute(
            text("""
                SELECT id, activity_type, title, description, metadata, is_read, event_id, created_at
                FROM candidate_activity_feed
                WHERE candidate_id = :cid
                ORDER BY created_at DESC
            """),
            {"cid": candidate_id},
        )
        results = rows.mappings().fetchall()
    return [
        {
            "id": str(r["id"]),
            "activity_type": r["activity_type"],
            "title": r["title"],
            "description": r["description"],
            "metadata": r["metadata"] or {},
            "is_read": r["is_read"],
            "event_id": str(r["event_id"]) if r["event_id"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in results
    ]


@api_router.post("/candidate/{candidate_id}/notifications/{notif_id}/read")
async def mark_notification_read(candidate_id: str, notif_id: str):
    """Mark a candidate_activity_feed entry as read."""
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        result = await db.execute(
            text("SELECT id FROM candidate_activity_feed WHERE id = :nid AND candidate_id = :cid LIMIT 1"),
            {"nid": notif_id, "cid": candidate_id},
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="notification not found")
        await db.execute(
            text("UPDATE candidate_activity_feed SET is_read = TRUE WHERE id = :nid AND candidate_id = :cid"),
            {"nid": notif_id, "cid": candidate_id},
        )
        await db.commit()
    return {"status": "ok"}


# ---------- Adam → Eve: recruiter interest ----------

class RecruiterInterestIn(BaseModel):
    adam_event_id: str          # UUID — idempotency key from Adam
    candidate_id: str           # candidates.id UUID
    job_id: str                 # job_descriptions.id UUID
    agency_id: str              # agencies.id UUID
    recruiter_user_id: Optional[str] = None
    recruiter_message: Optional[str] = None


@api_router.post("/internal/recruiter-interest", status_code=201)
async def internal_recruiter_interest(
    body: RecruiterInterestIn,
    authorization: Optional[str] = Header(default=None),
):
    """
    Adam → Eve: notify Eve that a recruiter is interested in a candidate.
    Validates candidate/job/agency existence and their relationship.
    Idempotent on adam_event_id.
    """
    _verify_eve_token(authorization or "")

    async with SessionLocal() as db:
        # Idempotency check first (cheap)
        existing = await db.execute(
            text("SELECT id FROM recruiter_interest_requests WHERE adam_event_id = :eid LIMIT 1"),
            {"eid": body.adam_event_id},
        )
        row = existing.fetchone()
        if row:
            return {"status": "duplicate", "rir_id": str(row[0])}

        # Validate candidate exists
        cand = await db.execute(
            text("SELECT id FROM candidates WHERE id = :cid LIMIT 1"),
            {"cid": body.candidate_id},
        )
        if not cand.fetchone():
            raise HTTPException(status_code=404, detail="candidate not found")

        # Validate job exists
        job = await db.execute(
            text("SELECT id, agency_id FROM job_descriptions WHERE id = :jid LIMIT 1"),
            {"jid": body.job_id},
        )
        job_row = job.mappings().fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="job not found")

        # Validate agency exists
        agency = await db.execute(
            text("SELECT id FROM agencies WHERE id = :aid LIMIT 1"),
            {"aid": body.agency_id},
        )
        if not agency.fetchone():
            raise HTTPException(status_code=404, detail="agency not found")

        # Validate job belongs to agency
        if str(job_row["agency_id"]) != str(body.agency_id):
            raise HTTPException(status_code=422, detail="job does not belong to agency")

        rir_id = str(uuid.uuid4())
        await db.execute(
            text("""
                INSERT INTO recruiter_interest_requests
                    (id, candidate_id, job_id, agency_id, recruiter_user_id,
                     recruiter_message, request_status, adam_event_id,
                     recruiter_requested_at, created_at, updated_at)
                VALUES
                    (:id, :cid, :jid, :aid, :ruid,
                     :msg, 'pending', :eid,
                     now(), now(), now())
            """),
            {
                "id": rir_id,
                "cid": body.candidate_id,
                "jid": body.job_id,
                "aid": body.agency_id,
                "ruid": body.recruiter_user_id,
                "msg": body.recruiter_message,
                "eid": body.adam_event_id,
            },
        )
        await db.commit()

    logger.info("[internal] recruiter-interest created rir_id=%s adam_event_id=%s", rir_id, body.adam_event_id)
    return {"status": "created", "rir_id": rir_id}


# ---------- Eve → Adam: candidate response ----------

ADAM_URL = os.environ.get("ADAM_INTERNAL_URL", os.environ.get("DASHBOARD_INTERNAL_URL", "")).rstrip("/")

# Retry schedule in seconds: attempts 1-5
_RETRY_DELAYS = [10, 30, 120, 600, 1800]
_MAX_ATTEMPTS = 5


class CandidateResponseIn(BaseModel):
    eve_event_id: str           # UUID — idempotency key from Eve
    adam_event_id: str          # original Adam event UUID
    candidate_id: str           # candidates.id UUID
    job_id: str                 # job_descriptions.id UUID
    agency_id: str              # agencies.id UUID
    response: Literal["interested", "not_interested"]


@api_router.post("/internal/candidate-response", status_code=200)
async def internal_candidate_response(
    body: CandidateResponseIn,
    authorization: Optional[str] = Header(default=None),
):
    """
    Internal: enqueue a candidate response for delivery to Adam.
    Idempotent on eve_event_id.
    """
    _verify_eve_token(authorization or "")

    async with SessionLocal() as db:
        # Idempotency check
        existing = await db.execute(
            text("SELECT id, status FROM eve_outbound_events WHERE eve_event_id = :eid LIMIT 1"),
            {"eid": body.eve_event_id},
        )
        row = existing.mappings().fetchone()
        if row:
            return {"status": "duplicate", "delivery_status": row["status"]}

        # Validate candidate
        cand = await db.execute(
            text("SELECT id FROM candidates WHERE id = :cid LIMIT 1"),
            {"cid": body.candidate_id},
        )
        if not cand.fetchone():
            raise HTTPException(status_code=404, detail="candidate not found")

        # Validate job
        job = await db.execute(
            text("SELECT id, agency_id FROM job_descriptions WHERE id = :jid LIMIT 1"),
            {"jid": body.job_id},
        )
        job_row = job.mappings().fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="job not found")

        # Validate agency
        agency = await db.execute(
            text("SELECT id FROM agencies WHERE id = :aid LIMIT 1"),
            {"aid": body.agency_id},
        )
        if not agency.fetchone():
            raise HTTPException(status_code=404, detail="agency not found")

        # Validate job/agency relationship
        if str(job_row["agency_id"]) != str(body.agency_id):
            raise HTTPException(status_code=422, detail="job does not belong to agency")

        # Create outbound event (pending)
        await db.execute(
            text("""
                INSERT INTO eve_outbound_events
                    (eve_event_id, adam_event_id, candidate_id, job_id, agency_id,
                     response, status, attempt_count, next_retry_at, created_at)
                VALUES
                    (:eid, :aeid, :cid, :jid, :aid,
                     :resp, 'pending', 0, now(), now())
            """),
            {
                "eid": body.eve_event_id,
                "aeid": body.adam_event_id,
                "cid": body.candidate_id,
                "jid": body.job_id,
                "aid": body.agency_id,
                "resp": body.response,
            },
        )
        await db.commit()

    # Attempt immediate delivery
    await _attempt_delivery(body.eve_event_id)
    return {"status": "accepted"}


async def _attempt_delivery(eve_event_id: str) -> bool:
    """Try to deliver one outbound event to Adam. Returns True on success."""
    if not ADAM_URL or not ADAM_INTERNAL_TOKEN:
        logger.warning("[retry] ADAM_URL or ADAM_INTERNAL_TOKEN not configured")
        return False

    async with SessionLocal() as db:
        row = await db.execute(
            text("""
                SELECT eve_event_id, adam_event_id, candidate_id, job_id, agency_id,
                       response, attempt_count
                FROM eve_outbound_events
                WHERE eve_event_id = :eid
                LIMIT 1
            """),
            {"eid": eve_event_id},
        )
        event = row.mappings().fetchone()
        if not event:
            return False

    payload = {
        "eve_event_id": str(event["eve_event_id"]),
        "adam_event_id": str(event["adam_event_id"]),
        "candidate_id": str(event["candidate_id"]),
        "job_id": str(event["job_id"]),
        "agency_id": str(event["agency_id"]),
        "response": event["response"],
    }
    headers = {"Authorization": f"Bearer {ADAM_INTERNAL_TOKEN}"}
    new_attempt = event["attempt_count"] + 1

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{ADAM_URL}/api/internal/candidate-response",
                json=payload,
                headers=headers,
            )
        if r.status_code in (200, 201, 409):
            async with SessionLocal() as db:
                await db.execute(
                    text("""
                        UPDATE eve_outbound_events
                        SET status = 'delivered', attempt_count = :ac,
                            delivered_at = now(), last_error = NULL
                        WHERE eve_event_id = :eid
                    """),
                    {"ac": new_attempt, "eid": eve_event_id},
                )
                await db.commit()
            logger.info("[delivery] delivered eve_event_id=%s attempt=%d", eve_event_id, new_attempt)
            return True
        error = f"HTTP {r.status_code}"
    except Exception as e:
        error = str(e)

    # Delivery failed — schedule next retry or mark failed
    if new_attempt >= _MAX_ATTEMPTS:
        async with SessionLocal() as db:
            await db.execute(
                text("""
                    UPDATE eve_outbound_events
                    SET status = 'failed', attempt_count = :ac, last_error = :err
                    WHERE eve_event_id = :eid
                """),
                {"ac": new_attempt, "err": error, "eid": eve_event_id},
            )
            await db.commit()
        logger.error("[delivery] permanently failed eve_event_id=%s after %d attempts: %s",
                     eve_event_id, new_attempt, error)
    else:
        delay = _RETRY_DELAYS[new_attempt - 1] if new_attempt - 1 < len(_RETRY_DELAYS) else _RETRY_DELAYS[-1]
        async with SessionLocal() as db:
            await db.execute(
                text("""
                    UPDATE eve_outbound_events
                    SET attempt_count = :ac, last_error = :err,
                        next_retry_at = now() + :delay * interval '1 second'
                    WHERE eve_event_id = :eid
                """),
                {"ac": new_attempt, "err": error, "delay": delay, "eid": eve_event_id},
            )
            await db.commit()
        logger.warning("[delivery] failed eve_event_id=%s attempt=%d next_retry_in=%ds error=%s",
                       eve_event_id, new_attempt, delay, error)
    return False


async def _retry_worker() -> None:
    """Background loop: retries pending outbound events on schedule. Recovers after restart."""
    logger.info("[retry] worker started")
    while True:
        try:
            async with SessionLocal() as db:
                rows = await db.execute(
                    text("""
                        SELECT eve_event_id FROM eve_outbound_events
                        WHERE status = 'pending'
                          AND next_retry_at <= now()
                          AND attempt_count < :max_attempts
                        ORDER BY next_retry_at
                        LIMIT 50
                    """),
                    {"max_attempts": _MAX_ATTEMPTS},
                )
                due = [str(r[0]) for r in rows.fetchall()]

            for eid in due:
                await _attempt_delivery(eid)
        except Exception as e:
            logger.warning("[retry] worker error: %s", e)

        await asyncio.sleep(5)


# ---------- Test candidate reset (dev/testing only) ----------

TEST_CANDIDATE_EMAIL = os.environ.get("TEST_CANDIDATE_EMAIL", "")
TEST_RESET_SECRET = os.environ.get("TEST_RESET_SECRET", "")


@app.post("/internal/test/reset-candidate")
async def reset_test_candidate(
    authorization: Optional[str] = Header(default=None),
):
    """
    Dev/testing only. Resets the configured test candidate's onboarding state
    so the full flow can be demonstrated from the beginning.
    Requires TEST_RESET_SECRET in Authorization header.
    Only operates on the candidate identified by TEST_CANDIDATE_EMAIL.
    """
    if not TEST_RESET_SECRET:
        raise HTTPException(status_code=503, detail="Test reset is not configured.")
    if not TEST_CANDIDATE_EMAIL:
        raise HTTPException(status_code=503, detail="TEST_CANDIDATE_EMAIL is not configured.")
    if not authorization or authorization != f"Bearer {TEST_RESET_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized.")

    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT id FROM candidates WHERE email = :email LIMIT 1"),
            {"email": TEST_CANDIDATE_EMAIL},
        )
        result = row.fetchone()

    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"Test candidate '{TEST_CANDIDATE_EMAIL}' not found. "
                   "Complete at least one full onboarding first.",
        )

    cid = str(result[0])
    logger.info("[test-reset] Resetting test candidate %s", cid)

    async with SessionLocal() as db:
        # Delete in FK-safe order (children before parent references)

        await db.execute(
            text("DELETE FROM eve_outbound_events WHERE candidate_id = :cid"),
            {"cid": cid},
        )

        await db.execute(
            text("DELETE FROM recruiter_interest_requests WHERE candidate_id = :cid"),
            {"cid": cid},
        )

        await db.execute(
            text("DELETE FROM candidate_activity_feed WHERE candidate_id = :cid"),
            {"cid": cid},
        )
        logger.info("[test-reset] Deleted candidate notifications")

        await db.execute(
            text("DELETE FROM candidate_job_recommendations WHERE candidate_id = :cid"),
            {"cid": cid},
        )
        logger.info("[test-reset] Reset candidate job/application state")

        await db.execute(
            text("DELETE FROM candidate_voice_intakes WHERE candidate_id = :cid"),
            {"cid": cid},
        )
        await db.execute(
            text("DELETE FROM candidate_voice_sessions WHERE candidate_id = :cid"),
            {"cid": cid},
        )
        logger.info("[test-reset] Reset voice intake")

        # Collect cert file paths before deleting rows
        cert_rows = await db.execute(
            text("SELECT file_path FROM candidate_certificates WHERE candidate_id = :cid"),
            {"cid": cid},
        )
        cert_paths = [r[0] for r in cert_rows.fetchall()]
        await db.execute(
            text("DELETE FROM candidate_certificates WHERE candidate_id = :cid"),
            {"cid": cid},
        )
        for p in cert_paths:
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception as e:
                    logger.warning("[test-reset] Could not delete cert file %s: %s", p, e)
        logger.info("[test-reset] Deleted certificates")

        # Collect resume file path before deleting row
        resume_row = await db.execute(
            text("SELECT source_path FROM internal_candidate_resumes WHERE candidate_id = :cid LIMIT 1"),
            {"cid": cid},
        )
        resume_result = resume_row.fetchone()
        await db.execute(
            text("DELETE FROM internal_candidate_resumes WHERE candidate_id = :cid"),
            {"cid": cid},
        )
        if resume_result and resume_result[0]:
            try:
                Path(resume_result[0]).unlink(missing_ok=True)
            except Exception as e:
                logger.warning("[test-reset] Could not delete resume file %s: %s", resume_result[0], e)
        logger.info("[test-reset] Deleted resume")

        # Reset onboarding fields; preserve id + email so LinkedIn re-auth
        # still matches this row and routes to /onboarding as a new candidate.
        await db.execute(
            text("""
                UPDATE candidates SET
                    name               = NULL,
                    phone              = NULL,
                    current_company    = NULL,
                    "current_role"     = NULL,
                    experience_years   = NULL,
                    location           = NULL,
                    summary            = NULL,
                    skills             = NULL,
                    work_experience    = NULL,
                    education          = NULL,
                    raw_data           = NULL,
                    parsing_status     = NULL,
                    resume_text        = NULL,
                    parsed_resume_json = NULL,
                    parsed_resume_text = NULL,
                    stage              = NULL,
                    stage_updated_at   = NULL,
                    updated_by_source  = 'test_reset',
                    updated_at         = now()
                WHERE id = :cid
            """),
            {"cid": cid},
        )
        logger.info("[test-reset] Reset onboarding state")

        await db.commit()

    logger.info("[test-reset] Reset complete for candidate %s", cid)
    return {
        "status": "reset",
        "candidate_id": cid,
        "message": "Test candidate reset successfully",
    }


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Total-Matching-Jobs"],
)
