import asyncio
import json
import sys
import types

import pytest

from app.job_ingestion.job_skill_extraction import extract_missing_job_skills, _clean_extracted_skills
from app.job_ingestion.normalize import normalize_greenhouse, normalize_lever, normalize_ashby, normalize_fantastic, normalize_theirstack
from skill_normalization import canonical_skill, canonical_skill_key


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


def test_provider_and_llm_skills_are_monotonically_enriched(monkeypatch):
    class Completions:
        async def create(self, **_kwargs):
            message = types.SimpleNamespace(content=json.dumps({"skills": ["Python", "FastAPI", "Docker", "AWS"]}))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(GroqClientPool=lambda: client))
    job = {"ats_job_id": "f", "title": "Engineer", "description": "Build APIs", "requirements": "",
           "skills": ["Python", "FastAPI", "PostgreSQL"], "skills_required": ["Python", "FastAPI", "PostgreSQL"],
           "structured_data": {}}
    result = asyncio.run(extract_missing_job_skills(job))
    assert result["skills_required"] == ["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"]
    assert result["structured_data"]["skill_extraction"]["provider_skills"] == ["Python", "FastAPI", "PostgreSQL"]


def test_llm_smaller_result_cannot_reduce_existing_skills(monkeypatch):
    class Completions:
        async def create(self, **_kwargs):
            message = types.SimpleNamespace(content=json.dumps({"skills": ["Python"]}))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])
    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(GroqClientPool=lambda: client))
    original = ["Python", "FastAPI", "PostgreSQL", "Docker"]
    job = {"ats_job_id": "f", "title": "Engineer", "description": "Build APIs", "requirements": "",
           "skills": original[:], "skills_required": original[:], "structured_data": {}}
    assert asyncio.run(extract_missing_job_skills(job))["skills_required"] == original


def test_theirstack_usable_technology_skills_skip_llm(monkeypatch):
    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(
        GroqClientPool=lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called"))
    ))
    job = normalize_theirstack({
        "id": "ts-skip", "title": "Engineer", "company": "Acme", "description": "Build APIs",
        "technology_slugs": ["python", "spring-boot"],
    })

    assert asyncio.run(extract_missing_job_skills(job)) is job
    assert job["skills"] == job["skills_required"] == ["Python", "Spring Boot"]


def test_theirstack_missing_technology_skills_uses_mocked_llm(monkeypatch):
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            message = types.SimpleNamespace(content=json.dumps({"skills": ["Python"]}))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(GroqClientPool=lambda: client))
    job = normalize_theirstack({"id": "ts-extract", "title": "Engineer", "description": "Build APIs"})

    result = asyncio.run(extract_missing_job_skills(job))

    assert len(calls) == 1
    assert result["skills"] == result["skills_required"] == ["Python"]


def test_theirstack_pending_retry_with_usable_skills_uses_mocked_llm(monkeypatch):
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            message = types.SimpleNamespace(content=json.dumps({"skills": ["Docker"]}))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(GroqClientPool=lambda: client))
    job = normalize_theirstack({
        "id": "ts-retry", "title": "Engineer", "description": "Build APIs",
        "technology_slugs": ["python"],
    })
    job["structured_data"]["metadata_extraction_status"] = "pending_retry"

    result = asyncio.run(extract_missing_job_skills(job))

    assert len(calls) == 1
    assert result["skills"] == result["skills_required"] == ["Python", "Docker"]


@pytest.mark.parametrize("provider", ["fantastic", "theirstack"])
@pytest.mark.parametrize("skills", [([], []), (None, None), ([None, "  "], [None, "  "])])
def test_complete_global_provider_with_unusable_canonical_arrays_uses_llm(monkeypatch, provider, skills):
    calls = _mock_llm(monkeypatch)
    job = {
        "ats_type": provider, "ats_job_id": f"{provider}-complete-incomplete",
        "title": "Engineer", "description": "Build APIs", "skills": skills[0],
        "skills_required": skills[1],
        "structured_data": {"metadata_extraction_status": "complete"},
    }

    asyncio.run(extract_missing_job_skills(job))

    assert len(calls) == 1


