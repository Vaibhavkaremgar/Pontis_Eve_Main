"""
ATS-friendly resume PDF tests.

Covers:
- PDF generation succeeds with a normal candidate profile
- Candidate name / contact information appears correctly
- Summary appears correctly
- Skills are deduplicated
- Conversational/noisy skill entries are removed
- Duplicate experience descriptions are removed
- Experience dates are preserved
- Education dates are preserved
- Certifications are included correctly
- Missing sections are omitted
- No candidate information is invented
- PDF remains text-based/selectable
- Existing Download PDF functionality continues to work
- Section headings use ATS-standard labels
- Experience uses job-title-first format (not "Title at Company")
- Skills rendered as comma-separated list (not individual bullets)
"""
import asyncio
import io
import os
import re
import sys

from pypdf import PdfReader

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return re.sub(r"\s+", " ", text).strip()


def _make_profile(overrides: dict | None = None) -> dict:
    base = {
        "candidate_id": "cand-ats-001",
        "name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "+1 555 010 2000",
        "location": "New York, NY",
        "bio": "Experienced product designer focused on accessibility and design systems.",
        "keySkills": ["Figma", "Design Systems", "Accessibility", "Figma"],  # intentional dup
        "experience": [
            {
                "title": "Staff Product Designer",
                "company": "Acme Corp",
                "dates": "2022 - Present",
                "description": (
                    "Led the redesign of the candidate dashboard. "
                    "Led the redesign of the candidate dashboard. "  # duplicate sentence
                    "Partnered with engineering to ship accessible flows."
                ),
            }
        ],
        "education": [
            {
                "degree": "B.Des. Interaction Design",
                "institution": "RISD",
                "dates": "2012 - 2016",
            }
        ],
        "certifications": ["NN/g UX Certification"],
    }
    if overrides:
        base.update(overrides)
    return base


async def _build_pdf(profile: dict) -> bytes:
    return server._build_candidate_profile_pdf(profile)


# ---------------------------------------------------------------------------
# 1. PDF generation succeeds with a normal candidate profile
# ---------------------------------------------------------------------------
def test_ats_pdf_generation_succeeds():
    pdf = asyncio.run(_build_pdf(_make_profile()))
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 500


# ---------------------------------------------------------------------------
# 2. PDF is text-based / selectable
# ---------------------------------------------------------------------------
def test_ats_pdf_is_text_selectable():
    pdf = asyncio.run(_build_pdf(_make_profile()))
    reader = PdfReader(io.BytesIO(pdf))
    text = "".join(page.extract_text() or "" for page in reader.pages)
    assert len(text.strip()) > 50, "PDF must contain selectable text"


# ---------------------------------------------------------------------------
# 3. Candidate name and contact information appear correctly
# ---------------------------------------------------------------------------
def test_ats_pdf_name_and_contact():
    pdf = asyncio.run(_build_pdf(_make_profile()))
    text = _extract_pdf_text(pdf)
    assert "Jane Doe" in text
    assert "jane@example.com" in text
    assert "+1 555 010 2000" in text
    assert "New York, NY" in text


# ---------------------------------------------------------------------------
# 4. Summary appears correctly
# ---------------------------------------------------------------------------
def test_ats_pdf_summary():
    pdf = asyncio.run(_build_pdf(_make_profile()))
    text = _extract_pdf_text(pdf)
    assert "Experienced product designer" in text
    # ATS heading
    assert "SUMMARY" in text.upper()


# ---------------------------------------------------------------------------
# 5. Skills are deduplicated
# ---------------------------------------------------------------------------
def test_ats_pdf_skills_deduplicated():
    profile = _make_profile({"keySkills": ["Python", "python", "PYTHON", "FastAPI", "FastAPI"]})
    pdf = asyncio.run(_build_pdf(profile))
    text = _extract_pdf_text(pdf)
    # Count occurrences of "python" (case-insensitive) — should appear exactly once
    assert text.lower().count("python") == 1
    assert text.lower().count("fastapi") == 1


