"""Safety and multi-turn coverage for Eve's deterministic profile-edit gate."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server


def _candidate(education=None, work=None):
    return {
        "education": education or [], "work_experience": work or [],
        "skills": [], "raw_data": {}, "parsed_resume_json": {},
    }


def test_bare_skill_addition_asks_for_destination():
    result = server._chat_profile_preflight("Add Python", _candidate(), [{"role": "user", "content": "Add Python"}])
    assert result["updates"] is None
    assert result["reply"] == "Where would you like me to add Python?"


def test_duplicate_replacement_never_selects_a_section():
    candidate = _candidate(
        [{"degree": "B.Tech", "institution": "CMR"}],
        [{"title": "Engineer", "company": "CMR"}],
    )
    result = server._chat_profile_preflight("Update CMR to CBIT", candidate, [{"role": "user", "content": "Update CMR to CBIT"}])
    assert result["updates"] is None
    assert "Education" in result["reply"] and "Work Experience" in result["reply"]


def test_unique_replacement_has_a_validated_target_instruction():
    candidate = _candidate(education=[{"degree": "B.Tech", "institution": "CMR"}])
    result = server._chat_profile_preflight("Update CMR to CBIT", candidate, [{"role": "user", "content": "Update CMR to CBIT"}])
    assert result["updates"]["profile_record_replacements"] == [{"section": "Education", "old": "CMR", "new": "CBIT"}]


def test_new_education_and_work_request_missing_timeline():
    education = server._chat_profile_preflight("I completed my Master's at CMR University.", _candidate(), [])
    work = server._chat_profile_preflight("I worked at ABC Technologies as a Java Developer.", _candidate(), [])
    assert "YYYY - YYYY" in education["reply"]
    assert "ABC Technologies" in work["reply"] and "YYYY - YYYY" in work["reply"]


def test_timeline_follow_up_completes_original_education_operation():
    history = [
        {"role": "user", "content": "Add my Master's from CBIT."},
        {"role": "assistant", "content": "What was the time period? Please provide YYYY - YYYY."},
        {"role": "user", "content": "2022 - 2024"},
    ]
    result = server._chat_profile_preflight("2022 - 2024", _candidate(), history)
    record = result["updates"]["education"][0]
    assert record == {"degree": "Master's", "institution": "CBIT", "start_date": "2022", "end_date": "2024"}


def test_delete_with_multiple_matches_asks_before_any_write():
    candidate = _candidate(work=[{"title": "Java Developer", "company": "A"}, {"title": "Java Engineer", "company": "B"}])
    result = server._chat_profile_preflight("Delete my Java experience", candidate, [])
    assert result["updates"] is None
    assert "Which record" in result["reply"]
