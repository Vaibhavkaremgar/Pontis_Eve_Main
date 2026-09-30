import asyncio

from app.job_ingestion.job_skill_extraction import extract_missing_job_skills
from app.job_ingestion.normalize import normalize_greenhouse, normalize_lever, normalize_ashby, normalize_fantastic


def test_tegna_style_greenhouse_jd_extracts_embedded_skills():
    jd = """<h2>Required Qualifications</h2><ul><li>AWS CDK and CloudFormation</li>
    <li>Terraform, Kubernetes, Docker, Prometheus, Grafana</li><li>Python, Go, PostgreSQL, MongoDB, Linux</li>
    <li>REST and gRPC services</li></ul>"""
    job = normalize_greenhouse({"id": "5170150007", "title": "Senior DevOps", "content": jd}, "TEGNA India")
    assert {"AWS", "Kubernetes", "Terraform", "Docker", "Python", "Go", "PostgreSQL", "MongoDB", "Linux"}.issubset(set(job["skills_required"]))
    assert job["skills"] == job["skills_required"]


def test_all_supported_ats_use_common_jd_skill_extraction():
    text = "Required Qualifications: Python, AWS, Kubernetes, Terraform"
    jobs = [
        normalize_greenhouse({"id": "g", "title": "Engineer", "content": text}, "A"),
        normalize_lever({"id": "l", "text": "Engineer", "descriptionPlain": text}, "A"),
        normalize_ashby({"id": "a", "title": "Engineer", "descriptionHtml": text}, "A"),
        normalize_fantastic({"id": "f", "title": "Engineer", "organization": "A", "description_text": text}),
    ]
    assert all({"Python", "AWS", "Kubernetes", "Terraform"}.issubset(set(job["skills_required"])) for job in jobs)


def test_structured_skills_are_preserved_and_deduplicated():
    job = normalize_greenhouse({"id": "g", "title": "Engineer", "content": "Python", "skills_required": ["Python", "python", "AWS"]}, "A")
    assert job["skills_required"] == ["Python", "AWS"]


def test_postgres_alias_is_canonicalized_to_postgresql():
    job = normalize_greenhouse({"id": "g", "title": "Engineer", "content": "Required Qualifications: Postgres, PostgreSQL"}, "A")
    assert "PostgreSQL" in job["skills_required"]
    assert "Postgres" not in job["skills_required"]
    assert job["skills_required"].count("PostgreSQL") == 1


def test_no_identifiable_skills_remains_empty():
    job = normalize_greenhouse({"id": "g", "title": "Coordinator", "content": "Coordinate schedules and communicate with stakeholders."}, "A")
    assert job["skills_required"] == []


def test_invalid_or_unavailable_llm_output_does_not_create_garbage(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    monkeypatch.setattr("groq_client.GroqClientPool", lambda: (_ for _ in ()).throw(RuntimeError("test")))
    job = {"ats_job_id": "x", "title": "Engineer", "description": "Build systems", "requirements": ""}
    result = asyncio.run(extract_missing_job_skills(job))
    assert result.get("skills_required") is None
