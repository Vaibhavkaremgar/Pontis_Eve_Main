from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Form, Header
from fastapi.responses import FileResponse, RedirectResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
import os
import json
import logging
import hashlib
import re
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Any, Dict
import uuid
from datetime import datetime, timezone
from openai import AsyncOpenAI
import pypdf
import io
import asyncio

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

openai_client = AsyncOpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

DOCS_DIR = Path(os.environ.get("EVE_DOCS_DIR", "/tmp/eve_docs"))
DOCS_DIR.mkdir(parents=True, exist_ok=True)

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
  "education": [{"degree":"","institution":"","start_date":"","end_date":""}],
  "certifications": ["cert1"]
}
Return only the JSON object, no markdown, no explanation."""


async def _parse_resume_with_llm(resume_text: str) -> dict:
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
    return json.loads(raw)


def _normalize_for_frontend(c: dict) -> dict:
    """Map DB candidate row → Eve frontend profile shape."""
    work_exp = c.get("work_experience") or []
    experience = []
    for i, w in enumerate(work_exp):
        dates = " — ".join(filter(None, [w.get("start_date"), w.get("end_date") or "Present"]))
        experience.append({
            "id": w.get("id", f"exp-{i}"),
            "title": w.get("title", ""),
            "company": w.get("company", ""),
            "dates": dates,
            "description": w.get("description", ""),
        })

    edu_raw = c.get("education") or []
    education = []
    for i, e in enumerate(edu_raw):
        dates = " — ".join(filter(None, [e.get("start_date"), e.get("end_date", "")]))
        education.append({
            "id": e.get("id", f"edu-{i}"),
            "degree": e.get("degree", ""),
            "institution": e.get("institution", ""),
            "dates": dates,
        })

    skills_raw = c.get("skills") or []
    key_skills = [(s["name"] if isinstance(s, dict) else s) for s in skills_raw]

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
        "availability": raw_data.get("availability", ""),
        "preferred_roles": raw_data.get("preferred_roles") or [],
        "certifications": raw_data.get("certifications") or [],
        "additional_information": raw_data.get("additional_information", ""),
        "parsing_status": c.get("parsing_status", ""),
        "photo_url": raw_data.get("photo_url") or None,
    }
    if voice_intake_resume:
        profile["voice_intake_resume"] = voice_intake_resume
    return profile


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
    r"^are you ready",
    r"^(?:hi|hello|hey)[,!]?",
    r"^(?:good (?:morning|afternoon|evening))[,!]?",
    r"^(?:nice to meet you|great to meet you)",
    r"^(?:shall we (?:get started|begin|start))",
    r"^(?:ready to (?:get started|begin|start))",
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

_QUESTION_START_PATTERN = re.compile(
    r"\b(?:"
    r"what(?:'s| is| kind of| type of| would| do| are)?|"
    r"which|how|who|why|where|when|"
    r"tell me|can you|could you|would you|do you|are you|is your"
    r")\b",
    re.IGNORECASE,
)


def _extract_question_from_assistant_turn(text: str) -> Optional[str]:
    """
    Extract the actual intake question from an assistant turn.
    Handles both '?' questions and statement-form intake prompts.
    """
    cleaned = _clean_str(text)
    if not cleaned:
        return None

    clauses = [c.strip() for c in re.split(r"(?<=[?.!])\s+", cleaned) if c.strip()]

    # Check statement-form intake prompts (e.g. "Tell me about your background.")
    for clause in reversed(clauses):
        lowered = clause.lower().rstrip(" .!?")
        if any(re.search(p, lowered) for p in _INTAKE_STATEMENT_PATTERNS):
            return clause

    question_clause = next((clause for clause in reversed(clauses) if "?" in clause), "")
    if not question_clause:
        return None

    question_text = question_clause[: question_clause.rfind("?") + 1]
    matches = list(_QUESTION_START_PATTERN.finditer(question_text))
    if matches:
        question_text = question_text[matches[-1].start():]

    # Collapse fragment separators while preserving the actual words.
    question_text = re.sub(r"[.,;:]+", " ", question_text)
    question_text = re.sub(r"\b(?:um|uh|er|ah)\b", " ", question_text, flags=re.IGNORECASE)
    question_text = re.sub(r"\s+", " ", question_text).strip(" ,.-")
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
    return question_text


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
            # Non-question, non-acknowledgement assistant turn (setup chatter, statement)
            # Only flush+reset if we have no accumulated answer yet; otherwise keep
            # the pending question open (Eve may be giving context before re-asking)
            if not answer_parts:
                pending_question = None

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
    source_turns = new_turns if normalized_incoming_notes else existing_turns
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

    # Never mark completed if there are still missing topics or a pending question
    if missing_topics or pending_question:
        is_completed = False

    if existing_status == "completed":
        return existing_resume  # type: ignore[return-value]

    status = "completed" if is_completed else "in_progress"
    current_question = pending_question
    if not current_question and existing_current_q and not answered_question:
        current_question = existing_current_q
    if not current_question and answered_question and next_question and not _question_in_completed_turns(next_question, completed_turns):
        current_question = next_question
    if current_question and next_question and _questions_are_rephrasing(current_question, next_question):
        next_question = ""

    resume: dict = {
        "status": status,
        "progress": progress,
        "voice_notes": _normalize_voice_notes(voice_notes, transcript) or (existing_resume or {}).get("voice_notes") or [],
        "completed_turns": completed_turns,
        "has_open_question": bool(current_question or next_question),
        "known_topics": known_topics,
        "missing_topics": missing_topics,
    }
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
    existing = await _get_candidate_row(candidate_id)
    raw_data = _parse_raw_data(existing.get("raw_data"))
    raw_data["voice_intake"] = dict(resume)

    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE candidates SET raw_data = CAST(:rd AS jsonb), updated_at = now(), updated_by_source = 'eve_voice' WHERE id = :cid"),
            {"rd": json.dumps(raw_data), "cid": candidate_id},
        )
        await db.commit()


async def _get_candidate_row(candidate_id: str) -> dict:
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT * FROM candidates WHERE id = :cid LIMIT 1"),
            {"cid": candidate_id},
        )
        result = row.mappings().fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="Candidate not found.")
    return dict(result)


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
    skills_json = json.dumps(parsed.get("skills") or [])
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

            # UPDATE existing candidate — preserve raw_data, write all resume fields
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
                    "name": parsed.get("name", ""),
                    "email": parsed.get("email", ""),
                    "phone": parsed.get("phone", ""),
                    "current_role": parsed.get("current_role") or parsed.get("headline", ""),
                    "current_company": parsed.get("current_company", ""),
                    "location": parsed.get("location", ""),
                    "summary": parsed.get("bio") or parsed.get("summary", ""),
                    "skills": skills_json,
                    "work_experience": work_exp_json,
                    "education": edu_json,
                    "exp_years": parsed.get("experience_years"),
                    "raw_data": json.dumps(existing_raw_data),
                    "resume_file_path": str(dest_path),
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
                         parsing_status, resume_file_path, resume_text, resume_received_at,
                         parsed_resume_json, parsed_resume_text,
                         created_at, updated_at)
                    VALUES
                        (:cid, :name, :email, :phone, :current_role, :current_company,
                         :location, :summary, CAST(:skills AS json), CAST(:work_experience AS json), CAST(:education AS json),
                         :exp_years, 'eve', 'eve', 'eve',
                         'completed', :resume_file_path, :resume_text, now(),
                         CAST(:parsed_resume_json AS jsonb), :parsed_resume_text,
                         now(), now())
                """),
                {
                    "cid": cid,
                    "name": parsed.get("name", ""),
                    "email": parsed.get("email", ""),
                    "phone": parsed.get("phone", ""),
                    "current_role": parsed.get("current_role") or parsed.get("headline", ""),
                    "current_company": parsed.get("current_company", ""),
                    "location": parsed.get("location", ""),
                    "summary": parsed.get("bio") or parsed.get("summary", ""),
                    "skills": skills_json,
                    "work_experience": work_exp_json,
                    "education": edu_json,
                    "exp_years": parsed.get("experience_years"),
                    "resume_file_path": str(dest_path),
                    "resume_text": resume_text,
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
                {"fn": original_filename, "sp": str(dest_path), "fp": fingerprint, "cid": cid},
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
                    "fn": original_filename, "sp": str(dest_path), "fp": fingerprint,
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
        await refresh_candidate_job_matches(candidate_id, candidate, SessionLocal)
    except Exception as e:
        logger.warning("[matching] Failed for candidate %s: %s", candidate_id, e)


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

    # If an existing_id is provided (e.g. test candidate re-onboarding), update that record.
    # Otherwise force_new=True to create a fresh record for a genuinely new candidate.
    if existing_id:
        cid = await _upsert_candidate(parsed, fingerprint, file_bytes, file.filename or "resume.pdf", resume_text=resume_text, existing_id=existing_id)
    else:
        cid = await _upsert_candidate(parsed, fingerprint, file_bytes, file.filename or "resume.pdf", resume_text=resume_text, force_new=True)

    asyncio.ensure_future(_trigger_matching(cid))

    profile = _normalize_for_frontend({**parsed, "id": cid})
    profile["_meta"] = {"used_ocr": used_ocr}
    return profile


@api_router.post("/candidate/{candidate_id}/photo")
async def upload_profile_photo(candidate_id: str, file: UploadFile = File(...)):
    await _get_candidate_row(candidate_id)
    allowed = ("image/jpeg", "image/png", "image/webp", "image/gif")
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported image type.")
    file_bytes = await file.read()
    if len(file_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image must be smaller than 5 MB.")
    dest_dir = DOCS_DIR / candidate_id / "photo"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "photo").suffix or ".jpg"
    dest_path = dest_dir / f"{uuid.uuid4()}{ext}"
    dest_path.write_bytes(file_bytes)

    photo_url = f"/api/candidate/{candidate_id}/photo/view"

    existing = await _get_candidate_row(candidate_id)
    raw_data = _parse_raw_data(existing.get("raw_data"))
    # Remove old photo file if present
    old_path = raw_data.get("photo_file_path")
    if old_path and old_path != str(dest_path):
        try:
            Path(old_path).unlink(missing_ok=True)
        except Exception:
            pass
    raw_data["photo_url"] = photo_url
    raw_data["photo_file_path"] = str(dest_path)
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE candidates SET raw_data = CAST(:rd AS jsonb), updated_at = now() WHERE id = :cid"),
            {"rd": json.dumps(raw_data), "cid": candidate_id},
        )
        await db.commit()
    return {"photo_url": photo_url}


