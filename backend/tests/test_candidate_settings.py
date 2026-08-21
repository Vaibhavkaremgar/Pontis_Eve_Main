import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


def test_candidate_session_token_roundtrip():
    token = server._issue_candidate_session_token("cand-123")
    server._verify_candidate_session_token(token, "cand-123")

    with pytest.raises(HTTPException) as exc:
        server._verify_candidate_session_token(token, "cand-999")
    assert exc.value.status_code == 403


def test_candidate_help_requires_auth():
    with pytest.raises(HTTPException) as exc:
        server._verify_candidate_session_token("", "cand-123")
    assert exc.value.status_code == 401


def test_delete_candidate_account_removes_candidate_rows_and_storage(tmp_path, monkeypatch):
    deleted_tables = []

    class FakeResult:
        def __init__(self, rows=None):
            self._rows = rows or []

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class FakeSession:
        def __init__(self):
            self.executed = []
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, stmt, params=None):
            sql = str(stmt)
            self.executed.append((sql, params or {}))
            if "SELECT file_path FROM candidate_certificates" in sql:
                return FakeResult([(str(cert_path),)])
            if "SELECT source_path FROM internal_candidate_resumes" in sql:
                return FakeResult([(str(resume_path),)])
            if sql.startswith("DELETE FROM"):
                deleted_tables.append(sql.split()[2])
            return FakeResult([])

        async def commit(self):
            self.committed = True

    candidate_id = "cand-123"
    candidate_dir = tmp_path / candidate_id
    photo_dir = candidate_dir / "photo"
    resume_dir = candidate_dir / "resume"
    cert_dir = candidate_dir / "certificates"
    photo_dir.mkdir(parents=True, exist_ok=True)
    resume_dir.mkdir(parents=True, exist_ok=True)
    cert_dir.mkdir(parents=True, exist_ok=True)

    photo_path = photo_dir / "avatar.png"
    resume_path = resume_dir / "resume.pdf"
    cert_path = cert_dir / "cert.pdf"
    photo_path.write_text("photo", encoding="utf-8")
    resume_path.write_text("resume", encoding="utf-8")
    cert_path.write_text("cert", encoding="utf-8")

    fake_session = FakeSession()

    async def fake_get_candidate_row(cid):
        return {
            "id": cid,
            "raw_data": json.dumps({"photo_file_path": str(photo_path)}),
        }

    monkeypatch.setattr(server, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)
    monkeypatch.setattr(server, "SessionLocal", lambda: fake_session)

    token = server._issue_candidate_session_token(candidate_id)
    result = asyncio.run(
        server.delete_candidate_account(
            candidate_id,
            authorization=f"Bearer {token}",
        )
    )

    assert result == {"status": "deleted", "candidate_id": candidate_id}
    assert fake_session.committed is True
    assert "candidate_certificates" in deleted_tables
    assert "internal_candidate_resumes" in deleted_tables
    assert "candidates" in deleted_tables
    assert not photo_path.exists()
    assert not resume_path.exists()
    assert not cert_path.exists()
    assert not candidate_dir.exists()


def test_candidate_help_sends_email_with_resend(monkeypatch):
    captured = {}

    async def fake_get_candidate_row(cid):
        return {"id": cid, "name": "Jane Doe", "email": "jane@example.com"}

    class FakeEmails:
        @staticmethod
        def send(params):
            captured["params"] = params
            return {"id": "email-123"}

    monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)
    monkeypatch.setattr(server, "resend", type("FakeResend", (), {"api_key": None, "Emails": FakeEmails})())
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "support@pontis.one")

    token = server._issue_candidate_session_token("cand-123")
    result = asyncio.run(
        server.candidate_help(
            "cand-123",
            server.CandidateHelpRequest(
                candidate_id="cand-123",
                subject="Need help with matching",
                message="I cannot see job matches in my dashboard.",
            ),
            authorization=f"Bearer {token}",
        )
    )

    assert result == {"status": "sent"}
    assert captured["params"]["from"] == "support@pontis.one"
    assert captured["params"]["to"] == ["info@pontis.one"]
    assert captured["params"]["subject"] == "Need help with matching"
    assert captured["params"]["reply_to"] == "jane@example.com"
    assert "Candidate ID: cand-123" in captured["params"]["text"]
    assert "Candidate Name: Jane Doe" in captured["params"]["text"]
    assert "I cannot see job matches in my dashboard." in captured["params"]["text"]
    assert server.resend.api_key == "re_test_key"


def test_candidate_help_returns_502_when_resend_fails(monkeypatch):
    async def fake_get_candidate_row(cid):
        return {"id": cid, "name": "Jane Doe", "email": "jane@example.com"}

    class FakeEmails:
        @staticmethod
        def send(params):
            raise RuntimeError("resend unavailable")

    monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)
    monkeypatch.setattr(server, "resend", type("FakeResend", (), {"api_key": None, "Emails": FakeEmails})())
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "support@pontis.one")

    token = server._issue_candidate_session_token("cand-123")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            server.candidate_help(
                "cand-123",
                server.CandidateHelpRequest(
                    candidate_id="cand-123",
                    subject="Need help with matching",
                    message="I cannot see job matches in my dashboard.",
                ),
                authorization=f"Bearer {token}",
            )
        )

    assert exc.value.status_code == 502
