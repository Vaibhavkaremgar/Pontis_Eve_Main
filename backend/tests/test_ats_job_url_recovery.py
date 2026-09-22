import asyncio

from app.job_ingestion import backfill_missing_job_urls as backfill
from app.job_ingestion.normalize import normalize_ashby, normalize_lever


def test_normalize_ashby_recovers_only_valid_extracted_job_url():
    normalized = normalize_ashby(
        {"id": "a-1", "title": "Engineer", "jobUrl": "not-a-url", "applyUrl": " https://jobs.ashbyhq.com/acme/a-1 "},
        "Acme",
    )

    assert normalized["job_url"] == "https://jobs.ashbyhq.com/acme/a-1"


def test_normalize_lever_recovers_only_valid_extracted_job_url():
    normalized = normalize_lever(
        {
            "id": "l-1",
            "text": "Engineer",
            "urls": {"show": "javascript:alert(1)", "apply": "https://jobs.lever.co/acme/l-1/apply"},
        },
        "Acme",
    )

    assert normalized["job_url"] == "https://jobs.lever.co/acme/l-1/apply"


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self.rows


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement, params):
        return _Result([
            {"id": "a-1", "company_name": "Ashby Co", "ats_type": "ashby", "ats_job_id": "a-1", "job_url": None, "identifier": "ashby-co", "title": "Engineer", "location": "Remote", "department": None, "employment_type": None, "structured_data": {"applyUrl": "https://jobs.ashbyhq.com/ashby-co/original"}},
            {"id": "a-2", "company_name": "Ashby Co", "ats_type": "ashby", "ats_job_id": "gone-a", "job_url": None, "identifier": "ashby-co", "title": "Designer", "location": "New York", "department": None, "employment_type": None, "structured_data": {}},
            {"id": "l-1", "company_name": "Lever Co", "ats_type": "lever", "ats_job_id": "gone-l", "job_url": None, "identifier": "lever-co", "title": "Engineer", "location": "Remote", "department": "Platform", "employment_type": None, "structured_data": {}},
            {"id": "l-2", "company_name": "Lever Co", "ats_type": "lever", "ats_job_id": "amb-l", "job_url": None, "identifier": "lever-co", "title": "Engineer", "location": "Remote", "department": None, "employment_type": None, "structured_data": {}},
        ])


def test_dry_run_reports_per_ats_recovery_counts(monkeypatch, capsys):
    class Collector:
        def collect_company_jobs(self, ats_type, identifier, company_name):
            if ats_type == "ashby":
                return [
                    {"ats_job_id": "new-a", "title": "Designer", "location": "New York", "job_url": "https://jobs.ashbyhq.com/ashby-co/new-a"},
                ]
            return [
                {"ats_job_id": "new-l", "title": "Engineer", "location": "Remote", "department": "Platform", "job_url": "https://jobs.lever.co/lever-co/new-l"},
                {"ats_job_id": "one", "title": "Engineer", "location": "Remote", "job_url": "https://jobs.lever.co/lever-co/one"},
                {"ats_job_id": "two", "title": "Engineer", "location": "Remote", "job_url": "https://jobs.lever.co/lever-co/two"},
            ]

    monkeypatch.setattr(backfill, "_get_session_local", lambda: _Session)
    monkeypatch.setattr("app.job_ingestion.collect_jobs.JobCollector", Collector)

    asyncio.run(backfill.main())

    output = capsys.readouterr().out
    assert "Ashby missing: 2" in output
    assert "Ashby exact URL recovered from stored data: 1" in output
    assert "Ashby URL recovered by safe re-resolution: 1" in output
    assert "Lever missing: 2" in output
    assert "Lever URL recovered by safe re-resolution: 1" in output
    assert "Lever ambiguous matches: 1" in output
    assert "https://jobs.ashbyhq.com/ashby-co/original" in output
    assert "https://jobs.lever.co/lever-co/new-l" in output


def test_reresolution_requires_more_than_a_title_and_ats_hosted_job_url():
    row = {"ats_type": "lever", "ats_job_id": "old", "title": "Engineer", "location": None, "department": None, "employment_type": None}
    jobs = [{"ats_job_id": "new", "title": "Engineer", "location": "Remote", "job_url": "https://jobs.lever.co/acme/new"}]
    assert backfill._resolve_from_board(row, jobs) == ("unrecoverable", None)


def test_reresolution_rejects_another_ats_url():
    row = {"ats_type": "ashby", "ats_job_id": "old", "title": "Engineer", "location": "Remote", "department": None, "employment_type": None}
    jobs = [{"ats_job_id": "new", "title": "Engineer", "location": "Remote", "job_url": "https://jobs.lever.co/acme/new"}]
    assert backfill._resolve_from_board(row, jobs) == ("unrecoverable", None)
