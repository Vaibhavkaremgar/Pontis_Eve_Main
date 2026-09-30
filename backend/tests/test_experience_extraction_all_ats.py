import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost/unused")

import pytest

from app.job_ingestion.normalize import (
    _experience_from_text,
    normalize_ashby,
    normalize_fantastic,
    normalize_greenhouse,
    normalize_lever,
)
from app.job_ingestion.job_ingestion_service import _metadata_params


FORMATS = [
    ("6+ years", "6+ years"),
    ("6 years", "6 years"),
    ("6-8 years", "6-8 years"),
    ("6 to 8 years", "6 to 8 years"),
    ("minimum 6 years", "minimum 6 years"),
    ("at least 6 years", "at least 6 years"),
    ("5+ years of experience", "5+ years of experience"),
    ("2 years of relevant experience", "2 years of relevant experience"),
]


@pytest.mark.parametrize("source,field", [("greenhouse", "content"), ("lever", "descriptionPlain"), ("ashby", "descriptionHtml"), ("fantastic", "description_text")])
@pytest.mark.parametrize("text,expected", FORMATS)
def test_all_supported_ats_preserve_explicit_experience(source, field, text, expected):
    payload = {"id": f"{source}-{expected}", "title": "Engineer", field: text}
    if source == "lever":
        payload["text"] = payload.pop("title")
        job = normalize_lever(payload, "Acme")
    elif source == "greenhouse":
        job = normalize_greenhouse(payload, "Acme")
    elif source == "ashby":
        job = normalize_ashby(payload, "Acme")
    else:
        payload["organization"] = "Acme"
        job = normalize_fantastic(payload)
    assert job["experience_required"] == expected
    assert _metadata_params(job)["experience_required"] == expected


def test_experience_extractor_returns_null_without_numeric_requirement():
    assert _experience_from_text("Experience with Java backend development") is None


@pytest.mark.parametrize("text", [
    "Our company has been in business for over 30 years.",
    "We have been serving clients for over 20 years.",
])
def test_experience_extractor_ignores_company_history(text):
    assert _experience_from_text(text) is None


def test_experience_extractor_prefers_candidate_requirement_over_company_history():
    text = ("CCS has been providing solutions to our clients for over 45 years. "
            "The ideal candidate has hands-on experience. "
            "1\N{EN DASH}2 years of experience in hardware and software installation or integration.")
    assert _experience_from_text(text) == "1\N{EN DASH}2 years"


@pytest.mark.parametrize("text,expected", [
    ("Minimum 5 years of experience in Java development.", "Minimum 5 years"),
    ("At least 8 years of relevant experience.", "At least 8 years"),
    ("5+ years of relevant experience.", "5+ years of relevant experience"),
    ("2-5 years of experience.", "2-5 years"),
    ("5 to 8 years of experience.", "5 to 8 years"),
])
def test_experience_extractor_handles_candidate_requirement_forms(text, expected):
    assert _experience_from_text(text) == expected


def test_experience_extractor_prefers_general_requirement():
    assert _experience_from_text(
        "3+ years of professional software development experience, including 1+ year working with Kubernetes."
    ) == "3+ years"


def test_lever_combines_supported_description_fields_without_duplicates():
    job = normalize_lever({
        "id": "lever-combined", "text": "Engineer",
        "descriptionPlain": "Build services.",
        "description": "Build services.",
        "descriptionHtml": "<p>6+ years of relevant experience</p>",
    }, "Acme")
    assert job["description"].count("Build services.") == 1
    assert job["experience_required"] == "6+ years of relevant experience"


def test_fantastic_uses_requirements_summary_for_experience_extraction():
    job = normalize_fantastic({
        "id": "fantastic-summary", "title": "Engineer", "organization": "Acme",
        "description_text": "Build services.",
        "ai_requirements_summary": "At least 6 years of experience",
    })
    assert job["experience_required"] == "At least 6 years"


def test_persistence_parameters_carry_extracted_experience_value():
    job = normalize_lever({
        "id": "lever-upsert", "text": "Engineer",
        "descriptionPlain": "6+ years of relevant experience",
    }, "Acme")
    assert _metadata_params(job)["experience_required"] == "6+ years of relevant experience"
