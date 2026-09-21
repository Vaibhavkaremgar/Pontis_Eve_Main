"""Regression coverage for Documents-tab file viewing."""

import asyncio
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server  # noqa: E402


class FakeResult:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeSession:
    def __init__(self, responses):
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement, params=None):
        sql = str(statement)
        for marker, result in self.responses.items():
            if marker in sql:
                return result
        raise AssertionError(f"Unexpected SQL: {sql}")


def _stub_candidate(monkeypatch, resume_file_path=None):
    async def get_candidate(candidate_id):
        assert candidate_id == "candidate-1"
        return {"id": candidate_id, "resume_file_path": resume_file_path}

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)


def test_resume_view_serves_the_persisted_uploaded_pdf_not_legacy_path(tmp_path, monkeypatch):
    persisted_pdf = tmp_path / "documents" / "candidate-1" / "resume" / "uploaded.pdf"
    persisted_pdf.parent.mkdir(parents=True)
    persisted_pdf.write_bytes(b"%PDF-persisted-resume")
    _stub_candidate(monkeypatch, str(persisted_pdf))
    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession({
        "SELECT source_filename FROM internal_candidate_resumes": FakeResult([("resume.pdf",)]),
    }))

    response = asyncio.run(server.view_resume("candidate-1"))

    assert response.path == str(persisted_pdf)
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="resume.pdf"'


def test_certificate_view_serves_the_uploaded_file(tmp_path, monkeypatch):
    certificate = tmp_path / "documents" / "candidate-1" / "certificates" / "certificate.pdf"
    certificate.parent.mkdir(parents=True)
    certificate.write_bytes(b"%PDF-persisted-certificate")
    _stub_candidate(monkeypatch)
    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession({
        "SELECT file_name, file_path FROM candidate_certificates": FakeResult([("certificate.pdf", str(certificate))]),
    }))

    response = asyncio.run(server.view_certificate("candidate-1", "cert-1"))

    assert response.path == str(certificate)
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="certificate.pdf"'


@pytest.mark.parametrize(
    ("endpoint", "responses", "resume_path"),
    [
        (
            lambda: server.view_resume("candidate-1"),
            {"SELECT source_filename FROM internal_candidate_resumes": FakeResult([("resume.pdf",)])},
            "missing-resume.pdf",
        ),
        (
            lambda: server.view_certificate("candidate-1", "cert-1"),
            {"SELECT file_name, file_path FROM candidate_certificates": FakeResult([("certificate.pdf", "missing-certificate.pdf")])},
            None,
        ),
    ],
)
def test_missing_document_file_returns_404(endpoint, responses, resume_path, monkeypatch):
    _stub_candidate(monkeypatch, resume_path)
    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession(responses))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint())

    assert exc.value.status_code == 404
    assert "not found" in exc.value.detail.lower() or "not available" in exc.value.detail.lower()


def test_document_listing_metadata_is_unchanged(monkeypatch):
    _stub_candidate(monkeypatch)
    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession({
        "SELECT source_filename, resume_fingerprint": FakeResult([("resume.pdf", "resume-sha")]),
        "SELECT id, file_name FROM candidate_certificates": FakeResult([("cert-1", "certificate.pdf")]),
    }))

    documents = asyncio.run(server.get_candidate_documents("candidate-1"))

    assert documents == {
        "resume": {"filename": "resume.pdf", "fingerprint": "resume-sha"},
        "certificates": [{"id": "cert-1", "filename": "certificate.pdf"}],
    }
