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


def _auth(candidate_id="candidate-1"):
    return f"Bearer {server._issue_candidate_session_token(candidate_id)}"


def _stub_candidate(monkeypatch, resume_file_path=None):
    async def get_candidate(candidate_id):
        assert candidate_id == "candidate-1"
        return {"id": candidate_id, "resume_file_path": resume_file_path}

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)


def test_resume_view_serves_the_persisted_uploaded_pdf_not_legacy_path(tmp_path, monkeypatch):
    persisted_pdf = tmp_path / "documents" / "candidate-1" / "resume" / "uploaded.pdf"
    persisted_pdf.parent.mkdir(parents=True)
    persisted_pdf.write_bytes(b"%PDF-persisted-resume")
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path / "documents")
    _stub_candidate(monkeypatch, str(persisted_pdf))
    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession({
        "SELECT source_filename, source_path FROM internal_candidate_resumes": FakeResult([("resume.pdf", str(persisted_pdf))]),
    }))

    response = asyncio.run(server.view_resume("candidate-1", authorization=_auth()))

    assert response.path == str(persisted_pdf)
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="resume.pdf"'


def test_certificate_view_serves_the_uploaded_file(tmp_path, monkeypatch):
    certificate = tmp_path / "documents" / "candidate-1" / "certificates" / "certificate.pdf"
    certificate.parent.mkdir(parents=True)
    certificate.write_bytes(b"%PDF-persisted-certificate")
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path / "documents")
    _stub_candidate(monkeypatch)
    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession({
        "SELECT file_name, file_path FROM candidate_certificates": FakeResult([("certificate.pdf", str(certificate))]),
    }))

    response = asyncio.run(server.view_certificate("candidate-1", "cert-1", authorization=_auth()))

    assert response.path == str(certificate)
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="certificate.pdf"'


@pytest.mark.parametrize(
    ("endpoint", "responses", "resume_path"),
    [
        (
            lambda: server.view_resume("candidate-1", authorization=_auth()),
            {"SELECT source_filename, source_path FROM internal_candidate_resumes": FakeResult([("resume.pdf", "missing-resume.pdf")])},
            "missing-resume.pdf",
        ),
        (
            lambda: server.view_certificate("candidate-1", "cert-1", authorization=_auth()),
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
    assert "unavailable" in exc.value.detail.lower() or "not available" in exc.value.detail.lower()


def test_document_listing_metadata_is_unchanged(monkeypatch):
    _stub_candidate(monkeypatch)
    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession({
        "SELECT source_filename, resume_fingerprint": FakeResult([("resume.pdf", "resume-sha")]),
        "SELECT id, file_name FROM candidate_certificates": FakeResult([("cert-1", "certificate.pdf")]),
    }))

    documents = asyncio.run(server.get_candidate_documents("candidate-1", authorization=_auth()))

    assert documents == {
        "resume": {"filename": "resume.pdf", "fingerprint": "resume-sha"},
        "certificates": [{"id": "cert-1", "filename": "certificate.pdf"}],
    }


def test_registered_resume_uses_current_persistent_volume_when_old_absolute_path_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path)
    saved = tmp_path / "candidate-1" / "resume" / "upload-id.pdf"
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b"%PDF registered resume")
    _stub_candidate(monkeypatch, "C:/former-container/documents/candidate-1/resume/upload-id.pdf")
    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession({
        "SELECT source_filename, source_path FROM internal_candidate_resumes": FakeResult([
            ("my-resume.pdf", "C:/former-container/documents/candidate-1/resume/upload-id.pdf")
        ]),
    }))

    response = asyncio.run(server.view_resume("candidate-1", authorization=_auth()))
    assert response.path == str(saved)


def test_multiple_registered_certificates_each_resolve_to_own_persistent_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path)
    cert_dir = tmp_path / "candidate-1" / "certificates"
    cert_dir.mkdir(parents=True)
    first, second = cert_dir / "a.pdf", cert_dir / "b.docx"
    first.write_bytes(b"%PDF first")
    second.write_bytes(b"DOCX second")
    _stub_candidate(monkeypatch)

    for cert_id, name, old_path, expected in [
        ("cert-a", "first.pdf", "/old/machine/a.pdf", first),
        ("cert-b", "second.docx", "/old/machine/b.docx", second),
    ]:
        monkeypatch.setattr(server, "SessionLocal", lambda name=name, old_path=old_path: FakeSession({
            "SELECT file_name, file_path FROM candidate_certificates": FakeResult([(name, old_path)]),
        }))
        response = asyncio.run(server.view_certificate("candidate-1", cert_id, authorization=_auth()))
        assert response.path == str(expected)


def test_newly_uploaded_certificate_view_uses_stored_key(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path)
    uploaded = tmp_path / "candidate-1" / "certificates" / "new-upload.jpg"
    uploaded.parent.mkdir(parents=True)
    uploaded.write_bytes(b"image")
    _stub_candidate(monkeypatch)
    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession({
        "SELECT file_name, file_path FROM candidate_certificates": FakeResult([("credential.jpg", str(uploaded))]),
    }))
    response = asyncio.run(server.view_certificate("candidate-1", "cert-new", authorization=_auth()))
    assert response.path == str(uploaded)
    assert response.media_type == "image/jpeg"


def test_candidate_cannot_view_another_candidates_document(monkeypatch):
    _stub_candidate(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(server.view_resume("candidate-1", authorization=_auth("candidate-2")))
    assert exc.value.status_code == 403
