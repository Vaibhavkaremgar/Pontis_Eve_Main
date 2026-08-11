"""
Dev test: voice intake WITHOUT VAPI.
Candidate ID: f164e669-1971-4a68-a20b-e3d4302e0324

Steps:
  1. Create a candidate via parse-resume (minimal PDF)
  2. POST /api/voice/candidate-intake with the test transcript
  3. Verify updated_by_source = 'eve_voice' in PostgreSQL
"""
import io
import sys
import requests
from reportlab.pdfgen import canvas as rl_canvas
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"
DATABASE_URL = os.environ["DATABASE_URL"]

TRANSCRIPT = (
    "I have three years of experience as a Python Backend Developer. "
    "I have worked with Python, FastAPI, PostgreSQL, AWS and Docker. "
    "I am available to join immediately and interested in Backend Software Engineer roles."
)


def _make_minimal_pdf() -> bytes:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.drawString(50, 800, "Dev Test Candidate")
    c.drawString(50, 780, "Email: devtest-voice@example.com")
    c.drawString(50, 760, "Python Backend Developer, 3 years experience.")
    c.showPage()
    c.save()
    return buf.getvalue()


def create_candidate() -> str:
    print("[1] Creating candidate via parse-resume...")
    r = requests.post(
        f"{API}/onboarding/parse-resume",
        files={"file": ("resume.pdf", _make_minimal_pdf(), "application/pdf")},
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    cid = data.get("candidate_id") or data.get("candidateId")
    assert cid, f"No candidate_id in response: {data}"
    print(f"    candidate_id = {cid}")
    return cid


def run_voice_intake(cid: str) -> dict:
    print("[2] Calling /api/voice/candidate-intake...")
    r = requests.post(
        f"{API}/voice/candidate-intake",
        json={"transcript": TRANSCRIPT, "candidate_id": cid},
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    print(f"  status       = {data['status']}")
    print(f"  intake_id    = {data.get('intake_id')}")
    print(f"  fields_updated = {data.get('fields_updated')}")
    return data


async def verify_db(cid: str):
    print("[3] Verifying PostgreSQL...")
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    async with async_sessionmaker(bind=engine, expire_on_commit=False)() as db:
        row = await db.execute(
            text("SELECT updated_by_source, skills, experience_years, raw_data FROM candidates WHERE id = :cid"),
            {"cid": cid},
        )
        result = row.fetchone()
    await engine.dispose()

    assert result, "Candidate not found in DB!"
    updated_by_source, skills, experience_years, raw_data = result

    print(f"  updated_by_source = {updated_by_source}")
    print(f"  experience_years  = {experience_years}")
    print(f"  skills            = {skills}")
    print(f"  raw_data          = {raw_data}")

    assert updated_by_source == "eve_voice", (
        f"FAIL: expected 'eve_voice', got '{updated_by_source}'"
    )
    print("\nPASS: updated_by_source = 'eve_voice'")


if __name__ == "__main__":
    cid = create_candidate()
    result = run_voice_intake(cid)
    assert result["status"] == "completed", f"Voice intake did not complete: {result}"
    asyncio.run(verify_db(cid))
    print(f"\nAll checks passed for candidate {cid}")
