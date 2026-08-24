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


def test_candidate_profile_download_orders_experience_newest_first(monkeypatch):
    async def fake_get_candidate_profile_payload(candidate_id: str):
        return {
            "candidate_id": candidate_id,
            "name": "Jordan Lee",
            "bio": "Seasoned operator and engineer.",
            "keySkills": ["Strategy", "Operations"],
            "experience": [
                {
                    "title": "Analyst",
                    "company": "Older Co",
                    "start_date": "2019-01-01",
                    "end_date": "2021-12-31",
                    "dates": "2019 — 2021",
                    "description": "Built reporting workflows.",
                },
                {
                    "title": "Senior Engineer",
                    "company": "Deepija Telecom Private Limited",
                    "start_date": "2023-11-01",
                    "end_date": "2024-10-09",
                    "dates": "01-11-2023 — 09-10-2024",
                    "description": "Scaled internal systems.",
                },
                {
                    "title": "Co-Founder",
                    "company": "Viral Bug",
                    "start_date": "2024-10-10",
                    "end_date": "",
                    "dates": "2024 — Present",
                    "description": "Leading product and growth.",
                },
            ],
            "education": [],
        }

    async def fake_get_candidate_row(candidate_id: str):
        return {"id": candidate_id, "raw_data": {}}

    monkeypatch.setattr(server, "_get_candidate_profile_payload", fake_get_candidate_profile_payload)
    monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)

    response = asyncio.run(server.download_candidate_profile("cand-999"))
    text = _extract_pdf_text(response.body)

    viral_bug_index = text.index("Viral Bug")
    deepija_index = text.index("Deepija Telecom Private Limited")
    older_index = text.index("Older Co")

    assert viral_bug_index < deepija_index < older_index
