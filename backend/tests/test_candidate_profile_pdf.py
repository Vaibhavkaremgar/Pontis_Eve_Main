import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


def test_candidate_profile_download_returns_pdf_headers_and_filename(monkeypatch):
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
        }

    monkeypatch.setattr(server, "_get_candidate_profile_payload", fake_get_candidate_profile_payload)

    response = asyncio.run(server.download_candidate_profile("cand-123"))

    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].endswith('filename="Jane_Doe_profile.pdf"')
    assert response.body.startswith(b"%PDF")
