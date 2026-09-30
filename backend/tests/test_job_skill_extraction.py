import asyncio

from app.job_ingestion.job_skill_extraction import extract_missing_job_skills
from app.job_ingestion.normalize import normalize_greenhouse, normalize_lever, normalize_ashby, normalize_fantastic


def test_go_is_extracted_only_in_programming_language_contexts():
    valid = [
        "Go/Golang",
        "Go programming and Go language experience",
        "You have written in Go.",
        "Experience with Go and proficiency in Go development.",
        "Go developer building Go services and Go applications.",
        "You'll write Go and Python.",
    ]
    for phrase in valid:
        job = normalize_greenhouse({"id": phrase, "title": "Engineer", "content": phrase}, "A")
        assert "Go" in job["skills_required"], phrase


def test_go_is_not_extracted_from_ordinary_english_contexts():
    invalid = [
        "go-live deployment",
        "go-to-market programs",
        "go from idea to launch",
        "go through the process",
        "go above and beyond",
        "Our goals are ambitious.",
    ]
    for phrase in invalid:
        job = normalize_greenhouse({"id": phrase, "title": "Coordinator", "content": phrase}, "A")
        assert "Go" not in job["skills_required"], phrase


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
    job = normalize_greenhouse({"id": "g", "title": "Engineer", "content": "Required Qualifications: Postgres"}, "A")
    assert "PostgreSQL" in job["skills_required"]
    assert "Postgres" not in job["skills_required"]
    assert job["skills_required"].count("PostgreSQL") == 1


def test_business_tools_are_extracted_case_insensitively_and_deduplicated():
    jd = "<h2>Required Qualifications</h2><ul><li>SALESFORCE and Workday</li><li>Excel, salesforce, WORKDAY</li></ul>"
    job = normalize_greenhouse({"id": "g", "title": "People Operations", "content": jd}, "A")
    assert job["skills_required"] == ["Salesforce", "Workday", "Excel"]


def test_business_tools_use_whole_word_matching():
    jd = "The team works on Salesforce integrations and Workday reporting in Excel."
    job = normalize_greenhouse({"id": "g", "title": "Analyst", "content": jd}, "A")
    assert set(job["skills_required"]) == {"Salesforce", "Workday", "Excel"}
    negative = normalize_greenhouse({"id": "g2", "title": "Coordinator", "content": "salesforceful work, workdaydream planning, excelled at communication."}, "A")
    assert negative["skills_required"] == []


def test_business_tools_are_shared_across_ats_normalizers():
    text = "Required Qualifications: Salesforce, Workday, Excel"
    jobs = [
        normalize_greenhouse({"id": "g", "title": "Role", "content": text}, "A"),
        normalize_lever({"id": "l", "text": "Role", "descriptionPlain": text}, "A"),
        normalize_ashby({"id": "a", "title": "Role", "descriptionHtml": text}, "A"),
        normalize_fantastic({"id": "f", "title": "Role", "organization": "A", "description_text": text}),
    ]
    assert all(set(["Salesforce", "Workday", "Excel"]).issubset(set(job["skills_required"])) for job in jobs)


def test_no_identifiable_skills_remains_empty():
    job = normalize_greenhouse({"id": "g", "title": "Coordinator", "content": "Coordinate schedules and communicate with stakeholders."}, "A")
    assert job["skills_required"] == []


def test_invalid_or_unavailable_llm_output_does_not_create_garbage(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_1", "test-key")
    monkeypatch.setattr("groq_client.GroqClientPool", lambda: (_ for _ in ()).throw(RuntimeError("test")))
    job = {"ats_job_id": "x", "title": "Engineer", "description": "Build systems", "requirements": ""}
    result = asyncio.run(extract_missing_job_skills(job))
    assert result.get("skills_required") is None
