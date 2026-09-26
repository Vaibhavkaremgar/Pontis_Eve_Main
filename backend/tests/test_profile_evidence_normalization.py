"""Regression coverage for canonical Chat, Voice, and Resume evidence handling."""
import sys

sys.path.insert(0, "backend")

from server import (
    _append_demonstrated_skill_evidence,
    _merge_projects,
    _merge_resume_into_existing_profile,
    _merge_voice_into_profile,
    _normalize_certifications,
    _normalize_projects,
    _normalize_skills,
    _projects_explicitly_named_in_work_experience,
    _sanitize_profile_updates,
)
from profile_strength_service import _backfill_demonstrated_skill_evidence, build_attribute_evidence


def test_malformed_skills_are_cleaned_without_losing_java_stack():
    skills = _normalize_skills([
        "I'm looking for Java roles", "Java, Spring Boot, Hibernate", "Java", "PythonFastAPI"
    ])
    assert skills == ["Java", "Spring Boot", "Hibernate", "Python", "FastAPI"]


def test_project_schema_preserves_only_supplied_evidence_and_merges_duplicates():
    project = _normalize_projects([{
        "project_name": "Order API", "role": "Backend Developer",
        "description": "Built APIs", "responsibilities": ["Built APIs"],
        "technologies": ["FastAPI", "PostgreSQL"], "outcomes": ["Reduced latency"],
    }])[0]
    assert project == {
        "project_name": "Order API", "title": "Order API", "role": "Backend Developer",
        "description": "Built APIs", "responsibilities": ["Built APIs"],
        "technologies": ["FastAPI", "PostgreSQL"], "outcomes": ["Reduced latency"],
    }
    assert len(_merge_projects([project], [{"title": "order api", "technologies": ["FastAPI"]}])) == 1
    from_work = _projects_explicitly_named_in_work_experience([{
        "title": "Backend Developer", "description": "Project: Order API using FastAPI and PostgreSQL."
    }])
    assert from_work[0]["project_name"] == "Order API"
    assert from_work[0]["technologies"] == ["PostgreSQL", "FastAPI"]


def test_direct_project_usage_creates_demonstrated_evidence_but_a_list_does_not():
    evidence = _append_demonstrated_skill_evidence(
        {}, ["FastAPI", "PostgreSQL"], "I built FastAPI APIs with PostgreSQL.", "eve_chat"
    )
    assert evidence["demonstrated_skill_evidence"][0]["skills"] == ["FastAPI", "PostgreSQL"]
    assert _append_demonstrated_skill_evidence({}, ["FastAPI"], "Skills: FastAPI", "eve_chat") == {}


def test_certification_cleanup_rejects_learning_sentence():
    assert _normalize_certifications([
        "I've been strengthening my Java skills", "AWS Certified Solutions Architect - Associate"
    ]) == ["AWS Certified Solutions Architect - Associate"]


def test_java_coursework_is_not_demonstrated_experience():
    assert _append_demonstrated_skill_evidence({}, ["Java", "Spring Boot"], "I am learning Java and Spring Boot.", "eve_chat") == {}
    assert _normalize_skills(["Java", "Spring Boot", "Hibernate"]) == ["Java", "Spring Boot", "Hibernate"]


def test_work_style_usage_phrases_extract_demonstrated_technology_without_inventing_it():
    skills = ["Python", "FastAPI", "REST APIs", "Redis", "PostgreSQL", "MySQL", "Embeddings", "Qdrant", "Semantic Search"]
    statement = (
        "Developed backend applications and REST APIs using Python and FastAPI. "
        "Used Redis for caching and PostgreSQL/MySQL. "
        "Implemented semantic job matching using embeddings and Qdrant."
    )
    evidence = _append_demonstrated_skill_evidence({}, skills, statement, "resume_work_experience")
    assert evidence["demonstrated_skill_evidence"][0]["skills"] == skills


def test_generic_fragments_and_preferences_are_not_skills():
    assert _normalize_skills([
        "I’m looking for a role where I can grow", "contribute to real-world projects",
        "but I’ve also worked with Java", "Backend databases", "Java frameworks",
        "JavaOps", "network", "attack", "sprint", "Java", "Spring Boot",
    ]) == ["Java", "Spring Boot"]


def test_profile_strength_reader_uses_the_same_raw_data_evidence_key():
    candidate = {
        "skills": ["Python", "FastAPI", "Redis", "Qdrant", "Semantic Search"],
        "work_experience": [{"description": "Developed backend applications using Python and FastAPI; used Redis. Implemented semantic job matching using Qdrant."}],
        "raw_data": {},
    }
    raw = _backfill_demonstrated_skill_evidence(candidate, {})
    assert raw["demonstrated_skill_evidence"][0]["skills"] == ["Python", "FastAPI", "Redis", "Qdrant", "Semantic Search"]
    assert build_attribute_evidence({**candidate, "raw_data": raw})["skills"]["evidence_level"] == 3


def test_chat_voice_resume_use_the_same_deduplicated_skill_and_project_rules():
    chat = _sanitize_profile_updates({"skills": ["Java", "Java", "I want Java roles"], "projects": [{"title": "Order API"}]})
    voice = _merge_voice_into_profile({"skills": ["Java"], "raw_data": {"projects": [{"title": "Order API"}]}}, {"skills": ["Java", "Spring Boot"], "projects": [{"project_name": "order api"}]})
    resume = _merge_resume_into_existing_profile({"skills": ["Java"], "work_experience": [], "education": []}, {"skills": ["Java", "Spring Boot"], "work_experience": [], "education": []})
    assert chat["skills"] == ["Java"]
    assert voice["skills"] == resume["skills"] == ["Java", "Spring Boot"]
    assert len(voice["raw_data"]["projects"]) == 1