# ---------------------------------------------------------------------------
# 6. Conversational/noisy skill entries are removed
# ---------------------------------------------------------------------------
def test_ats_pdf_noisy_skills_removed():
    profile = _make_profile({
        "keySkills": [
            "Python",
            "some more skills python",
            "voice intake",
            "resume processing",
            "More skills",
        ]
    })
    pdf = asyncio.run(_build_pdf(profile))
    text = _extract_pdf_text(pdf).lower()
    assert "some more skills" not in text
    assert "voice intake" not in text
    assert "resume processing" not in text
    assert "more skills" not in text
    assert "python" in text


# ---------------------------------------------------------------------------
# 7. Duplicate experience descriptions are removed
# ---------------------------------------------------------------------------
def test_ats_pdf_duplicate_experience_sentences_removed():
    dup_desc = "Led the redesign of the candidate dashboard. Led the redesign of the candidate dashboard."
    profile = _make_profile({
        "experience": [
            {
                "title": "Designer",
                "company": "Acme",
                "dates": "2022 - Present",
                "description": dup_desc,
            }
        ]
    })
    pdf = asyncio.run(_build_pdf(profile))
    text = _extract_pdf_text(pdf)
    # The sentence should appear only once
    count = text.lower().count("led the redesign of the candidate dashboard")
    assert count == 1, f"Expected 1 occurrence, got {count}"


# ---------------------------------------------------------------------------
# 8. Experience dates are preserved
# ---------------------------------------------------------------------------
def test_ats_pdf_experience_dates_preserved():
    pdf = asyncio.run(_build_pdf(_make_profile()))
    text = _extract_pdf_text(pdf)
    assert "2022" in text
    assert "Present" in text or "present" in text.lower()


# ---------------------------------------------------------------------------
# 9. Education dates are preserved
# ---------------------------------------------------------------------------
def test_ats_pdf_education_dates_preserved():
    pdf = asyncio.run(_build_pdf(_make_profile()))
    text = _extract_pdf_text(pdf)
    assert "2012" in text
    assert "2016" in text


# ---------------------------------------------------------------------------
# 10. Certifications are included correctly
# ---------------------------------------------------------------------------
def test_ats_pdf_certifications_included():
    pdf = asyncio.run(_build_pdf(_make_profile()))
    text = _extract_pdf_text(pdf)
    assert "NN/g UX Certification" in text
    assert "CERTIFICATION" in text.upper()


# ---------------------------------------------------------------------------
# 11. Missing sections are omitted
# ---------------------------------------------------------------------------
def test_ats_pdf_missing_sections_omitted():
    profile = _make_profile({
        "bio": "",
        "certifications": [],
        "education": [],
    })
    pdf = asyncio.run(_build_pdf(profile))
    text = _extract_pdf_text(pdf).upper()
    assert "SUMMARY" not in text
    assert "CERTIFICATION" not in text
    assert "EDUCATION" not in text
    # Experience and skills should still be present
    assert "EXPERIENCE" in text
    assert "SKILL" in text


# ---------------------------------------------------------------------------
# 12. No candidate information is invented
# ---------------------------------------------------------------------------
def test_ats_pdf_no_invented_information():
    profile = _make_profile({
        "name": "Alex Rivera",
        "email": "alex@example.com",
        "bio": "Operations leader.",
        "keySkills": ["Python"],
        "experience": [
            {
                "title": "Operations Lead",
                "company": "Northwind",
                "dates": "2023 - Present",
                "description": "Owned operations workflows.",
            }
        ],
        "education": [],
        "certifications": [],
    })
    pdf = asyncio.run(_build_pdf(profile))
    text = _extract_pdf_text(pdf)
    # Only real data should appear
    assert "Alex Rivera" in text
    assert "Northwind" in text
    assert "Operations Lead" in text
    # No invented companies or roles
    assert "Acme" not in text
    assert "Jane Doe" not in text
    assert "RISD" not in text