@api_router.get("/candidate/{candidate_id}/photo/view")
async def view_profile_photo(candidate_id: str):
    existing = await _get_candidate_row(candidate_id)
    raw_data = _parse_raw_data(existing.get("raw_data"))
    file_path = raw_data.get("photo_file_path")
    if not file_path or not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="No profile photo.")
    suffix = Path(file_path).suffix.lower()
    media_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                  "webp": "image/webp", "gif": "image/gif"}.get(suffix.lstrip("."), "image/jpeg")
    return FileResponse(file_path, media_type=media_type)


@api_router.get("/candidate/{candidate_id}/chat")
async def get_candidate_chat(candidate_id: str):
    """Return the persisted short-term chat window for a candidate."""
    await _get_candidate_row(candidate_id)
    messages = await _load_chat_window(candidate_id)
    return {"messages": messages}


@api_router.get("/candidate/{candidate_id}/profile")
async def get_candidate_profile(candidate_id: str):
    row = await _get_candidate_row(candidate_id)
    return _normalize_for_frontend(row)


@api_router.get("/candidate/{candidate_id}/documents")
async def get_candidate_documents(candidate_id: str):
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        resume_row = await db.execute(
            text("SELECT source_filename FROM internal_candidate_resumes WHERE candidate_id = :cid ORDER BY created_at DESC LIMIT 1"),
            {"cid": candidate_id},
        )
        resume = resume_row.fetchone()
        certs_rows = await db.execute(
            text("SELECT id, file_name FROM candidate_certificates WHERE candidate_id = :cid ORDER BY created_at ASC"),
            {"cid": candidate_id},
        )
        certs = certs_rows.fetchall()
    return {
        "resume": {"filename": resume[0]} if resume else None,
        "certificates": [{"id": str(r[0]), "filename": r[1]} for r in certs],
    }


