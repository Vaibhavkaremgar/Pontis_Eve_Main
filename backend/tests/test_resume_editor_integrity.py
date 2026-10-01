import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server


def test_resume_editor_payload_merges_all_profile_sources():
    candidate = {
        "name": "Candidate",
        "skills": ["Python", "AWS"],
        "work_experience": [{"company": "Company A"}],
        "education": [{"degree": "B.Tech"}],
        "raw_data": {
            "certifications": ["AWS Certification"],
            "projects": [{"title": "Project A"}],
        },
        "parsed_resume_json": {
            "skills": ["Kubernetes"],
            "work_experience": [{"company": "Company B"}],
            "education": [{"degree": "MBA"}],
            "certifications": ["Azure Fundamentals"],
            "projects": [{"title": "Project B"}],
        },
    }

    payload = server._resume_editor_payload(candidate)

    assert {s.casefold() for s in payload["skills"]} == {"python", "aws", "kubernetes"}
    assert {item["company"] for item in payload["work_experience"]} == {"Company A", "Company B"}
    assert {item["degree"] for item in payload["education"]} == {"B.Tech", "MBA"}
    assert payload["certifications"] == ["AWS Certification", "Azure Fundamentals"]
    assert {item["title"] for item in payload["projects"]} == {"Project A", "Project B"}


def test_resume_editor_empty_collections_are_not_delete_commands():
    existing = {
        "skills": ["Python"],
        "raw_data": {"certifications": ["AWS Certification"]},
        "parsed_resume_json": {},
    }
    payload = server._resume_editor_payload(existing)
    supplied = {"skills": [], "certifications": [], "projects": []}

    # Mirrors the defensive merge contract used by _save_resume_editor_updates.
    for field in ("skills", "certifications", "projects"):
        if supplied[field]:
            payload[field] = supplied[field]

    assert payload["skills"] == ["Python"]
    assert payload["certifications"] == ["AWS Certification"]
    assert payload["projects"] == []


def test_project_merge_preserves_partial_and_null_fields():
    existing = {
        "name": "Resume Parser",
        "description": "Built a resume parsing system using Python",
        "technologies": ["Python", "FastAPI"],
        "role": "Backend Engineer",
    }
    incoming = {
        "name": "Resume Parser",
        "description": "",
        "technologies": [],
        "role": None,
    }

    merged = server._merge_projects([existing], [incoming])

    assert merged == [{
        "title": "Resume Parser",
        "role": "Backend Engineer",
        "description": existing["description"],
        "technologies": existing["technologies"],
    }]


def test_project_merge_adds_new_fields_and_projects_without_duplicates():
    existing = [{"name": "Resume Parser", "description": "Built using Python", "technologies": ["Python"]}]
    incoming = [
        {"name": "Resume Parser", "description": "Resume extraction system", "technologies": ["FastAPI"]},
        {"name": "Job Matcher", "description": "Matches candidates to jobs", "technologies": ["AWS"]},
    ]

    once = server._merge_projects(existing, incoming)
    twice = server._merge_projects(once, incoming)

    assert len(once) == 2
    parser = next(project for project in once if project["title"] == "Resume Parser")
    assert "Built using Python" in parser["description"]
    assert "Resume extraction system" in parser["description"]
    assert parser["technologies"] == ["Python", "FastAPI"]
    assert twice == once