@pytest.mark.parametrize("provider", ["fantastic", "theirstack"])
def test_complete_global_provider_with_both_usable_canonical_arrays_skips_llm(monkeypatch, provider):
    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(
        GroqClientPool=lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called"))
    ))
    job = {
        "ats_type": provider, "ats_job_id": f"{provider}-complete-ready",
        "title": "Engineer", "description": "Build APIs", "skills": ["Python"],
        "skills_required": ["Docker"],
        "structured_data": {"metadata_extraction_status": "complete"},
    }

    assert asyncio.run(extract_missing_job_skills(job)) is job


@pytest.mark.parametrize("provider", ["fantastic", "theirstack"])
def test_pending_global_provider_with_usable_canonical_arrays_remains_eligible(monkeypatch, provider):
    calls = _mock_llm(monkeypatch)
    job = {
        "ats_type": provider, "ats_job_id": f"{provider}-pending",
        "title": "Engineer", "description": "Build APIs", "skills": ["Python"],
        "skills_required": ["Docker"],
        "structured_data": {"metadata_extraction_status": "pending"},
    }

    asyncio.run(extract_missing_job_skills(job))

    assert len(calls) == 1


def _mock_llm(monkeypatch, skills=("Docker",)):
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            message = types.SimpleNamespace(content=json.dumps({"skills": list(skills)}))
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=Completions()))
    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(GroqClientPool=lambda: client))
    return calls


@pytest.mark.parametrize(
    ("normalizer", "payload"),
    [
        (normalize_greenhouse, {"id": "g-ready", "title": "Engineer", "content": "Required Skills: Python, AWS\nExperience Required: 5 years\nSalary Range: $100k-$120k\nEmployment Type: Full-time"}),
        (normalize_lever, {"id": "l-ready", "text": "Engineer", "descriptionPlain": "Required Skills: Python, AWS\nExperience Required: 5 years\nSalary Range: $100k-$120k", "categories": {"commitment": "Full-time"}}),
        (normalize_ashby, {"id": "a-ready", "title": "Engineer", "descriptionPlain": "Required Skills: Python, AWS\nExperience Required: 5 years\nSalary Range: $100k-$120k", "employmentType": "Full-time"}),
    ],
)
def test_deterministically_complete_ats_metadata_skips_llm(monkeypatch, normalizer, payload):
    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(
        GroqClientPool=lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called"))
    ))
    job = normalizer(payload, "Acme")

    assert asyncio.run(extract_missing_job_skills(job)) is job
    assert job["skills"] == job["skills_required"] == ["Python", "AWS"]


@pytest.mark.parametrize(
    ("normalizer", "payload"),
    [
        (normalize_greenhouse, {"id": "g-missing", "title": "Engineer", "content": "Required Skills: Python\nEmployment Type: Full-time"}),
        (normalize_lever, {"id": "l-missing", "text": "Engineer", "descriptionPlain": "Required Skills: Python", "categories": {"commitment": "Full-time"}}),
        (normalize_ashby, {"id": "a-missing", "title": "Engineer", "descriptionPlain": "Required Skills: Python", "employmentType": "Full-time"}),
    ],
)
def test_incomplete_deterministic_ats_metadata_uses_llm(monkeypatch, normalizer, payload):
    calls = _mock_llm(monkeypatch)
    job = normalizer(payload, "Acme")

    result = asyncio.run(extract_missing_job_skills(job))

    assert len(calls) == 1
    assert result["skills"] == result["skills_required"] == ["Python", "Docker"]


@pytest.mark.parametrize(
    ("normalizer", "payload", "field"),
    [
        (normalize_greenhouse, {
            "id": "g-undisclosed-salary", "title": "Engineer",
            "content": "Required Skills: Python\nExperience Required: 5 years\nSalary Range: Not disclosed\nEmployment Type: Full-time",
        }, "salary_range"),
        (normalize_ashby, {
            "id": "a-undisclosed-experience", "title": "Engineer",
            "descriptionPlain": "Required Skills: Python\nExperience Required: No experience required\nSalary Range: $100k-$120k",
            "employmentType": "Full-time",
        }, "experience_required"),
    ],
)
def test_explicitly_undisclosed_ats_metadata_skips_llm(monkeypatch, normalizer, payload, field):
    monkeypatch.setitem(sys.modules, "groq_client", types.SimpleNamespace(
        GroqClientPool=lambda: (_ for _ in ()).throw(AssertionError("LLM should not be called"))
    ))
    job = normalizer(payload, "Acme")

    assert job[field] is None
    assert job["structured_data"]["explicitly_undisclosed"] == {field: True}
    assert asyncio.run(extract_missing_job_skills(job)) is job