@api_router.get("/candidate/{candidate_id}/resume/view")
async def view_resume(candidate_id: str, download: bool = False):
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT source_filename, source_path FROM internal_candidate_resumes WHERE candidate_id = :cid ORDER BY created_at DESC LIMIT 1"),
            {"cid": candidate_id},
        )
        result = row.fetchone()
    if not result:
        raise HTTPException(status_code=404, detail="No resume found.")
    filename, source_path = result[0], result[1]
    file_path = Path(source_path) if source_path else None
    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail="Resume file not available.")
    disposition = "attachment" if download else "inline"
    return FileResponse(
        str(file_path), media_type="application/pdf", filename=filename,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@api_router.delete("/candidate/{candidate_id}/resume")
async def delete_resume(candidate_id: str):
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
    if source_path:
        try:
            Path(source_path).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Could not delete resume file %s: %s", source_path, e)
    return {"status": "deleted"}


@api_router.post("/candidate/{candidate_id}/resume/replace")
async def replace_resume(candidate_id: str, file: UploadFile = File(...)):
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

    profile = _normalize_for_frontend({**parsed, "id": candidate_id})
    return {"status": "replaced", "filename": file.filename, "profile": profile}


@api_router.post("/candidate/{candidate_id}/certificates/upload")
async def upload_certificate(candidate_id: str, file: UploadFile = File(...)):
    await _get_candidate_row(candidate_id)
    allowed = (".pdf", ".png", ".jpg", ".jpeg")
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
            {"id": cert_id, "cid": candidate_id, "fn": file.filename, "fp": str(dest_path)},
        )
        await db.commit()
    return {"id": cert_id, "filename": file.filename}