# ---------------------------------------------------------------------------
# 13. ATS section headings use standard labels
# ---------------------------------------------------------------------------
def test_ats_pdf_standard_section_headings():
    pdf = asyncio.run(_build_pdf(_make_profile()))
    text = _extract_pdf_text(pdf).upper()
    assert "PROFESSIONAL SUMMARY" in text or "SUMMARY" in text
    assert "TECHNICAL SKILLS" in text or "SKILL" in text
    assert "PROFESSIONAL EXPERIENCE" in text or "EXPERIENCE" in text
    assert "EDUCATION" in text
    assert "CERTIFICATION" in text


# ---------------------------------------------------------------------------
# 14. Experience uses job-title-first format (not "Title at Company")
# ---------------------------------------------------------------------------
def test_ats_pdf_experience_not_title_at_company():
    pdf = asyncio.run(_build_pdf(_make_profile()))
    text = _extract_pdf_text(pdf)
    # Old format was "Staff Product Designer at Acme Corp"
    assert "Staff Product Designer at Acme Corp" not in text
    # New format: title on its own line, company in meta line
    assert "Staff Product Designer" in text
    assert "Acme Corp" in text


# ---------------------------------------------------------------------------
# 15. Skills rendered as comma-separated list (not one-per-line bullets)
# ---------------------------------------------------------------------------
def test_ats_pdf_skills_comma_separated():
    profile = _make_profile({"keySkills": ["Figma", "Design Systems", "Accessibility"]})
    pdf = asyncio.run(_build_pdf(profile))
    text = _extract_pdf_text(pdf)
    # All three skills should appear and be close together (comma-separated)
    assert "Figma" in text
    assert "Design Systems" in text
    assert "Accessibility" in text
    # They should appear on the same logical line (within 60 chars of each other)
    idx_figma = text.find("Figma")
    idx_ds = text.find("Design Systems")
    assert abs(idx_figma - idx_ds) < 80, "Skills should be close together (comma-separated)"


# ---------------------------------------------------------------------------
# 16. Existing Download PDF endpoint continues to work (monkeypatched)
# ---------------------------------------------------------------------------
def test_ats_download_endpoint_continues_to_work(monkeypatch):
    async def fake_get_candidate_profile_payload(candidate_id: str):
        return _make_profile({"candidate_id": candidate_id})

    async def fake_get_candidate_row(candidate_id: str):
        return {"id": candidate_id, "raw_data": {}}

    monkeypatch.setattr(server, "_get_candidate_profile_payload", fake_get_candidate_profile_payload)
    monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)

    response = asyncio.run(server.download_candidate_profile("cand-ats-001"))
    assert response.headers["content-type"] == "application/pdf"
    assert "filename=" in response.headers["content-disposition"]
    assert response.body.startswith(b"%PDF")
    text = _extract_pdf_text(response.body)
    assert "Jane Doe" in text


# ---------------------------------------------------------------------------
# 17. _ats_clean_description deduplication unit test
# ---------------------------------------------------------------------------
def test_ats_clean_description_deduplicates():
    desc = "Built REST APIs. Built REST APIs. Deployed to AWS."
    bullets = server._ats_clean_description(desc)
    assert len(bullets) == 2
    assert bullets[0] == "Built REST APIs"
    assert bullets[1] == "Deployed to AWS"


def test_ats_clean_description_empty():
    assert server._ats_clean_description("") == []
    assert server._ats_clean_description(None) == []


# ---------------------------------------------------------------------------
# 18. _candidate_profile_pdf_skills deduplication unit test
# ---------------------------------------------------------------------------
def test_candidate_profile_pdf_skills_deduplicates():
    profile = {"keySkills": ["Python", "python", "PYTHON", "FastAPI", "fastapi"]}
    skills = server._candidate_profile_pdf_skills(profile)
    lower = [s.lower() for s in skills]
    assert lower.count("python") == 1
    assert lower.count("fastapi") == 1


def test_candidate_profile_pdf_skills_removes_noisy():
    profile = {"keySkills": ["Python", "some more skills python", "voice intake", "resume processing"]}
    skills = server._candidate_profile_pdf_skills(profile)
    lower = [s.lower() for s in skills]
    assert "python" in lower
    assert "some more skills python" not in lower
    assert "voice intake" not in lower
    assert "resume processing" not in lower
