"""
Unit tests for voice intake pure functions.
No network / DB required.
"""
import sys
import os
import json
import pytest

# Allow importing server module directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import (
    _merge_certifications,
    _merge_list,
    _merge_skills,
    _merge_voice_into_profile,
    _normalize_certifications,
)


class TestMergeList:
    def test_empty_existing_returns_new(self):
        assert _merge_list([], ["Python", "Go"]) == ["Python", "Go"]

    def test_empty_new_returns_existing(self):
        assert _merge_list(["Python"], []) == ["Python"]

    def test_deduplicates_case_insensitively(self):
        result = _merge_list(["Python", "FastAPI"], ["python", "Docker"])
        lower = [s.lower() for s in result]
        assert lower.count("python") == 1
        assert "docker" in lower

    def test_dicts_always_appended(self):
        existing = [{"title": "Dev", "company": "A"}]
        new = [{"title": "Lead", "company": "B"}]
        result = _merge_list(existing, new)
        assert len(result) == 2


class TestMergeVoiceIntoProfile:
    def _base_profile(self):
        return {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "+1234567890",
            "summary": "Experienced engineer",
            "current_role": "Software Engineer",
            "current_company": "Acme",
            "location": "New York",
            "experience_years": 5.0,
            "skills": ["Python", "Django"],
            "work_experience": [{"title": "Engineer", "company": "Acme", "description": "Built APIs"}],
            "education": [{"degree": "B.Sc CS", "institution": "MIT"}],
        }

    def test_existing_name_not_overwritten(self):
        profile = self._base_profile()
        voice = {"summary": "New summary", "current_role": ""}
        merged = _merge_voice_into_profile(profile, voice)
        assert merged["name"] == "Jane Doe"

    def test_existing_email_not_overwritten(self):
        profile = self._base_profile()
        voice = {"summary": "New summary"}
        merged = _merge_voice_into_profile(profile, voice)
        assert merged["email"] == "jane@example.com"

    def test_empty_voice_field_does_not_overwrite(self):
        profile = self._base_profile()
        voice = {"current_role": "", "location": ""}
        merged = _merge_voice_into_profile(profile, voice)
        assert merged["current_role"] == "Software Engineer"
        assert merged["location"] == "New York"

    def test_new_skills_merged(self):
        profile = self._base_profile()
        voice = {"skills": ["Docker", "Kubernetes", "Python"]}  # Python is duplicate
        merged = _merge_voice_into_profile(profile, voice)
        lower = [s.lower() for s in merged["skills"]]
        assert lower.count("python") == 1
        assert "docker" in lower
        assert "kubernetes" in lower

    def test_merge_skills_deduplicates_case_and_whitespace(self):
        result = _merge_skills(["Python"], [" python ", "PYTHON", " FastAPI "])
        assert result == ["Python", "FastAPI"]

    def test_merge_skills_keeps_different_languages_separate(self):
        result = _merge_skills(["Java"], ["JavaScript"])
        assert result == ["Java", "JavaScript"]

    def test_existing_skills_preserved(self):
        profile = self._base_profile()
        voice = {"skills": ["Go"]}
        merged = _merge_voice_into_profile(profile, voice)
        lower = [s.lower() for s in merged["skills"]]
        assert "python" in lower
        assert "django" in lower

    def test_missing_summary_filled_from_voice(self):
        profile = self._base_profile()
        profile["summary"] = ""
        voice = {"summary": "Voice-derived summary"}
        merged = _merge_voice_into_profile(profile, voice)
        assert "Voice-derived summary" in merged["summary"]
        assert "Software Engineer" in merged["summary"]

    def test_existing_summary_not_overwritten(self):
        profile = self._base_profile()
        voice = {
            "summary": "Voice-derived summary",
            "skills": ["Go"],
            "preferred_roles": ["Backend Engineer"],
            "availability": "30 days",
        }
        merged = _merge_voice_into_profile(profile, voice)
        assert "Experienced engineer" in merged["summary"]
        assert "Voice-derived summary" in merged["summary"]
        assert "Go" in merged["summary"]
        assert "Backend Engineer" in merged["summary"]
        assert "30 days" in merged["summary"]

    def test_summary_includes_resume_and_voice_information(self):
        profile = self._base_profile()
        voice = {
            "role_preference_bio": "Looking for backend roles using Java and Spring Boot.",
            "skills": ["Java", "Spring Boot", "Python"],
            "preferred_roles": ["Backend Engineer"],
            "availability": "30 days",
        }
        merged = _merge_voice_into_profile(profile, voice)
        assert "Experienced engineer" in merged["summary"]
        assert "Looking for backend roles using Java and Spring Boot." in merged["summary"]
        assert "Java" in merged["summary"]
        assert "Spring Boot" in merged["summary"]
        assert "Backend Engineer" in merged["summary"]
        assert "30 days" in merged["summary"]

    def test_summary_deduplicates_repeated_resume_and_voice_information(self):
        profile = self._base_profile()
        profile["summary"] = "Experienced engineer with Python and Django."
        voice = {
            "summary": "Experienced engineer with Python and Django.",
            "skills": ["Python", "Django", "Docker"],
        }
        merged = _merge_voice_into_profile(profile, voice)
        summary = merged["summary"]
        assert summary.count("Experienced engineer with Python and Django.") == 1
        assert summary.count("Python") == 1
        assert summary.count("Django") == 1
        assert "Docker" in summary

    def test_summary_preserves_resume_info_when_voice_is_unrelated(self):
        profile = self._base_profile()
        voice = {"additional_information": "I enjoy hiking on weekends."}
        merged = _merge_voice_into_profile(profile, voice)
        assert "Experienced engineer" in merged["summary"]
        assert "Software Engineer" in merged["summary"]
        assert "Acme" in merged["summary"]
        assert "I enjoy hiking on weekends." in merged["summary"]

    def test_summary_reflects_latest_merged_profile_state(self):
        profile = self._base_profile()
        voice = {
            "skills": ["Java", "Spring Boot"],
            "preferred_roles": ["Java Backend Developer"],
            "availability": "Immediately",
        }
        merged = _merge_voice_into_profile(profile, voice)
        summary = merged["summary"]
        assert "Java" in summary
        assert "Spring Boot" in summary
        assert "Java Backend Developer" in summary
        assert "Immediately" in summary
        assert "Python" in summary
        assert "Django" in summary

    def test_experience_years_not_overwritten_if_exists(self):
        profile = self._base_profile()
        voice = {"experience_years": 2}
        merged = _merge_voice_into_profile(profile, voice)
        assert merged["experience_years"] == 5.0

    def test_experience_years_filled_if_missing(self):
        profile = self._base_profile()
        profile["experience_years"] = None
        voice = {"experience_years": 7}
        merged = _merge_voice_into_profile(profile, voice)
        assert merged["experience_years"] == 7.0

    def test_certifications_are_kept_out_of_skills_when_they_are_clearly_certifications(self):
        profile = self._base_profile()
        voice = {
            "skills": ["AWS Certified Solutions Architect - Associate", "Python"],
            "certifications": [
                "aws certified solutions architect associate",
                "AWS Certified Solutions Architect - Associate",
            ],
        }
        merged = _merge_voice_into_profile(profile, voice)
        lower = [s.lower() for s in merged["skills"]]
        assert "python" in lower
        assert "aws certified solutions architect - associate" not in lower
        assert merged["raw_data"]["certifications"] == [
            "aws certified solutions architect associate"
        ]

    def test_normalize_certifications_collapses_near_duplicates(self):
        result = _normalize_certifications([
            "AWS Certified Solutions Architect - Associate",
            "aws certified solutions architect associate",
            "AWS Solutions Architect Associate",
        ])
        assert result == ["AWS Certified Solutions Architect - Associate"]

    def test_merge_certifications_deduplicates_existing_and_new_values(self):
        result = _merge_certifications(
            ["AWS Certified Solutions Architect - Associate"],
            [" aws certified solutions architect associate ", "AWS Solutions Architect Associate"],
        )
        assert result == ["AWS Certified Solutions Architect - Associate"]

    def test_merge_certifications_appends_genuinely_new_items(self):
        result = _merge_certifications(
            ["AWS Certified Solutions Architect - Associate"],
            ["Google Cloud Professional Data Engineer"],
        )
        assert result == [
            "AWS Certified Solutions Architect - Associate",
            "Google Cloud Professional Data Engineer",
        ]

    def test_work_experience_appended(self):
        profile = self._base_profile()
        voice = {"work_experience": [{"title": "Lead", "company": "NewCo", "description": "Led team"}]}
        merged = _merge_voice_into_profile(profile, voice)
        assert len(merged["work_experience"]) == 2

    def test_empty_voice_returns_unchanged_profile(self):
        profile = self._base_profile()
        merged = _merge_voice_into_profile(profile, {})
        assert merged["name"] == profile["name"]
        assert merged["skills"] == profile["skills"]
