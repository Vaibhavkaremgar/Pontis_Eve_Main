import asyncio

from app.job_ingestion import apply_approved_ashby_url_recoveries as apply


def test_verified_row_requires_one_stable_identity_match():
    current = {"title": "Engineer", "location": "Remote", "department": "Platform", "employment_type": None}
    matching = {"id": "old", "ats_job_id": "old-ats", **current}
    assert apply._verified_row([matching], current) == matching

    # A title-only or duplicate match must never be written.
    title_only = {"id": "weak", "ats_job_id": "weak-ats", "title": "Engineer", "location": None, "department": None, "employment_type": None}
    try:
        apply._verified_row([title_only], current)
        assert False, "title-only match was accepted"
    except RuntimeError:
        pass
    try:
        apply._verified_row([matching, {"id": "two", "ats_job_id": "two-ats", **current}], current)
        assert False, "ambiguous match was accepted"
    except RuntimeError:
        pass


class _Result:
    def __init__(self, rows=None): self.rows = rows or []
    def mappings(self): return self
    def fetchall(self): return self.rows
    def scalar_one_or_none(self): return self.rows[0]["id"] if self.rows else None


class _Session:
    def __init__(self):
        self.rows = [
            {"id": "eleven-old", "company_name": "ElevenLabs", "ats_type": "ashby", "ats_job_id": "eleven-old-ats", "title": "Engineer", "location": "Remote", "department": "Platform", "employment_type": None, "job_url": None},
            {"id": "open-one-old", "company_name": "OpenAI", "ats_type": "ashby", "ats_job_id": "open-one-old-ats", "title": "Researcher", "location": "San Francisco", "department": None, "employment_type": None, "job_url": None},
            {"id": "open-two-old", "company_name": "OpenAI", "ats_type": "ashby", "ats_job_id": "open-two-old-ats", "title": "Engineer", "location": "Remote", "department": None, "employment_type": None, "job_url": None},
            {"id": "keep", "company_name": "OpenAI", "ats_type": "ashby", "ats_job_id": "keep-ats", "title": "Other", "location": "Remote", "department": None, "employment_type": None, "job_url": None},
            {"id": "nonempty", "company_name": "OpenAI", "ats_type": "ashby", "ats_job_id": "existing", "title": "Engineer", "location": "Remote", "department": None, "employment_type": None, "job_url": "https://jobs.ashbyhq.com/OpenAI/existing"},
        ]
        self.updated_fields = []
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    async def execute(self, statement, params=None):
        sql, params = str(statement).lower(), params or {}
        if "select id, company_name" in sql and "nullif" in sql:
            return _Result([r.copy() for r in self.rows if r["company_name"].lower() == params["company_name"].lower() and not r["job_url"]])
        if "update job_descriptions" in sql:
            row = next(r for r in self.rows if r["id"] == params["id"])
            assert row["job_url"] is None
            row["job_url"] = params["job_url"]
            self.updated_fields.append(set(params))
            return _Result([row])
        if "where id = any" in sql:
            return _Result([r.copy() for r in self.rows if r["id"] in params["ids"]])
        raise AssertionError(sql)
    async def commit(self): pass


class _Factory:
    def __init__(self, session): self.session = session
    def __call__(self): return self.session


def test_apply_writes_only_the_three_approved_empty_url_rows():
    session = _Session()
    class Collector:
        def collect_company_jobs(self, _ats, identifier, _company):
            jobs = {
                "elevenlabs": [{"ats_job_id": "new-eleven", "title": "Engineer", "location": "Remote", "department": "Platform", "job_url": apply.APPROVED_URLS[0][2]}],
                "OpenAI": [
                    {"ats_job_id": "new-one", "title": "Researcher", "location": "San Francisco", "job_url": apply.APPROVED_URLS[1][2]},
                    {"ats_job_id": "new-two", "title": "Engineer", "location": "Remote", "job_url": apply.APPROVED_URLS[2][2]},
                ],
            }
            return jobs[identifier]
    updated = asyncio.run(apply.apply_approved_recoveries(_Factory(session), Collector()))
    assert updated == ["eleven-old", "open-one-old", "open-two-old"]
    assert session.rows[3]["job_url"] is None
    assert session.rows[4]["job_url"] == "https://jobs.ashbyhq.com/OpenAI/existing"
    assert all(fields == {"id", "job_url"} for fields in session.updated_fields)
