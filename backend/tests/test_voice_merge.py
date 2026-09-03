"""
Unit tests for voice intake pure functions.
No network / DB required.
"""
import sys
import os
import json
import re
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
        summary = merged["summary"]
        assert "Software Engineer" in summary
        assert "Python" in summary
        assert "Voice-derived summary" in summary
        assert "Acme" not in summary

    def test_existing_summary_not_overwritten(self):
        profile = self._base_profile()
        voice = {
            "summary": "Voice-derived summary",
            "skills": ["Go"],
            "preferred_roles": ["Backend Engineer"],
            "availability": "30 days",
        }
        merged = _merge_voice_into_profile(profile, voice)
        summary = merged["summary"]
        assert "Software Engineer" in summary
        assert "Go" in summary
        assert "Voice-derived summary" in summary
        assert "Backend Engineer" in summary
        assert "30 days" not in summary
        assert "Acme" not in summary

    def test_summary_includes_resume_and_voice_information(self):
        profile = self._base_profile()
        voice = {
            "role_preference_bio": "Looking for backend roles using Java and Spring Boot.",
            "skills": ["Java", "Spring Boot", "Python"],
            "preferred_roles": ["Backend Engineer"],
            "availability": "30 days",
        }
        merged = _merge_voice_into_profile(profile, voice)
        summary = merged["summary"]
        sentence_count = len([part for part in re.split(r"[.!?]+", summary) if part.strip()])
        assert 2 <= sentence_count <= 4
        assert "Software Engineer" in summary
        assert "Python" in summary
        assert "Java" in summary
        assert "Spring Boot" in summary
        assert "Backend Engineer" in summary
        assert "Acme" not in summary
        assert "MIT" not in summary
        assert "5.0" not in summary
        assert "30 days" not in summary
        assert "Built APIs" not in summary
        assert summary.count("Python") == 1

    def test_summary_deduplicates_repeated_resume_and_voice_information(self):
        profile = self._base_profile()
        profile["summary"] = "Experienced engineer with Python and Django."
        voice = {
            "summary": "Experienced engineer with Python and Django.",
            "skills": ["Python", "Django", "Docker"],
            "preferred_roles": ["Backend Engineer"],
        }
        merged = _merge_voice_into_profile(profile, voice)
        summary = merged["summary"]
        assert summary.count("Python") == 1
        assert summary.count("Django") == 1
        assert "Docker" in summary
        assert "Backend Engineer" in summary

    def test_summary_preserves_resume_info_when_voice_is_unrelated(self):
        profile = self._base_profile()
        voice = {"additional_information": "I enjoy hiking on weekends."}
        merged = _merge_voice_into_profile(profile, voice)
        summary = merged["summary"]
        assert "Software Engineer" in summary
        assert "Python" in summary
        assert "Django" in summary
        assert "I enjoy hiking on weekends." in summary
        assert "Acme" not in summary
        assert "MIT" not in summary

    def test_summary_reflects_latest_merged_profile_state(self):
        profile = self._base_profile()
        voice = {
            "skills": ["Java", "Spring Boot"],
            "preferred_roles": ["Java Backend Developer"],
            "availability": "Immediately",
        }
        merged = _merge_voice_into_profile(profile, voice)
        summary = merged["summary"]
        assert "Software Engineer" in summary
        assert "Java" in summary
        assert "Spring Boot" in summary
        assert "Java Backend Developer" in summary
        assert "Python" in summary
        assert "Django" in summary
        assert "Immediately" not in summary
        assert "Acme" not in summary

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

    def test_existing_experience_is_enriched_instead_of_duplicated(self):
        profile = self._base_profile()
        profile["work_experience"] = [
            {
                "title": "Backend Developer",
                "company": "Acme",
                "start_date": "2022-01-01",
                "end_date": "Present",
                "description": "Built APIs",
            }
        ]
        voice = {
            "work_experience": [
                {
                    "title": "Python Backend Developer",
                    "company": "Acme",
                    "description": "Maintained services and added FastAPI endpoints.",
                }
            ]
        }
        merged = _merge_voice_into_profile(profile, voice)
        assert len(merged["work_experience"]) == 1
        exp = merged["work_experience"][0]
        assert exp["title"] == "Python Backend Developer"
        assert exp["company"] == "Acme"
        assert exp["start_date"] == "2022-01-01"
        assert exp["end_date"] == "Present"
        assert "Built APIs" in exp["description"]
        assert "Maintained services" in exp["description"]

    def test_retaking_voice_intake_is_idempotent(self):
        profile = self._base_profile()
        voice = {
            "work_experience": [
                {
                    "title": "Backend Developer",
                    "company": "Acme",
                    "start_date": "2022-01-01",
                    "end_date": "Present",
                    "description": "Built APIs",
                }
            ],
            "education": [
                {"degree": "B.Sc CS", "institution": "MIT"},
            ],
            "skills": ["Python", "Docker"],
            "certifications": ["AWS Certified Solutions Architect - Associate"],
        }
        merged_once = _merge_voice_into_profile(profile, voice)
        merged_twice = _merge_voice_into_profile(merged_once, voice)
        assert merged_twice["work_experience"] == merged_once["work_experience"]
        assert merged_twice["education"] == merged_once["education"]
        assert merged_twice["skills"] == merged_once["skills"]
        assert merged_twice["raw_data"]["certifications"] == merged_once["raw_data"]["certifications"]

    def test_real_start_dates_are_preserved_and_present_is_not_doubled(self):
        profile = self._base_profile()
        profile["work_experience"] = [
            {
                "title": "Backend Developer",
                "company": "Acme",
                "start_date": "2022-01-01",
                "end_date": "",
            }
        ]
        voice = {
            "work_experience": [
                {
                    "title": "Python Backend Developer",
                    "company": "Acme",
                    "end_date": "Present",
                    "description": "Worked on APIs",
                }
            ]
        }
        merged = _merge_voice_into_profile(profile, voice)
        exp = merged["work_experience"][0]
        assert exp["start_date"] == "2022-01-01"
        assert exp["end_date"] == "Present"
        assert exp.get("dates", "") != "Present — Present"
        assert exp.get("dates", "") != "Present - Present"

    def test_missing_start_date_does_not_generate_present_present(self):
        profile = self._base_profile()
        profile["work_experience"] = [
            {
                "title": "Backend Developer",
                "company": "Acme",
                "end_date": "Present",
            }
        ]
        voice = {
            "work_experience": [
                {
                    "title": "Python Backend Developer",
                    "company": "Acme",
                    "end_date": "Present",
                    "description": "Worked on APIs",
                }
            ]
        }
        merged = _merge_voice_into_profile(profile, voice)
        exp = merged["work_experience"][0]
        assert exp["end_date"] == "Present"
        assert exp.get("dates", "") not in {"Present â€” Present", "Present - Present"}
        assert not exp.get("dates", "").startswith("Present â€” Present")

    def test_education_is_preserved_and_merged_without_duplicates(self):
        profile = self._base_profile()
        profile["education"] = [
            {"degree": "B.Sc CS", "institution": "MIT", "start_date": "2012", "end_date": "2016"}
        ]
        voice = {
            "education": [
                {"degree": "B.Sc CS", "institution": "MIT", "location": "Cambridge"},
                {"degree": "M.Sc CS", "institution": "Stanford"},
            ]
        }
        merged = _merge_voice_into_profile(profile, voice)
        assert len(merged["education"]) == 2
        first = merged["education"][0]
        assert first["degree"] == "B.Sc CS"
        assert first["institution"] == "MIT"
        assert first["start_date"] == "2012"
        assert first["end_date"] == "2016"
        assert first["location"] == "Cambridge"

    def test_voice_intake_month_year_start_date_preserved(self):
        """Regression: 'January 2025' start date from voice intake must not become 'Present'."""
        from server import _merge_work_experience
        existing = [
            {"title": "Backend Developer", "company": "Viralbug", "description": "Built services."}
        ]
        voice_item = [
            {
                "title": "Backend Developer",
                "company": "Viralbug",
                "start_date": "January 2025",
                "end_date": "Present",
            }
        ]
        merged = _merge_work_experience(existing, voice_item)
        assert len(merged) == 1
        exp = merged[0]
        assert exp["start_date"] == "January 2025"
        assert exp["end_date"] == "Present"
        dates = exp.get("dates") or ""
        assert "January 2025" in dates
        assert "Present" in dates
        assert dates.count("Present") == 1

    def test_empty_voice_returns_unchanged_profile(self):
        profile = self._base_profile()
        merged = _merge_voice_into_profile(profile, {})
        assert merged["name"] == profile["name"]
        assert merged["skills"] == profile["skills"]


