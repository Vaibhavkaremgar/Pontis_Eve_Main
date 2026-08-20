"""
Unit tests for profile strength scoring.
"""
import os
import sys

# Allow importing server module directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import _calculate_profile_strength


def _base_profile():
    return {
        "name": "",
        "email": "",
        "phone": "",
        "location": "",
        "current_role": "",
        "headline": "",
        "current_company": "",
        "summary": "",
        "experience_years": None,
        "skills": [],
        "work_experience": [],
        "education": [],
        "certifications": [],
        "candidate_certificates": [],
        "raw_data": {},
    }


def _certificate_rows(*file_names):
    return [
        {
            "id": f"cert-{index}",
            "file_name": file_name,
            "file_path": f"/tmp/cert-{index}.pdf",
        }
        for index, file_name in enumerate(file_names, start=1)
    ]


def test_empty_profile_scores_zero():
    percent, label = _calculate_profile_strength(_base_profile())
    assert percent == 0
    assert label == "Building"


def test_resume_only_profile_scores_baseline_completeness():
    profile = _base_profile()
    profile.update(
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "photo_url": "/api/candidate/test/photo/view",
            "current_role": "Backend Engineer",
            "skills": ["Python", "FastAPI"],
            "work_experience": [
                {"title": "Engineer", "company": "Acme", "description": "Built APIs"}
            ],
        }
    )

    percent, label = _calculate_profile_strength(profile, profile["raw_data"])
    assert percent == 60
    assert label == "Developing"


def test_experienced_candidate_scores_higher_with_resume_depth():
    profile = _base_profile()
    profile.update(
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "photo_url": "/api/candidate/test/photo/view",
            "location": "Remote",
            "current_role": "Senior Backend Engineer",
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "work_experience": [
                {"title": "Lead Engineer", "company": "Acme", "description": "Led platform work"}
            ],
            "education": [{"degree": "B.Tech", "institution": "Example University"}],
        }
    )

    percent, label = _calculate_profile_strength(profile, profile["raw_data"])
    assert percent == 80
    assert label == "Strong"


def test_fresher_profile_redistributes_work_experience_weight():
    profile = _base_profile()
    profile.update(
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "photo_url": "/api/candidate/test/photo/view",
            "experience_years": 0,
            "current_role": "Fresher Backend Developer",
            "skills": ["Python", "FastAPI", "Git"],
            "education": [{"degree": "B.Tech", "institution": "Example University"}],
            "candidate_certificates": _certificate_rows("AWS Cloud Practitioner"),
            "raw_data": {
                "preferred_roles": ["Backend Developer"],
                "availability": "Immediate",
                "location_preferences": ["Remote"],
                "projects": ["Campus project for a REST API platform"],
            },
        }
    )

    percent, label = _calculate_profile_strength(profile, profile["raw_data"])
    assert percent == 100
    assert label == "Strong"


def test_voice_intake_additions_increase_score_without_double_counting():
    profile = _base_profile()
    profile.update(
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "photo_url": "/api/candidate/test/photo/view",
            "current_role": "Backend Engineer",
            "skills": ["Python", "FastAPI"],
            "work_experience": [
                {"title": "Engineer", "company": "Acme", "description": "Built APIs"}
            ],
            "raw_data": {
                "voice_intake": {
                    "status": "in_progress",
                    "completed_turns": [
                        {
                            "question": "What kind of projects have you worked on recently?",
                            "answer": "I built an AI automation project for onboarding.",
                        }
                    ],
                },
                "availability": "Immediate",
                "location_preferences": ["Remote"],
            },
        }
    )

    percent, label = _calculate_profile_strength(profile, profile["raw_data"])
    assert percent == 85
    assert label == "Strong"


