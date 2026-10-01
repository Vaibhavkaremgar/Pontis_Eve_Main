import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import server


def _preflight(messages):
    return server._chat_profile_preflight(
        messages[-1], {"work_experience": []},
        [{"role": "user", "content": item} for item in messages],
    )


def test_complete_work_experience_appends_and_preserves_existing():
    result = _preflight(["I am currently working as Backend Engineer at ABC Technologies from January 2024 to Present."])
    assert result["updates"]["work_experience"][0]["end_date"] == "Present"


def test_incomplete_work_experience_collects_required_fields():
    assert "title" in _preflight(["I started a new job at ABC Technologies."])["reply"]
    assert "company" in _preflight(["I started a new job as Backend Engineer."])["reply"]
    assert "start" in _preflight(["I work as Backend Engineer at ABC Technologies."])["reply"]
    assert "time period" in _preflight(["I worked as Backend Engineer at ABC Technologies."])["reply"]


def test_multi_turn_work_experience_is_persistable_only_when_complete():
    result = _preflight([
        "I started a new job at ABC Technologies.",
        "Backend Engineer",
        "January 2024",
        "Present",
    ])
    entry = result["updates"]["work_experience"][0]
    assert entry == {"title": "Backend Engineer", "company": "ABC Technologies", "start_date": "January 2024", "end_date": "Present"}


def test_project_at_company_is_not_work_experience():
    result = _preflight(["I built a project at ABC Technologies called Hiring Portal."])
    assert result is None
