import asyncio

from app.job_ingestion import scheduler
from app.job_ingestion.job_ingestion_service import description_fingerprint, find_existing_ats_job, find_existing_ats_job_by_id_or_url, upsert_ats_job
from app.job_ingestion.job_skill_extraction import has_sufficient_deterministic_metadata, has_sufficient_global_provider_metadata


def _job(**overrides):
    job = {
        "ats_type": "greenhouse", "ats_job_id": "gh-1", "title": "Engineer",
        "company_name": "Acme", "description": "Required Skills: Python",
        "skills": ["Python"], "skills_required": ["Python"],
        "experience_required": "5 years", "salary_range": "$100k-$120k",
        "employment_type": "Full-time", "structured_data": {},
    }
    job.update(overrides)
    return job


def _existing(**overrides):
    row = {"id": "existing-id", **_job()}
    row.update(overrides)
    return row


def test_existing_lookup_and_extraction_gate_use_provider_scoped_id():
    class Result:
        def first(self):
            return ("existing-id", None, "Required Skills: Python", None, ["Python"], ["Python"], "5 years", "$100k-$120k", "Full-time", {}, "greenhouse", "gh-1")

    class Session:
        def __init__(self): self.params = None
        async def execute(self, _statement, params): self.params = params; return Result()

    session = Session()
    existing = asyncio.run(find_existing_ats_job(session, "greenhouse", "gh-1"))

    assert session.params == {"ats_type": "greenhouse", "ats_job_id": "gh-1"}
    assert existing["id"] == "existing-id"
    assert existing["ats_type"] == "greenhouse"
    assert existing["ats_job_id"] == "gh-1"
    assert not scheduler._existing_ats_job_needs_extraction(_job(), existing, has_sufficient_deterministic_metadata)


def test_existing_incomplete_changed_and_pending_jobs_remain_extractable():
    existing = _existing()
    assert scheduler._existing_ats_job_needs_extraction(_job(), _existing(salary_range=None), has_sufficient_deterministic_metadata)
    assert scheduler._existing_ats_job_needs_extraction(_job(description="Updated requirements: Python and Docker"), existing, has_sufficient_deterministic_metadata)
    assert scheduler._existing_ats_job_needs_extraction(_job(), _existing(structured_data={"metadata_extraction_status": "pending"}), has_sufficient_deterministic_metadata)
    assert scheduler._existing_ats_job_needs_extraction(_job(), _existing(structured_data={"metadata_extraction_status": "pending_retry"}), has_sufficient_deterministic_metadata)


def test_description_change_gate_processes_a_shorter_jd_once_then_remembers_it():
    richer = _existing(
        description="A substantially richer stored description with all requirements.",
        structured_data={"metadata_extraction_status": "complete"},
    )
    shorter = _job(description="Short provider response")

    assert scheduler._existing_ats_job_needs_extraction(shorter, richer, has_sufficient_deterministic_metadata)
    persisted_shorter = _existing(
        description=richer["description"],
        structured_data={
            "metadata_extraction_status": "complete",
            "description_extraction_fingerprint": description_fingerprint(shorter["description"]),
        },
    )
    assert not scheduler._existing_ats_job_needs_extraction(shorter, persisted_shorter, has_sufficient_deterministic_metadata)
    assert scheduler._existing_ats_job_needs_extraction(
        _job(description="A different short response"), persisted_shorter, has_sufficient_deterministic_metadata
    )


def test_description_change_gate_still_processes_longer_and_equal_length_revisions():
    existing = _existing(description="original")
    assert scheduler._existing_ats_job_needs_extraction(
        _job(description="a longer replacement"), existing, has_sufficient_deterministic_metadata
    )
    assert scheduler._existing_ats_job_needs_extraction(
        _job(description="revised!"), existing, has_sufficient_deterministic_metadata
    )


def test_global_existing_job_requires_complete_status_and_both_skill_arrays():
    incoming = _job(ats_type="fantastic", ats_job_id="f-1")
    complete = _existing(
        ats_type="fantastic", ats_job_id="f-1",
        structured_data={"metadata_extraction_status": "complete"},
    )
    assert not scheduler._existing_ats_job_needs_extraction(
        incoming, complete, has_sufficient_deterministic_metadata, has_sufficient_global_provider_metadata
    )
    assert scheduler._existing_ats_job_needs_extraction(
        incoming, _existing(**{**complete, "skills_required": []}),
        has_sufficient_deterministic_metadata, has_sufficient_global_provider_metadata,
    )


def test_global_id_or_url_lookup_returns_metadata_and_collision_identity():
    class Result:
        def first(self):
            return ("existing-id", "https://jobs.example/f-1", "Build APIs", None, ["Python"], ["Python"], "5 years", "$100k-$120k", "Full-time", {"metadata_extraction_status": "complete"}, "fantastic", "f-1")

    class Session:
        async def execute(self, _statement, _params): return Result()

    existing = asyncio.run(find_existing_ats_job_by_id_or_url(Session(), "fantastic", "f-1", "https://jobs.example/f-1"))
    assert existing == {
        "id": "existing-id", "job_url": "https://jobs.example/f-1", "description": "Build APIs",
        "requirements": None, "skills": ["Python"], "skills_required": ["Python"],
        "experience_required": "5 years", "salary_range": "$100k-$120k", "employment_type": "Full-time",
        "structured_data": {"metadata_extraction_status": "complete"}, "ats_type": "fantastic", "ats_job_id": "f-1",
    }


