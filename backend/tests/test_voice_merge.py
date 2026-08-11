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

from server import _merge_list, _merge_voice_into_profile


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
        assert merged["summary"] == "Voice-derived summary"

    def test_existing_summary_not_overwritten(self):
        profile = self._base_profile()
        voice = {"summary": "Voice-derived summary"}
        merged = _merge_voice_into_profile(profile, voice)
        assert merged["summary"] == "Experienced engineer"

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

    def test_certifications_added_to_skills(self):
        profile = self._base_profile()
        voice = {"certifications": ["AWS Certified", "GCP Professional"]}
        merged = _merge_voice_into_profile(profile, voice)
        lower = [s.lower() for s in merged["skills"]]
        assert "aws certified" in lower
        assert "gcp professional" in lower

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
