"""Fix My Resume application-PDF persistence regression coverage."""
import asyncio
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server  # noqa: E402


class _Session:
    def __init__(self, state, view_row=None, documents=False):
        self.state = state
        self.view_row = view_row
        self.documents = documents

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.state.setdefault("sql", []).append(sql)
        self.state.setdefault("params", []).append(params or {})
        if self.view_row is not None and "SELECT file_name, file_path, recommendation_id, company_name" in sql:
            return _Result([self.view_row])
        if self.documents:
            if "SELECT source_filename, resume_fingerprint" in sql:
                return _Result()
            if "SELECT id, file_name FROM candidate_certificates" in sql:
                return _Result()
            if "SELECT id, file_name, company_name, recommendation_id" in sql:
                return _Result([("application-1", "pontis_sai_vignesh.pdf", "Pontis", "rec-pontis")])
        return _Result()

    async def commit(self):
        self.state["committed"] = True


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def test_fix_my_resume_download_persists_exact_pdf_with_job_company_and_is_viewable(tmp_path, monkeypatch):
    """The download artifact is stored and exposed as the same job-scoped PDF."""
    candidate_id, recommendation_id = "candidate-1", "rec-pontis"
    source = tmp_path / candidate_id / "updated_resume.pdf"
    source.parent.mkdir(parents=True)
    generated_pdf = b"%PDF-1.4 exact generated Fix My Resume bytes"
    source.write_bytes(generated_pdf)
    state = {}

    async def candidate(_candidate_id):
        return {
            "id": candidate_id,
            "name": "Sai Vignesh",
            "raw_data": {"updated_resume_file_path": str(source)},
        }

    async def selected_job(_candidate_id, _recommendation_id):
        # This is the real job/recommendation field, not a frontend display alias.
        return {"job_id": "job-pontis", "company_name": "Pontis"}

    monkeypatch.setattr(server, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(server, "_updated_resume_pdf_path", lambda _cid: source)
    monkeypatch.setattr(server, "_get_candidate_row", candidate)
    monkeypatch.setattr(server, "_get_job_match_improvement_row", selected_job)
    monkeypatch.setattr(server, "SessionLocal", lambda: _Session(state))

    response = asyncio.run(server.download_application_resume(candidate_id, recommendation_id))

    persisted = tmp_path / candidate_id / "application_resumes" / recommendation_id / "pontis_sai_vignesh.pdf"
    assert response.path == str(persisted)
    assert persisted.read_bytes() == generated_pdf
    assert response.headers["content-disposition"] == 'attachment; filename="pontis_sai_vignesh.pdf"'
    assert state["committed"] is True
    upsert_params = state["params"][-1]
    assert upsert_params == {
        "cid": candidate_id, "rid": recommendation_id, "jid": "job-pontis",
        "company": "Pontis", "filename": "pontis_sai_vignesh.pdf",
        "path": f"{recommendation_id}/pontis_sai_vignesh.pdf",
    }
    assert any("ON CONFLICT (candidate_id, recommendation_id) DO UPDATE" in sql for sql in state["sql"])

    # Reproduce production: persisted download -> Documents -> authenticated
    # application-resume View. The View response is the exact persisted bytes.
    monkeypatch.setattr(
        server, "SessionLocal",
        lambda: _Session({}, ("pontis_sai_vignesh.pdf", f"{recommendation_id}/pontis_sai_vignesh.pdf", recommendation_id), documents=True),
    )
    token = server._issue_candidate_session_token(candidate_id)
    client = TestClient(server.app)
    documents = client.get(
        f"/api/candidate/{candidate_id}/documents",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert documents.status_code == 200
    assert documents.json()["application_resumes"] == [{
        "id": "application-1", "filename": "pontis_sai_vignesh.pdf",
        "company": "Pontis", "recommendation_id": recommendation_id,
    }]
    view = client.get(
        f"/api/candidate/{candidate_id}/application-resumes/application-1/view",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert view.status_code == 200
    assert view.content == generated_pdf
    assert view.headers["content-type"] == "application/pdf"
    assert view.headers["content-disposition"] == 'inline; filename="pontis_sai_vignesh.pdf"'


def test_application_resume_storage_key_isolated_per_recommendation(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path)
    first = server._application_resume_path("candidate-1", "rec-pontis", "pontis_sai_vignesh.pdf")
    second = server._application_resume_path("candidate-1", "rec-other-pontis-role", "pontis_sai_vignesh.pdf")

    assert first != second
    assert first.as_posix().endswith("rec-pontis/pontis_sai_vignesh.pdf")
    assert second.as_posix().endswith("rec-other-pontis-role/pontis_sai_vignesh.pdf")


def test_application_resume_view_recovers_legacy_stale_db_path_by_recommendation(tmp_path, monkeypatch):
    candidate_id, recommendation_id = "candidate-1", "rec-pontis"
    legacy_pdf = tmp_path / candidate_id / "application_resumes" / f"{recommendation_id}.pdf"
    legacy_pdf.parent.mkdir(parents=True)
    legacy_pdf.write_bytes(b"%PDF legacy application")
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path)

    async def candidate(_candidate_id):
        return {"id": candidate_id}

    monkeypatch.setattr(server, "_get_candidate_row", candidate)
    monkeypatch.setattr(server, "SessionLocal", lambda: _Session(
        {}, ("pontis_sai_vignesh.pdf", r"C:\old\downloads\pontis_sai_vignesh.pdf", recommendation_id),
    ))
    response = asyncio.run(server.view_application_resume(
        candidate_id, "application-1", authorization=f"Bearer {server._issue_candidate_session_token(candidate_id)}",
    ))
    assert response.path == str(legacy_pdf)


def test_application_resume_view_recovers_stale_db_path_to_current_canonical_volume(tmp_path, monkeypatch):
    """A DB path from a former container must resolve under today's DOCS_DIR."""
    candidate_id, recommendation_id = "candidate-1", "rec-pontis"
    filename = "pontis_sai_vignesh.pdf"
    canonical_pdf = tmp_path / candidate_id / "application_resumes" / recommendation_id / filename
    canonical_pdf.parent.mkdir(parents=True)
    canonical_pdf.write_bytes(b"%PDF canonical application")
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path)

    async def candidate(_candidate_id):
        return {"id": candidate_id}

    monkeypatch.setattr(server, "_get_candidate_row", candidate)
    monkeypatch.setattr(server, "SessionLocal", lambda: _Session(
        {}, (filename, "/former-railway-volume/downloads/pontis.pdf", recommendation_id, "Pontis"),
    ))
    response = asyncio.run(server.view_application_resume(
        candidate_id, "application-1", authorization=f"Bearer {server._issue_candidate_session_token(candidate_id)}",
    ))

    assert response.path == str(canonical_pdf)
