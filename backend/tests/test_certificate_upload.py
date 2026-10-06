import asyncio
import os
import sys
from types import SimpleNamespace

from fastapi import UploadFile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server  # noqa: E402


class FakeResult:
    def fetchone(self):
        return None


class FakeSession:
    def __init__(self, inserted):
        self.inserted = inserted

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement, params=None):
        assert "INSERT INTO candidate_certificates" in str(statement)
        self.inserted.append(params)
        return FakeResult()

    async def commit(self):
        pass


def test_certificate_upload_persists_file_and_metadata(tmp_path, monkeypatch):
    candidate_id = "candidate-1"
    inserted = []
    monkeypatch.setattr(server, "DOCS_DIR", tmp_path / "documents")

    async def get_candidate(candidate):
        assert candidate == candidate_id
        return {"id": candidate_id}

    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession(inserted))

    upload = UploadFile(filename="AWS Certificate.pdf", file=__import__("io").BytesIO(b"certificate-bytes"))
    response = asyncio.run(
        server.upload_certificate(
            candidate_id,
            upload,
            authorization=f"Bearer {server._issue_candidate_session_token(candidate_id)}",
        )
    )

    stored = tmp_path / "documents" / candidate_id / "certificates"
    files = list(stored.iterdir())
    assert response["filename"] == "AWS Certificate.pdf"
    assert len(files) == 1
    assert files[0].read_bytes() == b"certificate-bytes"
    assert inserted == [{
        "id": response["id"],
        "cid": candidate_id,
        "fn": "AWS Certificate.pdf",
        "fp": files[0].name,
    }]
