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


def test_candidate_profile_download_renders_clean_pdf_without_candidate_id_or_extra_fields(monkeypatch):
    async def fake_get_candidate_profile_payload(candidate_id: str):
        return {
            "candidate_id": candidate_id,
            "name": "Jane Doe",
            "headline": "Senior Product Designer",
            "current_company": "Acme Corp",
            "location": "New York, NY",
            "email": "jane@example.com",
            "phone": "+1 555 010 2000",
            "experience_years": 8,
            "availability": "2 weeks",
            "preferred_roles": ["Lead Designer"],
            "bio": "Design systems, accessibility, and product strategy.",
            "keySkills": ["Figma", "Design Systems", "Accessibility"],
            "experience": [
                {
                    "title": "Staff Product Designer",
                    "company": "Acme Corp",
                    "dates": "2022 - Present",
                    "description": "Led the redesign of the candidate dashboard.",
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
            "additional_information": "Should not appear in the PDF.",
        }

    async def fake_get_candidate_row(candidate_id: str):
        return {
            "id": candidate_id,
            "raw_data": {
                "linkedin_url": "https://www.linkedin.com/in/janedoe",
                "social_links": [
                    {"label": "Portfolio", "url": "https://janedoe.design"},
                    {"name": "GitHub", "href": "https://github.com/janedoe"},
                ],
            },
        }

    monkeypatch.setattr(server, "_get_candidate_profile_payload", fake_get_candidate_profile_payload)
    monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)

    response = asyncio.run(server.download_candidate_profile("cand-123"))
    text = _extract_pdf_text(response.body)

    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].endswith('filename="Jane_Doe_profile.pdf"')
    assert response.body.startswith(b"%PDF")

    assert "Jane Doe" in text
    assert "New York, NY" in text
    assert "jane@example.com" in text
    assert "+1 555 010 2000" in text
    assert "https://www.linkedin.com/in/janedoe" in text
    assert "https://janedoe.design" in text
    assert "https://github.com/janedoe" in text

    assert "targeting" not in text.lower()
    assert "candidate id" not in text.lower()
    assert "cand-123" not in text
    assert "Availability" not in text
    assert "Preferred roles" not in text
    assert "Additional Information" not in text
    assert "Should not appear in the PDF." not in text

    assert "Summary" in text
    assert "Skills" in text
    assert "Certifications" in text
    assert "Experience" in text
    assert "Education" in text


def test_candidate_profile_download_paginates_large_profiles(monkeypatch):
    long_description = " ".join(
        [
            "Led large scale product design work across onboarding, search, and collaboration.",
            "Partnered closely with engineering, research, and product teams to ship accessible flows.",
        ]
        * 8
    )

    async def fake_get_candidate_profile_payload(candidate_id: str):
        return {
            "candidate_id": candidate_id,
            "name": "Jordan Smith",
            "location": "Remote",
            "email": "jordan@example.com",
            "phone": "+1 555 010 3000",
            "bio": "Product leader with a strong execution track record.",
            "keySkills": ["Strategy", "Research", "Execution", "Communication"],
            "certifications": ["Certified Product Leader"],
            "experience": [
                {
                    "title": f"Principal Product Designer {index + 1}",
                    "company": "Northstar Labs",
                    "dates": f"201{index} - 202{index}",
                    "description": long_description,
                }
                for index in range(8)
            ],
            "education": [
                {
                    "degree": "M.Des. Human Computer Interaction",
                    "institution": "Carnegie Mellon University",
                    "dates": "2010 - 2012",
                }
            ],
        }

    async def fake_get_candidate_row(candidate_id: str):
        return {"id": candidate_id, "raw_data": {}}

    monkeypatch.setattr(server, "_get_candidate_profile_payload", fake_get_candidate_profile_payload)
    monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)

    response = asyncio.run(server.download_candidate_profile("cand-456"))
    reader = PdfReader(io.BytesIO(response.body))
    text = _extract_pdf_text(response.body)

    assert len(reader.pages) >= 2
    assert "cand-456" not in text
    assert "Candidate ID" not in text
    assert "Additional Information" not in text
    assert "Availability" not in text
    assert "Preferred roles" not in text
    assert "Jordan Smith" in text
    assert "Principal Product Designer 1" in text
    assert "Education" in text


def test_candidate_profile_download_excludes_project_features_from_skills(monkeypatch):
    async def fake_get_candidate_profile_payload(candidate_id: str):
        return {
            "candidate_id": candidate_id,
            "name": "Alex Rivera",
            "bio": "Operations leader who improved intake and resume workflows.",
            "keySkills": ["Voice intake", "Resume processing", "Python"],
            "experience": [
                {
                    "title": "Operations Lead",
                    "company": "Northwind",
                    "dates": "2023 - Present",
                    "description": (
                        "Owned voice intake and resume processing workflows across candidate operations."
                    ),
                }
            ],
            "certifications": ["Operations Excellence Certificate"],
            "education": [
                {
                    "degree": "B.S. Business Administration",
                    "institution": "State University",
                    "dates": "2014 - 2018",
                }
            ],
        }

    async def fake_get_candidate_row(candidate_id: str):
        return {"id": candidate_id, "raw_data": {}}

    monkeypatch.setattr(server, "_get_candidate_profile_payload", fake_get_candidate_profile_payload)
    monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)

    response = asyncio.run(server.download_candidate_profile("cand-789"))
    text = _extract_pdf_text(response.body)
    text_lower = text.lower()

    assert "voice intake" in text_lower
    assert "resume processing" in text_lower
    assert "python" in text_lower

    skills_section = text_lower.split("skills", 1)[1].split("certifications", 1)[0]
    assert "voice intake" not in skills_section
    assert "resume processing" not in skills_section
    assert "python" in skills_section


def test_candidate_profile_download_normalizes_unicode_dashes(monkeypatch):
    async def fake_get_candidate_profile_payload(candidate_id: str):
        return {
            "candidate_id": candidate_id,
            "name": "Maya Chen",
            "bio": "Built third–party and AI—powered automation for end‑to‑end operations.",
            "keySkills": ["Product Strategy"],
            "experience": [
                {
                    "title": "Platform Lead",
                    "company": "Bright Labs",
                    "dates": "2021 – Present",
                    "description": (
                        "Led third–party integrations, AI—powered workflows, and end‑to‑end delivery."
                    ),
                }
            ],
            "education": [
                {
                    "degree": "B.S. Computer Science",
                    "institution": "City College",
                    "dates": "2011 – 2015",
                }
            ],
        }

    async def fake_get_candidate_row(candidate_id: str):
        return {"id": candidate_id, "raw_data": {}}

    monkeypatch.setattr(server, "_get_candidate_profile_payload", fake_get_candidate_profile_payload)
    monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)

    response = asyncio.run(server.download_candidate_profile("cand-321"))
    text = _extract_pdf_text(response.body).lower()

    assert "third-party" in text
    assert "ai-powered" in text
    assert "end-to-end" in text
    assert "bright labs" in text
    assert "■" not in text
