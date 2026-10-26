import pytest

from app.job_ingestion import collect_jobs, scheduler
from app.job_ingestion.feature_flags import provider_enabled


@pytest.mark.parametrize("provider", ["greenhouse", "lever", "ashby", "workable"])
def test_ats_flags_default_disabled_and_only_true_enables(monkeypatch, provider):
    monkeypatch.delenv(f"{provider.upper()}_ENABLED", raising=False)
    assert provider_enabled(provider) is False
    monkeypatch.setenv(f"{provider.upper()}_ENABLED", "not-a-boolean")
    assert provider_enabled(provider) is False
    monkeypatch.setenv(f"{provider.upper()}_ENABLED", "true")
    assert provider_enabled(provider) is True


@pytest.mark.parametrize("provider", ["greenhouse", "lever", "ashby", "workable"])
def test_disabled_collector_returns_before_provider_fetch(monkeypatch, provider, caplog):
    caplog.set_level("INFO")
    monkeypatch.setenv(f"{provider.upper()}_ENABLED", "false")
    connector = getattr(collect_jobs, provider)
    monkeypatch.setattr(connector, "collect_jobs", lambda _: pytest.fail("provider was fetched"))

    assert collect_jobs.JobCollector().collect_company_jobs(provider, "board", "Acme") == []
    assert provider in caplog.text


@pytest.mark.parametrize("provider", ["greenhouse", "lever", "ashby", "workable"])
def test_enabled_collector_keeps_normal_fetch_and_normalize_path(monkeypatch, provider):
    monkeypatch.setenv(f"{provider.upper()}_ENABLED", "true")
    connector = getattr(collect_jobs, provider)
    monkeypatch.setattr(connector, "collect_jobs", lambda _: [{"id": f"{provider}-1"}])
    monkeypatch.setattr(
        collect_jobs,
        f"normalize_{provider}",
        lambda job, company: {"ats_type": provider, "company_name": company},
    )

    assert collect_jobs.JobCollector().collect_company_jobs(provider, "board", "Acme") == [
        {"ats_type": provider, "company_name": "Acme"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["greenhouse", "lever", "ashby", "workable"])
async def test_disabled_provider_reconciliation_does_not_touch_database(monkeypatch, provider):
    monkeypatch.setenv(f"{provider.upper()}_ENABLED", "false")

    class Session:
        async def execute(self, *args, **kwargs):
            pytest.fail("disabled provider reconciliation queried or modified the database")

    assert await scheduler.reconcile_missing_ats_jobs(
        Session(), company_registry_id="company", ats_type=provider, current_ats_ids=set()
    ) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["greenhouse", "lever", "ashby"])
async def test_disabled_provider_pending_metadata_is_not_extracted(monkeypatch, provider):
    monkeypatch.setenv(f"{provider.upper()}_ENABLED", "false")
    row = {"id": "job-1", "ats_type": provider, "structured_data": {"metadata_extraction_status": "pending_retry"}}

    class Result:
        def mappings(self):
            return self

        def all(self):
            return [row]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *args, **kwargs):
            return Result()

    monkeypatch.setattr(scheduler, "_get_session_local", lambda: lambda: Session())
    monkeypatch.setattr(
        "app.job_ingestion.job_skill_extraction.extract_missing_job_skills",
        lambda job: pytest.fail("disabled provider was sent to extraction"),
    )

    assert await scheduler.retry_pending_metadata_extraction() == 0


def test_unknown_provider_is_always_disabled(monkeypatch):
    monkeypatch.setenv("UNKNOWN_ENABLED", "true")
    assert provider_enabled("unknown") is False
