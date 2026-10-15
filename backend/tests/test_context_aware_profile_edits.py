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
    assert "1." in result["reply"] and "2." in result["reply"] and "Both" in result["reply"]


def test_unique_replacement_has_a_validated_target_instruction():
    candidate = _candidate(education=[{"degree": "B.Tech", "institution": "CMR"}])
    result = server._chat_profile_preflight("Update CMR to CBIT", candidate, [{"role": "user", "content": "Update CMR to CBIT"}])
    assert result["updates"]["profile_record_replacements"] == [{"section": "Education", "old": "CMR", "new": "CBIT", "record_indexes": [0], "all_matches": False}]


def test_new_education_and_work_request_missing_timeline():
    education = server._chat_profile_preflight("I completed my Master's at CMR University.", _candidate(), [])
    work = server._chat_profile_preflight("I worked at ABC Technologies as a Java Developer.", _candidate(), [])
    assert "YYYY - YYYY" in education["reply"]
    assert "ABC Technologies" in work["reply"] and "YYYY - YYYY" in work["reply"]


def test_bachelors_paraphrase_does_not_duplicate_existing_btech():
    candidate = _candidate(education=[{"degree": "B.Tech", "institution": "CMR Engineering College", "end_date": "2022"}])
    result = server._chat_profile_preflight("I have done my bachelors in 2022", candidate, [])
    assert result["updates"] is None
    assert "already" in result["reply"].lower()


def test_btech_add_does_not_become_delete_or_duplicate():
    candidate = _candidate(education=[{"degree": "B.Tech", "institution": "CMR Engineering College", "end_date": "2022"}])
    result = server._chat_profile_preflight("I have done my btech in 2022", candidate, [])
    assert result["updates"] is None
    assert "already" in result["reply"].lower()
    assert "remove" not in result["reply"].lower()


def test_duplicate_list_items_ask_before_writing_across_sections():
    candidate = _candidate()
    candidate.update({
        "skills": ["Python"],
        "certifications": ["AWS Solutions Architect"],
        "preferred_roles": ["Backend Engineer"],
        "preferred_locations": ["Bengaluru"],
    })
    for message in (
        "Add Python to my skills",
        "Add AWS Solutions Architect to my certifications",
        "Add Backend Engineer to my preferred roles",
        "Add Bengaluru to my preferred locations",
    ):
        result = server._chat_profile_preflight(message, candidate, [])
        assert result["updates"] is None
        assert "update" in result["reply"].lower()
        assert "add" in result["reply"].lower()


def test_duplicate_preferred_role_uses_authoritative_preferences_row():
    candidate = _candidate()
    candidate["_prefs_row"] = {"preferred_roles": ["Python Developer"]}
    duplicate = server._duplicate_profile_update(candidate, {"preferred_roles": ["python developer"]})
    assert duplicate == ("preferred roles", "Python Developer")


def test_same_company_experience_requires_update_or_add_choice():
    candidate = _candidate(work=[{
        "title": "Python Developer", "company": "ABC Technologies",
        "start_date": "2022", "end_date": "2024",
    }])
    duplicate = server._duplicate_profile_update(candidate, {"work_experience": [{
        "title": "Senior Python Developer", "company": "ABC Technologies",
        "start_date": "2022", "end_date": "2024",
    }]})
    assert duplicate == ("work experience", "ABC Technologies")


def test_deterministic_reply_acknowledges_candidate():
    reply = server._acknowledge_candidate_reply(
        "Add my experience at ABC Technologies",
        "I can see ABC Technologies already in your profile.",
    )
    assert reply.startswith("Got it")
    assert "ABC Technologies" in reply


def test_add_confirmation_keeps_existing_and_uses_previous_request():
    candidate = _candidate(work=[{
        "title": "Python Developer", "company": "ABC Technologies",
        "start_date": "2020", "end_date": "2022",
    }])
    history = [
        {"role": "user", "content": "I worked at ABC Technologies as a Senior Python Developer from 2022 to 2024"},
        {"role": "assistant", "content": "I already have ABC Technologies in your profile. Should I update/replace that work experience, or add this as a separate record? Please say update or add."},
        {"role": "user", "content": "add"},
    ]
    result = server._chat_profile_preflight("add", candidate, history)
    assert result["updates"]["work_experience"][0]["company"] == "ABC Technologies"
    assert "separate" in result["reply"].lower()


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