class TestRegressionEducationAndNewJob:
    """
    Regression tests for:
    1. Education date preservation — resume dates must not be overwritten by Voice Intake.
    2. New current job via Voice Intake — a different company must create a separate
       work-experience record, not be merged into the previous employer's entry.
    """

    def test_education_dates_preserved_when_voice_adds_extra_fields(self):
        """
        Resume education entry has start_date/end_date.
        Voice Intake provides the same degree/institution with no dates.
        The original dates must be preserved exactly.
        """
        from server import _merge_education

        existing = [
            {
                "degree": "B.Sc Computer Science",
                "institution": "MIT",
                "start_date": "2015",
                "end_date": "2019",
            }
        ]
        voice_edu = [
            {
                "degree": "B.Sc Computer Science",
                "institution": "MIT",
                "location": "Cambridge, MA",
                # no start_date / end_date from voice
            }
        ]
        merged = _merge_education(existing, voice_edu)
        assert len(merged) == 1
        entry = merged[0]
        assert entry["start_date"] == "2015", "start_date must not be cleared"
        assert entry["end_date"] == "2019", "end_date must not be cleared"
        assert entry["location"] == "Cambridge, MA", "new field from voice should be added"

    def test_education_dates_not_overwritten_by_voice_dates(self):
        """
        Resume has specific dates; Voice Intake provides different/empty dates.
        Resume dates must win.
        """
        from server import _merge_education

        existing = [
            {
                "degree": "M.Sc Data Science",
                "institution": "Stanford",
                "start_date": "2019",
                "end_date": "2021",
            }
        ]
        voice_edu = [
            {
                "degree": "M.Sc Data Science",
                "institution": "Stanford",
                "start_date": "",   # voice provides empty — must not overwrite
                "end_date": "",
            }
        ]
        merged = _merge_education(existing, voice_edu)
        assert len(merged) == 1
        entry = merged[0]
        assert entry["start_date"] == "2019"
        assert entry["end_date"] == "2021"

    def test_voice_intake_new_current_job_creates_separate_record(self):
        """
        Candidate has an existing resume entry at 'Viral Bug'.
        Voice Intake reports a NEW current job at 'NewCorp' with a different title.
        The result must have TWO separate work-experience records — the original
        Viral Bug entry must be unchanged (same company, same description, same dates).
        """
        from server import _merge_work_experience

        existing = [
            {
                "title": "Python Developer",
                "company": "Viral Bug",
                "start_date": "2022-01-01",
                "end_date": "2024-06-30",
                "description": "Built backend services with FastAPI.",
            }
        ]
        voice_new_job = [
            {
                "title": "Senior Backend Engineer",
                "company": "NewCorp",
                "start_date": "January 2025",
                "end_date": "Present",
                "description": "Leading API platform development.",
            }
        ]
        merged = _merge_work_experience(existing, voice_new_job)

        assert len(merged) == 2, (
            f"Expected 2 separate work-experience records, got {len(merged)}: {merged}"
        )

        viral_bug = next((e for e in merged if "Viral Bug" in (e.get("company") or "")), None)
        newcorp = next((e for e in merged if "NewCorp" in (e.get("company") or "")), None)

        assert viral_bug is not None, "Viral Bug entry must be preserved"
        assert newcorp is not None, "NewCorp entry must be created"

        # Original entry must be completely unchanged
        assert viral_bug["title"] == "Python Developer"
        assert viral_bug["company"] == "Viral Bug"
        assert viral_bug["start_date"] == "2022-01-01"
        assert viral_bug["end_date"] == "2024-06-30"
        assert "FastAPI" in viral_bug["description"]

        # New entry must carry the voice-provided data
        assert newcorp["title"] == "Senior Backend Engineer"
        assert newcorp["start_date"] == "January 2025"
        assert newcorp["end_date"] == "Present"

    def test_voice_intake_same_company_merges_not_duplicates(self):
        """
        Voice Intake provides updated info for the SAME company already in the resume.
        Must enrich the existing entry, not create a duplicate.
        """
        from server import _merge_work_experience

        existing = [
            {
                "title": "Backend Developer",
                "company": "Acme Corp",
                "description": "Built REST APIs.",
            }
        ]
        voice_same = [
            {
                "title": "Backend Developer",
                "company": "Acme Corp",
                "start_date": "March 2023",
                "end_date": "Present",
            }
        ]
        merged = _merge_work_experience(existing, voice_same)

        assert len(merged) == 1, "Same company must be merged, not duplicated"
        entry = merged[0]
        assert entry["company"] == "Acme Corp"
        assert entry["start_date"] == "March 2023"
        assert "REST APIs" in entry["description"]
