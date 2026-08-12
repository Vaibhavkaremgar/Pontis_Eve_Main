from fastapi import FastAPI, APIRouter, HTTPException, UploadFile, File, Form, Header
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
import os
import json
import logging
import hashlib
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
        model="llama-3.3-70b-versatile",
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

    return {
        "candidate_id": str(c.get("id") or c.get("candidate_id") or ""),
        "name": c.get("name", ""),
        "email": c.get("email", ""),
        "phone": c.get("phone", ""),
        "location": c.get("location", ""),
        "headline": c.get("current_role", "") or c.get("headline", ""),
        "bio": c.get("summary", ""),
        "experience_years": c.get("experience_years") or c.get("total_experience_years"),
        "keySkills": key_skills,
        "experience": experience,
        "education": education,
        "availability": raw_data.get("availability", ""),
        "preferred_roles": raw_data.get("preferred_roles") or [],
        "certifications": raw_data.get("certifications") or [],
        "additional_information": raw_data.get("additional_information", ""),
    }


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
                             original_filename: str, existing_id: Optional[str] = None,
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

        if cid:
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

            # UPDATE existing candidate — preserve raw_data (voice-derived fields)
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
                    "cid": cid,
                },
            )
        else:
            # INSERT new candidate
            cid = str(uuid.uuid4())
            logger.info("[parse-resume] inserting new candidate_id=%s", cid)
            await db.execute(
                text("""
                    INSERT INTO candidates
                        (id, name, email, phone, "current_role", current_company,
                         location, summary, skills, work_experience, education,
                         experience_years, source, created_by_source, updated_by_source,
                         parsing_status, created_at, updated_at)
                    VALUES
                        (:cid, :name, :email, :phone, :current_role, :current_company,
                         :location, :summary, CAST(:skills AS json), CAST(:work_experience AS json), CAST(:education AS json),
                         :exp_years, 'eve', 'eve', 'eve',
                         'completed', now(), now())
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
                },
            )

        # Store resume file
        dest_dir = DOCS_DIR / cid / "resume"
        dest_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid.uuid4()}.pdf"
        dest_path = dest_dir / stored_name
        dest_path.write_bytes(file_bytes)

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
async def parse_resume(file: UploadFile = File(...)):
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

    cid = await _upsert_candidate(parsed, fingerprint, file_bytes, file.filename or "resume.pdf", force_new=True)

    asyncio.ensure_future(_trigger_matching(cid))

    profile = _normalize_for_frontend({**parsed, "id": cid})
    profile["_meta"] = {"used_ocr": used_ocr}
    return profile


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
async def view_resume(candidate_id: str):
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
    return FileResponse(str(file_path), media_type="application/pdf", filename=filename)


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

    await _upsert_candidate(parsed, fingerprint, file_bytes, file.filename or "resume.pdf", existing_id=candidate_id)

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
async def view_certificate(candidate_id: str, cert_id: str):
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
    return FileResponse(str(path), media_type=media_type, filename=filename)


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


# ---------- Chat with candidate context + structured profile updates ----------

EVE_SYSTEM_TEMPLATE = """You are Eve, the candidate-side AI recruitment agent on the Pontis platform.
You help candidates refine their profile, discover matching roles, and prep for outreach.

STYLE: Warm, concise, action-oriented. 2-3 short sentences per reply unless depth is requested.
Speak as a trusted career partner. No emojis. No markdown headers.

CANDIDATE PROFILE (current state):
{profile_context}

MISSING FIELDS: {missing_fields}

BEHAVIOR:
- If important profile fields are missing, ask ONE focused question to fill the most critical gap.
- When the candidate provides new professional information, extract it and include a "profile_updates" JSON block at the END of your reply in this exact format:
  <<<PROFILE_UPDATES>>>
  {{"profile_updates": {{"field": value}}}}
  <<<END_UPDATES>>>
- Only include profile_updates when the candidate actually provides new information.
- Do NOT change open_to_opportunities unless the candidate explicitly asks.
- Do NOT overwrite fields that already have good data unless the candidate is correcting them.

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

    def add(label, val):
        if val and (not isinstance(val, list) or len(val) > 0):
            lines.append(f"- {label}: {val}")
        else:
            missing.append(label)

    add("Name", profile.get("name"))
    add("Email", profile.get("email"))
    add("Phone", profile.get("phone"))
    add("Headline", profile.get("headline") or profile.get("current_role"))
    add("Location", profile.get("location"))
    add("Bio/Summary", profile.get("bio") or profile.get("summary"))
    add("Skills", profile.get("keySkills") or profile.get("skills"))
    add("Work Experience", profile.get("experience") or profile.get("work_experience"))
    add("Education", profile.get("education"))
    add("Experience Years", profile.get("experience_years"))

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


@api_router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages is empty")

    last_user = next((m for m in reversed(request.messages) if m.role == "user"), None)
    if not last_user:
        raise HTTPException(status_code=400, detail="No user message provided")

    # Load candidate profile for context
    profile_context = "No profile loaded yet."
    missing_fields: list = []
    if request.candidate_id:
        try:
            row = await _get_candidate_row(request.candidate_id)
            frontend_profile = _normalize_for_frontend(row)
            profile_context, missing_fields = _build_profile_context(frontend_profile)
        except HTTPException:
            pass

    system_prompt = EVE_SYSTEM_TEMPLATE.format(
        profile_context=profile_context,
        missing_fields=", ".join(missing_fields) if missing_fields else "None",
    )

    messages = [{"role": "system", "content": system_prompt}]
    for m in request.messages[-10:]:
        messages.append({"role": m.role, "content": m.content})

    try:
        resp = await openai_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.7,
        )
        raw_reply = resp.choices[0].message.content or ""
    except Exception as e:
        logger.exception("LLM chat failure")
        raise HTTPException(status_code=502, detail=f"LLM error: {str(e)}")

    clean_reply, profile_updates = _extract_profile_updates(raw_reply)

    # Apply updates to PostgreSQL
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


class VoiceCandidateIntakeRequest(BaseModel):
    transcript: str
    voice_notes: Optional[List[VoiceNote]] = None
    candidate_id: str  # validated server-side against DB


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
    """Only manages tables not covered by Alembic (voice intake).
    Production schema (adam_event_id, eve_outbound_events) is managed by Alembic.
    """
    async with SessionLocal() as db:
        await db.execute(text(CREATE_VOICE_INTAKES_TABLE))
        await db.execute(text(CREATE_VOICE_INTAKES_INDEX))
        await db.execute(text("""
            ALTER TABLE candidate_job_recommendations
            ADD COLUMN IF NOT EXISTS tracked_at TIMESTAMPTZ NULL
        """))
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
Return only the JSON object."""


async def _extract_voice_info(transcript: str) -> dict:
    """Use LLM to extract structured candidate info from voice transcript."""
    try:
        resp = await openai_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
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
    for key in ("summary", "current_company", "location"):
        if voice.get(key) and not merged.get(key):
            merged[key] = voice[key]

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
    voice_notes_json = json.dumps(
        [n.model_dump() for n in (request.voice_notes or [])]
    )
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
                "notes": voice_notes_json,
            },
        )
        await db.commit()

    # 4. Extract structured info via LLM
    voice_data = await _extract_voice_info(transcript)

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

    # 7. Mark intake as completed
    async with SessionLocal() as db:
        await db.execute(
            text("""
                UPDATE candidate_voice_intakes
                SET status = 'completed', completed_at = now()
                WHERE id = :id
            """),
            {"id": intake_id},
        )
        await db.commit()

    logger.info("Voice intake completed for candidate %s (intake %s)", request.candidate_id, intake_id)

    asyncio.ensure_future(_trigger_matching(request.candidate_id))

    # Return updated profile so dashboard can refresh immediately
    updated_candidate = await _get_candidate_row(request.candidate_id)
    updated_profile = _normalize_for_frontend(updated_candidate)

    return {
        "status": "completed",
        "intake_id": intake_id,
        "candidate_id": request.candidate_id,
        "fields_updated": [c.split(" ")[0].strip('"') for c in set_clauses
                           if not c.startswith("updated") and not c.startswith("raw_data")],
        "profile": updated_profile,
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
    """Record application initiation: sets applied_at and tracked_at."""
    await _get_candidate_row(candidate_id)
    async with SessionLocal() as db:
        row = await db.execute(
            text("SELECT id, job_id FROM candidate_job_recommendations WHERE id = :rid AND candidate_id = :cid LIMIT 1"),
            {"rid": rec_id, "cid": candidate_id},
        )
        rec = row.mappings().fetchone()
        if not rec:
            raise HTTPException(status_code=404, detail="Recommendation not found.")
        await db.execute(
            text("""
                UPDATE candidate_job_recommendations
                SET applied_at = now(), tracked_at = COALESCE(tracked_at, now())
                WHERE id = :rid AND candidate_id = :cid
            """),
            {"rid": rec_id, "cid": candidate_id},
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
import httpx

LINKEDIN_CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "")
LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
LINKEDIN_REDIRECT_URI = os.environ.get("LINKEDIN_REDIRECT_URI", "http://localhost:3000")


@api_router.get("/auth/linkedin/init")
async def linkedin_init():
    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(status_code=503, detail="LinkedIn OAuth is not configured.")
    state = secrets.token_urlsafe(16)
    auth_url = (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code"
        f"&client_id={LINKEDIN_CLIENT_ID}"
        f"&redirect_uri={LINKEDIN_REDIRECT_URI}"
        f"&state={state}"
        f"&scope=openid%20profile%20email"
    )
    return {"auth_url": auth_url, "state": state}


@api_router.get("/auth/linkedin/callback")
async def linkedin_callback(code: str, state: str):
    if not LINKEDIN_CLIENT_ID:
        raise HTTPException(status_code=503, detail="LinkedIn OAuth is not configured.")

    async with httpx.AsyncClient() as client:
        # Exchange code for access token
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
            raise HTTPException(status_code=400, detail="LinkedIn token exchange failed.")
        token_data = token_resp.json()
        access_token = token_data.get("access_token")

        # Fetch profile via OpenID userinfo
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

    # Check if candidate already exists by email
    is_returning = False
    candidate_id = None
    async with SessionLocal() as db:
        if email:
            row = await db.execute(
                text("SELECT id FROM candidates WHERE email = :email LIMIT 1"),
                {"email": email},
            )
            existing = row.fetchone()
            if existing:
                candidate_id = str(existing[0])
                is_returning = True

    return {
        "profile": {"name": name, "email": email, "picture": picture, "linkedin_id": linkedin_id},
        "candidate_id": candidate_id,
        "is_returning": is_returning,
    }


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


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