@api_router.get("/candidate/{candidate_id}/certificates/{cert_id}/view")
async def view_certificate(candidate_id: str, cert_id: str, download: bool = False):
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
    path = Path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Certificate file not available.")
    suffix = path.suffix.lower()
    media_type = "application/pdf" if suffix == ".pdf" else f"image/{suffix.lstrip('.')}"
    disposition = "attachment" if download else "inline"
    return FileResponse(
        str(path), media_type=media_type, filename=filename,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@api_router.delete("/candidate/{candidate_id}/certificates/{cert_id}")
async def delete_certificate(candidate_id: str, cert_id: str):
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
    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Could not delete certificate file %s: %s", file_path, e)
    return {"status": "deleted"}


@api_router.post("/candidate/{candidate_id}/certificates/{cert_id}/replace")
async def replace_certificate(candidate_id: str, cert_id: str, file: UploadFile = File(...)):
    await _get_candidate_row(candidate_id)
    allowed = (".pdf", ".png", ".jpg", ".jpeg")
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

    old_path = Path(result[0])
    if old_path.exists():
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
            {"fn": file.filename, "fp": str(dest_path), "cid": cert_id, "owner": candidate_id},
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

BEHAVIOR:
- ALWAYS answer the candidate's current message FIRST and DIRECTLY, using the candidate profile above. Do not redirect to job search or any other topic unless the candidate's message explicitly asks for it.
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
- Do NOT change open_to_opportunities unless the candidate explicitly asks.
- Do NOT overwrite fields that already have good data unless the candidate is correcting them.

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
  work_experience: List of job objects. Each object must have:
                     {{"title": "<job title>", "company": "<company name>", "description": "<responsibilities>"}}
                   title   = the role/position held (e.g. "Python Backend Developer")
                   company = the employer name (e.g. "ABC Technologies")
                   description = what they did (technologies used, responsibilities)
                   Do NOT put a technology name as title. Do NOT put a company name as title.
  name, email, phone, location, bio: plain string fields.
  education      : List of {{"degree": "", "institution": "", "start_date": "", "end_date": ""}}.

EXAMPLE — if candidate says "I worked at ABC Technologies as a Python Backend Developer. I built REST APIs with FastAPI and PostgreSQL.":
  <<<PROFILE_UPDATES>>>
  {{"profile_updates": {{
    "current_role": "Python Backend Developer",
    "work_experience": [{{"title": "Python Backend Developer", "company": "ABC Technologies", "description": "Built REST APIs using FastAPI and PostgreSQL."}}],
    "skills": ["Python", "FastAPI", "PostgreSQL", "REST APIs"]
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
    add("Salary Expectation", raw_data.get("salary_expectation"), required=False)
    add("Work Type Preference", raw_data.get("work_type_preference"), required=False)
    add("Career Goals", raw_data.get("career_goals"), required=False)
    add("Location Preferences", raw_data.get("location_preferences"), required=False)
    add("Target Industries", raw_data.get("target_industries"), required=False)

    return "\n".join(lines) if lines else "No profile data yet.", missing


def _extract_profile_updates(reply_text: str) -> tuple[str, Optional[dict]]:
    """Split LLM reply into (clean_reply, profile_updates_dict)."""
    marker_start = "<<<PROFILE_UPDATES>>>"
    marker_end = "<<<END_UPDATES>>>"
    if marker_start not in reply_text:
        return reply_text.strip(), None
    parts = reply_text.split(marker_start, 1)
    clean = parts[0].strip()
    rest = parts[1].split(marker_end, 1)[0].strip()
    try:
        data = json.loads(rest)
        updates = data.get("profile_updates")
        return clean, updates if isinstance(updates, dict) else None
    except Exception:
        return clean, None


VALID_UPDATE_FIELDS = {
    "name", "email", "phone", "location", "headline", "bio",
    "current_role", "experience_years", "skills", "work_experience", "education",
}


async def _apply_profile_updates(candidate_id: str, updates: dict) -> None:
    """Validate and apply structured profile updates to PostgreSQL, merging lists."""
    safe = {k: v for k, v in updates.items() if k in VALID_UPDATE_FIELDS and v is not None}
    if not safe:
        return

    # Fetch existing candidate row for merging lists
    existing = await _get_candidate_row(candidate_id)

    set_clauses = []
    params: dict = {"cid": candidate_id}
    current_role_set = False

    for field, value in safe.items():
        if field == "skills":
            if not isinstance(value, list):
                continue
            merged = _merge_skills(existing.get("skills") or [], value)
            set_clauses.append("skills = CAST(:skills AS json)")
            params["skills"] = json.dumps(merged)
        elif field == "work_experience":
            if not isinstance(value, list):
                continue
            merged = _merge_work_experience(existing.get("work_experience") or [], value)
            set_clauses.append("work_experience = CAST(:work_experience AS json)")
            params["work_experience"] = json.dumps(merged)
        elif field == "education":
            if not isinstance(value, list):
                continue
            merged = _merge_education(existing.get("education") or [], value)
            set_clauses.append("education = CAST(:education AS json)")
            params["education"] = json.dumps(merged)
        elif field in ("headline", "current_role"):
            if not current_role_set:
                set_clauses.append('"current_role" = :current_role')
                params["current_role"] = str(value)
                current_role_set = True
        elif field == "bio":
            set_clauses.append("summary = :bio")
            params["bio"] = str(value)
        elif field == "experience_years":
            try:
                set_clauses.append("experience_years = :experience_years")
                params["experience_years"] = float(value)
            except (TypeError, ValueError):
                pass
        else:
            set_clauses.append(f"{field} = :{field}")
            params[field] = str(value)

    if not set_clauses:
        return

    set_clauses.append("updated_at = now()")
    set_clauses.append("updated_by_source = 'eve_chat'")

    async with SessionLocal() as db:
        await db.execute(
            text(f"UPDATE candidates SET {', '.join(set_clauses)} WHERE id = :cid"),
            params,
        )
        await db.commit()


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
    if request.candidate_id:
        try:
            row = await _get_candidate_row(request.candidate_id)
            raw_data = row.get("raw_data") or {}
            if isinstance(raw_data, str):
                try:
                    raw_data = json.loads(raw_data)
                except Exception:
                    raw_data = {}
            frontend_profile = _normalize_for_frontend(row)
            frontend_profile["raw_data"] = raw_data
            profile_context, missing_fields = _build_profile_context(frontend_profile)
            voice_resume = frontend_profile.get("voice_intake_resume") or _build_voice_intake_resume(frontend_profile)
            if voice_resume:
                profile_context += "\n\nVOICE INTAKE RESUME:\n" + _format_voice_intake_resume_context(voice_resume)
            persisted_window = await _load_chat_window(request.candidate_id)
        except HTTPException:
            pass

    # If the user is explicitly asking for job matches, retrieve real jobs first
    job_context = ""
    if request.candidate_id and _is_job_search_request(last_user.content):
        try:
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
        except Exception as e:
            logger.warning("[chat] Job retrieval/matching failed for candidate %s: %s", request.candidate_id, e)
            job_context = "\n\nJob search attempted but no results could be retrieved at this time."

    system_prompt = EVE_SYSTEM_TEMPLATE.format(
        profile_context=profile_context + job_context,
        missing_fields=", ".join(missing_fields) if missing_fields else "None",
    )

    # Build message list: use the incoming request messages as the source of truth for the
    # current conversation turn. The persisted window is only used to backfill history that
    # the frontend did not send (i.e. messages older than the current request window).
    incoming = [{"role": m.role, "content": m.content} for m in request.messages]

    if persisted_window and incoming:
        incoming_set = {(m["role"], m["content"]) for m in incoming}
        older = [m for m in persisted_window if (m["role"], m["content"]) not in incoming_set]
        combined = older + incoming
    else:
        combined = incoming

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

    clean_reply, profile_updates = _extract_profile_updates(raw_reply)

    # Persist updated window (includes assistant reply)
    if request.candidate_id:
        updated_window = combined + [{"role": "assistant", "content": clean_reply}]
        asyncio.ensure_future(_save_chat_window(request.candidate_id, request.session_id, updated_window))

    # Apply profile updates to PostgreSQL
    if profile_updates and request.candidate_id:
        try:
            await _apply_profile_updates(request.candidate_id, profile_updates)
            asyncio.ensure_future(_trigger_matching(request.candidate_id))
        except Exception as e:
            logger.warning("Profile update failed: %s", e)
            profile_updates = None

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


async def _ensure_voice_intake_table():
    async with SessionLocal() as db:
        await db.execute(text(CREATE_VOICE_INTAKES_TABLE))
        await db.execute(text(CREATE_VOICE_INTAKES_INDEX))
        await db.commit()


async def _ensure_schema():
    async with SessionLocal() as db:
        await db.execute(text(CREATE_VOICE_INTAKES_TABLE))
        await db.execute(text(CREATE_VOICE_INTAKES_INDEX))
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
  "location": "",
  "preferred_roles": [],
  "current_role": "",
  "current_company": "",
  "work_experience": [{"title":"","company":"","description":""}],
  "education": [{"degree":"","institution":""}],
  "certifications": [],
  "additional_information": "",
  "confidence": 0.0
}
Only include fields where the candidate actually provided information.
Do NOT invent or hallucinate information.
For "role_preference_bio": if the candidate mentions the type of roles they are looking for or their career preferences, write a concise bio sentence capturing that preference (e.g. "Looking for Python Backend roles involving FastAPI and AI"). Do NOT include specific company names. Leave empty if no role preference was mentioned.
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
        return json.loads(raw)
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


def _merge_skills(existing: list, new_items: list) -> list:
    """Merge skill lists with case-insensitive deduplication."""
    if not new_items:
        return existing
    seen = {str(x).lower() for x in existing}
    merged = list(existing)
    for item in new_items:
        s = item["name"] if isinstance(item, dict) else str(item)
        if s.lower() not in seen:
            merged.append(item)
            seen.add(s.lower())
    return merged


def _work_exp_key(entry: dict) -> str:
    """Normalised key for deduplicating work experience entries."""
    title = str(entry.get("title") or "").lower().strip()
    company = str(entry.get("company") or "").lower().strip()
    return f"{company}|{title}"


def _merge_work_experience(existing: list, new_items: list) -> list:
    """
    Merge work experience lists.
    When the same company+title appears in both, merge descriptions rather than
    creating a duplicate entry. New entries are appended.
    """
    if not new_items:
        return existing
    merged = [dict(e) for e in existing]
    existing_keys = {_work_exp_key(e): i for i, e in enumerate(merged)}
    for item in new_items:
        key = _work_exp_key(item)
        if key in existing_keys:
            idx = existing_keys[key]
            existing_desc = merged[idx].get("description") or ""
            new_desc = item.get("description") or ""
            if new_desc and new_desc.lower() not in existing_desc.lower():
                merged[idx]["description"] = f"{existing_desc} {new_desc}".strip()
        else:
            merged.append(dict(item))
            existing_keys[key] = len(merged) - 1
    return merged


def _merge_education(existing: list, new_items: list) -> list:
    """Merge education lists, deduplicating by degree+institution."""
    if not new_items:
        return existing
    merged = list(existing)
    seen = set()
    for e in existing:
        key = f"{str(e.get('institution') or '').lower()}|{str(e.get('degree') or '').lower()}"
        seen.add(key)
    for item in new_items:
        key = f"{str(item.get('institution') or '').lower()}|{str(item.get('degree') or '').lower()}"
        if key not in seen:
            merged.append(item)
            seen.add(key)
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


def _merge_voice_into_profile(existing: dict, voice: dict) -> dict:
    """
    Safely merge voice-extracted data into existing candidate profile.
    - Fills missing fields from voice
    - Merges skills (deduped), work_experience (deduped by company+title), education (deduped)
    - Allows voice to update current_role only when it is clearly more specific
    - Stores availability, preferred_roles, certifications, additional_information in raw_data
    """
    merged = dict(existing)

    # Fill missing scalar fields
    for key in ("current_company", "location"):
        if voice.get(key) and not merged.get(key):
            merged[key] = voice[key]

    # summary: fill if missing; update with role_preference_bio if candidate stated preferences
    if voice.get("role_preference_bio"):
        merged["summary"] = voice["role_preference_bio"]
    elif voice.get("summary") and not merged.get("summary"):
        merged["summary"] = voice["summary"]

    # current_role: fill if missing, or update if voice provides a more specific title
    voice_role = (voice.get("current_role") or "").strip()
    existing_role = (merged.get("current_role") or "").strip()
    if voice_role:
        if not existing_role:
            merged["current_role"] = voice_role
        elif _is_more_specific_role(existing_role, voice_role):
            merged["current_role"] = voice_role

    # experience_years: fill if missing
    if voice.get("experience_years") and not merged.get("experience_years"):
        try:
            merged["experience_years"] = float(voice["experience_years"])
        except (TypeError, ValueError):
            pass

    # Merge lists
    merged["skills"] = _merge_skills(
        merged.get("skills") or [], voice.get("skills") or []
    )
    merged["work_experience"] = _merge_work_experience(
        merged.get("work_experience") or [], voice.get("work_experience") or []
    )
    merged["education"] = _merge_education(
        merged.get("education") or [], voice.get("education") or []
    )

    # Store voice-only fields in raw_data (no new DB columns needed)
    existing_raw = merged.get("raw_data") or {}
    if isinstance(existing_raw, str):
        try:
            existing_raw = json.loads(existing_raw)
        except Exception:
            existing_raw = {}
    raw_data = dict(existing_raw)

    if voice.get("availability"):
        raw_data["availability"] = voice["availability"]
    if voice.get("preferred_roles"):
        existing_pr = raw_data.get("preferred_roles") or []
        seen_pr = {r.lower() for r in existing_pr}
        for r in voice["preferred_roles"]:
            if r.lower() not in seen_pr:
                existing_pr.append(r)
                seen_pr.add(r.lower())
        raw_data["preferred_roles"] = existing_pr
    if voice.get("certifications"):
        existing_certs = raw_data.get("certifications") or []
        seen_certs = {c.lower() for c in existing_certs}
        for cert in voice["certifications"]:
            if isinstance(cert, str) and cert.lower() not in seen_certs:
                existing_certs.append(cert)
                seen_certs.add(cert.lower())
        raw_data["certifications"] = existing_certs
        # Also merge certifications into skills so they appear in profile
        merged["skills"] = _merge_skills(merged.get("skills") or [], voice["certifications"])
    if voice.get("additional_information"):
        raw_data["additional_information"] = voice["additional_information"]

    merged["raw_data"] = raw_data
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

    # 5. Merge into existing profile
    merged = _merge_voice_into_profile(candidate, voice_data)

    # 6. Build update params
    update_params: dict = {"cid": request.candidate_id}
    set_clauses = []

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

    # Always persist raw_data (availability, preferred_roles, certifications, etc.)
    merged_raw = merged.get("raw_data") or {}
    merged_raw["voice_intake"] = voice_intake_state
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

    # 7. Mark intake as completed only when the actual intake questions have all been answered.
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
    updated_profile = _normalize_for_frontend(updated_candidate)
    updated_profile["voice_intake_resume"] = voice_intake_state

    return {
        "status": voice_intake_state["status"],
        "intake_id": intake_id,
        "candidate_id": request.candidate_id,
        "fields_updated": [c.split(" ")[0].strip('"') for c in set_clauses
                           if not c.startswith("updated") and not c.startswith("raw_data")],
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
    await _get_candidate_row(candidate_id)
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

@api_router.get("/candidate/{candidate_id}/jobs")
async def get_candidate_jobs(candidate_id: str):
    """Return semantic job recommendations for this candidate, joined with job_descriptions."""
    candidate = await _get_candidate_row(candidate_id)

    # If no recommendations exist yet, run matching synchronously so the first load is useful
    async with SessionLocal() as db:
        count_row = await db.execute(
            text("SELECT COUNT(*) FROM candidate_job_recommendations WHERE candidate_id = :cid"),
            {"cid": candidate_id},
        )
        rec_count = count_row.scalar() or 0

    if rec_count == 0:
        try:
            from candidate_job_matching_service import refresh_candidate_job_matches
            await refresh_candidate_job_matches(candidate_id, candidate, SessionLocal)
        except Exception as e:
            logger.warning("[matching] On-demand matching failed for %s: %s", candidate_id, e)

    async with SessionLocal() as db:
        rows = await db.execute(
            text("""
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
                    jd.description,
                    jd.requirements,
                    jd.skills,
                    jd.company_logo_url,
                    jd.job_url
                FROM candidate_job_recommendations cjr
                LEFT JOIN job_descriptions jd ON jd.id = cjr.job_id
                WHERE cjr.candidate_id = :cid
                  AND cjr.hidden_at IS NULL
                ORDER BY cjr.recommendation_rank ASC NULLS LAST, cjr.match_score DESC NULLS LAST
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
        }
        for r in results
    ]


@api_router.get("/candidate/{candidate_id}/tracked-jobs")
async def get_tracked_jobs(candidate_id: str):
    """Return only jobs the candidate has explicitly tracked."""
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        rows = await db.execute(
            text("""
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
async def dismiss_job(candidate_id: str, rec_id: str):
    """Hide a recommendation (Not for me)."""
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT id FROM candidate_job_recommendations WHERE id = :rid AND candidate_id = :cid LIMIT 1"),
            {"rid": rec_id, "cid": candidate_id},
        )
        if not row.fetchone():
            raise HTTPException(status_code=404, detail="Recommendation not found.")
        await db.execute(
            text("UPDATE candidate_job_recommendations SET hidden_at = now() WHERE id = :rid AND candidate_id = :cid"),
            {"rid": rec_id, "cid": candidate_id},
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

    if candidate_id and not needs_onboarding:
        redirect_url = f"{FRONTEND_URL}/dashboard?candidate_id={candidate_id}"
    elif candidate_id and needs_onboarding:
        redirect_url = f"{FRONTEND_URL}/onboarding?candidate_id={candidate_id}&linkedin_profile={profile_param}&needs_onboarding=true"
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

    if candidate_id and not needs_onboarding:
        redirect_url = f"{FRONTEND_URL}/dashboard?candidate_id={candidate_id}"
    elif candidate_id and needs_onboarding:
        redirect_url = f"{FRONTEND_URL}/onboarding?candidate_id={candidate_id}&linkedin_profile={profile_param}&needs_onboarding=true"
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
)