def test_saving_voice_intake_cannot_reduce_score_when_canonical_profile_is_unchanged():
    profile = _base_profile()
    profile.update(
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "photo_url": "/api/candidate/test/photo/view",
            "location": "Remote",
            "current_role": "Senior Backend Engineer",
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "work_experience": [
                {"title": "Lead Engineer", "company": "Acme", "description": "Led platform work"}
            ],
            "education": [{"degree": "B.Tech", "institution": "Example University"}],
            "candidate_certificates": _certificate_rows("AWS Certified Solutions Architect"),
            "raw_data": {
                "availability": "Immediate",
                "location_preferences": ["Remote", "Hybrid"],
                "preferred_roles": ["Senior Backend Engineer"],
            },
            # Stale resume JSON should never override stronger canonical DB fields.
            "parsed_resume_json": {
                "name": "Jane Doe",
                "skills": ["Python"],
                "work_experience": [],
                "education": [],
                "certifications": [],
            },
        }
    )

    before_percent, before_label = _calculate_profile_strength(profile, profile["raw_data"])
    assert before_percent == 85
    assert before_label == "Strong"

    saved_voice_profile = dict(profile)
    saved_voice_profile["raw_data"] = {
        **profile["raw_data"],
        "voice_intake": {
            "status": "in_progress",
            "completed_turns": [
                {
                    "question": "What kinds of projects have you worked on recently?",
                    "answer": "I built a candidate dashboard and reporting workflow.",
                }
            ],
        },
    }

    after_percent, after_label = _calculate_profile_strength(
        saved_voice_profile,
        saved_voice_profile["raw_data"],
    )

    assert after_percent >= before_percent
    assert after_label == "Strong"


def test_fully_completed_profile_scores_hundred():
    profile = _base_profile()
    profile.update(
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "photo_url": "/api/candidate/test/photo/view",
            "location": "Remote",
            "current_role": "Senior Backend Engineer",
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "work_experience": [
                {"title": "Lead Engineer", "company": "Acme", "description": "Led platform work"}
            ],
            "education": [{"degree": "B.Tech", "institution": "Example University"}],
            "candidate_certificates": _certificate_rows("AWS Certified Solutions Architect"),
            "raw_data": {
                "availability": "Immediate",
                "location_preferences": ["Remote", "Hybrid"],
                "preferred_roles": ["Senior Backend Engineer"],
                "projects": ["AI automation platform"],
            },
        }
    )

    percent, label = _calculate_profile_strength(profile, profile["raw_data"])
    assert percent == 100
    assert label == "Strong"


def test_profile_strength_certifications_score_zero_without_candidate_certificates():
    profile = _base_profile()
    profile.update(
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "photo_url": "/api/candidate/test/photo/view",
            "location": "Remote",
            "current_role": "Senior Backend Engineer",
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "work_experience": [
                {"title": "Lead Engineer", "company": "Acme", "description": "Led platform work"}
            ],
            "education": [{"degree": "B.Tech", "institution": "Example University"}],
            "raw_data": {
                "availability": "Immediate",
                "location_preferences": ["Remote", "Hybrid"],
                "preferred_roles": ["Senior Backend Engineer"],
                "projects": ["AI automation platform"],
            },
        }
    )

    percent, label = _calculate_profile_strength(profile, profile["raw_data"])
    assert percent == 95
    assert label == "Strong"


def test_profile_strength_certifications_score_five_with_one_candidate_certificate():
    profile = _base_profile()
    profile.update(
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "photo_url": "/api/candidate/test/photo/view",
            "location": "Remote",
            "current_role": "Senior Backend Engineer",
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "work_experience": [
                {"title": "Lead Engineer", "company": "Acme", "description": "Led platform work"}
            ],
            "education": [{"degree": "B.Tech", "institution": "Example University"}],
            "candidate_certificates": _certificate_rows("AWS Certified Solutions Architect"),
            "raw_data": {
                "availability": "Immediate",
                "location_preferences": ["Remote", "Hybrid"],
                "preferred_roles": ["Senior Backend Engineer"],
                "projects": ["AI automation platform"],
            },
        }
    )

    percent, label = _calculate_profile_strength(profile, profile["raw_data"])
    assert percent == 100
    assert label == "Strong"


def test_profile_strength_certifications_score_stays_five_with_multiple_candidate_certificates():
    profile = _base_profile()
    profile.update(
        {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "photo_url": "/api/candidate/test/photo/view",
            "location": "Remote",
            "current_role": "Senior Backend Engineer",
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "work_experience": [
                {"title": "Lead Engineer", "company": "Acme", "description": "Led platform work"}
            ],
            "education": [{"degree": "B.Tech", "institution": "Example University"}],
            "candidate_certificates": _certificate_rows(
                "AWS Certified Solutions Architect",
                "GCP Professional Cloud Architect",
            ),
            "raw_data": {
                "availability": "Immediate",
                "location_preferences": ["Remote", "Hybrid"],
                "preferred_roles": ["Senior Backend Engineer"],
                "projects": ["AI automation platform"],
            },
        }
    )

    percent, label = _calculate_profile_strength(profile, profile["raw_data"])
    assert percent == 100
    assert label == "Strong"