def test_completed_existing_job_with_legitimately_absent_salary_skips_when_unchanged():
    existing = _existing(
        salary_range=None, experience_required=None,
        structured_data={"metadata_extraction_status": "complete"},
    )
    incoming = _job(salary_range=None, experience_required=None)

    assert not scheduler._existing_ats_job_needs_extraction(incoming, existing, has_sufficient_deterministic_metadata)


def test_existing_undisclosed_markers_make_unchanged_record_complete_without_status():
    existing = _existing(
        salary_range=None, experience_required=None,
        structured_data={"explicitly_undisclosed": {"salary_range": True, "experience_required": True}},
    )
    incoming = _job(salary_range=None, experience_required=None)

    assert not scheduler._existing_ats_job_needs_extraction(incoming, existing, has_sufficient_deterministic_metadata)
    assert scheduler._existing_ats_job_needs_extraction(
        incoming,
        _existing(**{**existing, "structured_data": {"metadata_extraction_status": "pending_retry", "explicitly_undisclosed": {"salary_range": True, "experience_required": True}}}),
        has_sufficient_deterministic_metadata,
    )


def test_malformed_persisted_undisclosed_markers_do_not_make_record_complete():
    existing = _existing(
        salary_range=None, experience_required=None,
        structured_data={"explicitly_undisclosed": {"salary_range": "true", "experience_required": True}},
    )

    assert scheduler._existing_ats_job_needs_extraction(
        _job(salary_range=None, experience_required=None), existing, has_sufficient_deterministic_metadata
    )


def test_upsert_reuses_pre_extraction_lookup_without_second_select(monkeypatch):
    class Result:
        def scalar_one(self): raise AssertionError("existing rows must not insert")

    class Session:
        def __init__(self): self.sql = []; self.commits = 0
        async def execute(self, statement, _params=None): self.sql.append(str(statement)); return Result()
        async def commit(self): self.commits += 1

    session = Session()
    job = _job(job_url=None)
    assert asyncio.run(upsert_ats_job(session, job, existing_job=_existing(job_url=None))) == "existing-id"
    assert not any("SELECT id, job_url, description" in sql for sql in session.sql)
    assert session.commits == 1


def test_normal_stable_id_lookup_is_reused_without_a_second_query():
    class Result:
        def scalar_one(self): raise AssertionError("existing rows must not insert")

    class Session:
        def __init__(self): self.calls = 0
        async def execute(self, _statement, _params=None): self.calls += 1; return Result()
        async def commit(self): pass

    session = Session()
    existing = _existing(job_url=None)
    assert asyncio.run(upsert_ats_job(session, _job(job_url=None), existing_job=existing)) == "existing-id"
    assert session.calls == 1  # UPDATE only; no verifying SELECT is needed for a known match.


def test_upsert_reuses_a_not_found_lookup_without_second_select(monkeypatch):
    class Result:
        def scalar_one(self): return "new-id"

    class Session:
        def __init__(self): self.sql = []
        async def execute(self, statement, _params=None):
            self.sql.append(str(statement))
            return Result()
        async def commit(self): pass

    async def unexpected_lookup(*_args):
        raise AssertionError("the scheduler's not-found lookup must be reused")

    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.find_existing_ats_job", unexpected_lookup)
    job = _job(ats_type="fantastic", ats_job_id="f-1", job_url=None)
    assert asyncio.run(upsert_ats_job(Session(), job, existing_job=None)) == "new-id"


def test_upsert_does_not_reuse_a_url_collision_row(monkeypatch):
    requested = []

    class Result:
        def scalar_one(self): return "new-id"

    class Session:
        async def execute(self, _statement, _params=None): return Result()
        async def commit(self): pass

    async def stable_lookup(_db, ats_type, ats_job_id):
        requested.append((ats_type, ats_job_id))
        return None

    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.find_existing_ats_job", stable_lookup)
    collision = _existing(ats_type="theirstack", ats_job_id="old-id")
    assert asyncio.run(upsert_ats_job(Session(), _job(ats_type="theirstack", ats_job_id="new-id"), existing_job=collision)) == "new-id"
    assert requested == [("theirstack", "new-id")]


def test_legacy_existing_projection_is_verified_before_it_is_reused(monkeypatch):
    requested = []

    class Result:
        def scalar_one(self): raise AssertionError("verified existing row must not insert")

    class Session:
        async def execute(self, _statement, _params=None): return Result()
        async def commit(self): pass

    legacy = _existing()
    legacy.pop("ats_type")
    legacy.pop("ats_job_id")

    async def stable_lookup(_db, ats_type, ats_job_id):
        requested.append((ats_type, ats_job_id))
        return _existing()

    monkeypatch.setattr("app.job_ingestion.job_ingestion_service.find_existing_ats_job", stable_lookup)
    assert asyncio.run(upsert_ats_job(Session(), _job(), existing_job=legacy)) == "existing-id"
    assert requested == [("greenhouse", "gh-1")]