@pytest.mark.parametrize(
    ("normalizer", "payload"),
    [
        (normalize_greenhouse, {"id": "g-unparsed-salary", "title": "Engineer", "content": "Required Skills: Python\nExperience Required: 5 years\nEmployment Type: Full-time"}),
        (normalize_lever, {"id": "l-unparsed-experience", "text": "Engineer", "descriptionPlain": "Required Skills: Python\nSalary Range: $100k-$120k", "categories": {"commitment": "Full-time"}}),
    ],
)
def test_absent_ats_metadata_is_not_marked_undisclosed_and_uses_llm(monkeypatch, normalizer, payload):
    calls = _mock_llm(monkeypatch)
    job = normalizer(payload, "Acme")

    assert "explicitly_undisclosed" not in job["structured_data"]
    asyncio.run(extract_missing_job_skills(job))
    assert len(calls) == 1


def test_valid_ats_salary_and_experience_are_preserved_without_undisclosed_marker():
    job = normalize_ashby({
        "id": "a-valid-values", "title": "Engineer",
        "descriptionPlain": "Required Skills: Python\nExperience Required: 5 years\nSalary Range: $100k-$120k",
        "employmentType": "Full-time",
    }, "Acme")

    assert job["experience_required"] == "5 years"
    assert job["salary_range"] == "$100k-$120k"
    assert "explicitly_undisclosed" not in job["structured_data"]


def test_ashby_description_plain_is_merged_with_html_for_deterministic_parsing():
    job = normalize_ashby({
        "id": "a-plain-html", "title": "Engineer",
        "descriptionPlain": "Required Skills: Python, SQL\nExperience Required: 4 years",
        "descriptionHtml": "<p>Salary Range: $100k-$120k</p>",
        "employmentType": "Full-time", "department": {"name": "Engineering"}, "team": {"name": "Platform"},
    }, "Acme")

    assert "Python" in job["skills_required"] and "SQL" in job["skills_required"]
    assert job["experience_required"] == "4 years"
    assert job["salary_range"] == "$100k-$120k"
    assert job["department"] == "Engineering"


@pytest.mark.parametrize("status", ["pending", "pending_retry"])
def test_complete_ats_pending_retry_remains_eligible_for_llm(monkeypatch, status):
    calls = _mock_llm(monkeypatch)
    job = normalize_greenhouse({
        "id": "g-retry", "title": "Engineer",
        "content": "Required Skills: Python\nExperience Required: 5 years\nSalary Range: $100k-$120k\nEmployment Type: Full-time",
    }, "Acme")
    job["structured_data"]["metadata_extraction_status"] = status

    assert len(calls) == 0
    asyncio.run(extract_missing_job_skills(job))
    assert len(calls) == 1


def test_safe_aliases_and_non_equivalences():
    assert {canonical_skill(value) for value in ("Postgres", "PostgreSQL", "postgresql database")} == {"PostgreSQL"}
    assert canonical_skill("GCP") == "Google Cloud Platform"
    assert canonical_skill("ReactJS") == "React"
    for left, right in (("Java", "JavaScript"), ("Python", "PyTorch"), ("SQL", "PostgreSQL"),
                        ("Docker", "Kubernetes"), ("AWS", "AWS Lambda")):
        assert canonical_skill_key(left) != canonical_skill_key(right)


def test_skill_postprocessing_removes_metadata_and_preserves_capabilities():
    job = {"title": "Software Developer", "company_name": "Marriott International"}
    values = ["Marriott International", "Software Developer", "Remote work", "ESOP",
              "Healthcare", "Financial domain", "Financial modelling", "PostgreSQL",
              "Docker", "Vendor Management"]
    assert _clean_extracted_skills(values, job) == ["Financial modelling", "PostgreSQL", "Docker", "Vendor Management"]


def test_skill_postprocessing_deduplicates_obvious_variants_without_merging_technologies():
    job = {"title": "Engineer", "company_name": "Example"}
    result = _clean_extracted_skills(
        ["GIS Technology", "GIS", "BIM", "BIM Modelling", "Revit MEP", "Autodesk Revit MEP",
         "Java", "JavaScript", "React", "React Native", "AWS", "Azure", "SQL", "PostgreSQL"], job)
    assert result == ["GIS", "BIM", "Autodesk Revit MEP", "Java", "JavaScript", "React", "React Native", "AWS", "Azure", "SQL", "PostgreSQL"]
