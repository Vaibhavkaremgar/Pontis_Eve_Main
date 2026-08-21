"""Unit tests for candidate profile photo helpers."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server import (  # noqa: E402
    _candidate_photo_url,
    _candidate_photo_dir,
    _resolve_candidate_photo_path,
    _photo_view_url,
    _validate_candidate_photo_upload,
    MAX_PROFILE_PHOTO_BYTES,
    upload_profile_photo,
    delete_profile_photo,
)


def test_validate_candidate_photo_upload_accepts_supported_types():
    assert _validate_candidate_photo_upload("image/jpeg", b"x") == ".jpg"
    assert _validate_candidate_photo_upload("image/png", b"x") == ".png"
    assert _validate_candidate_photo_upload("image/webp", b"x") == ".webp"


def test_validate_candidate_photo_upload_rejects_unsupported_type():
    with pytest.raises(HTTPException) as exc:
        _validate_candidate_photo_upload("image/gif", b"x")
    assert getattr(exc.value, "status_code", None) == 400


def test_validate_candidate_photo_upload_rejects_oversized_file():
    with pytest.raises(HTTPException) as exc:
        _validate_candidate_photo_upload("image/png", b"x" * (MAX_PROFILE_PHOTO_BYTES + 1))
    assert getattr(exc.value, "status_code", None) == 400


def test_candidate_photo_path_is_scoped_to_candidate_dir(tmp_path, monkeypatch):
    import server

    monkeypatch.setattr(server, "DOCS_DIR", tmp_path)
    candidate_id = "cand-123"
    candidate_dir = _candidate_photo_dir(candidate_id)
    candidate_dir.mkdir(parents=True, exist_ok=True)

    valid_path = candidate_dir / "photo.png"
    valid_path.write_bytes(b"img")
    assert _resolve_candidate_photo_path(candidate_id, str(valid_path)) == valid_path.resolve()

    outside_path = tmp_path / "other" / "photo.png"
    outside_path.parent.mkdir(parents=True, exist_ok=True)
    outside_path.write_bytes(b"img")
    assert _resolve_candidate_photo_path(candidate_id, str(outside_path)) is None


def test_candidate_photo_url_falls_back_to_view_endpoint(tmp_path, monkeypatch):
    import server

    monkeypatch.setattr(server, "DOCS_DIR", tmp_path)
    candidate_id = "cand-123"
    candidate_dir = _candidate_photo_dir(candidate_id)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    photo_path = candidate_dir / "photo.webp"
    photo_path.write_bytes(b"img")

    profile = {"id": candidate_id}
    raw_data = {"photo_file_path": str(photo_path)}

    assert _candidate_photo_url(profile, raw_data) == f"/api/candidate/{candidate_id}/photo/view"


def test_photo_view_url_can_be_versioned():
    assert _photo_view_url("cand-123") == "/api/candidate/cand-123/photo/view"
    assert _photo_view_url("cand-123", "rev-1") == "/api/candidate/cand-123/photo/view?rev=rev-1"


def test_upload_and_replace_profile_photo_updates_stored_reference(tmp_path, monkeypatch):
    import server

    class FakeUploadFile:
        def __init__(self, content: bytes, filename: str, content_type: str):
            self._content = content
            self.filename = filename
            self.content_type = content_type

        async def read(self):
            return self._content

    class FakeSession:
        def __init__(self):
            self.executed = []
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, stmt, params):
            self.executed.append(params)

        async def commit(self):
            self.committed = True

    async def run_upload(existing_raw_data, content: bytes, filename: str, content_type: str, version_hex: str):
        fake_session = FakeSession()
        candidate_id = "cand-123"
        old_path = existing_raw_data.get("photo_file_path")

        async def fake_get_candidate_row(cid):
            return {"id": cid, "raw_data": dict(existing_raw_data)}

        monkeypatch.setattr(server, "DOCS_DIR", tmp_path)
        monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)
        monkeypatch.setattr(server, "SessionLocal", lambda: fake_session)
        monkeypatch.setattr(server.uuid, "uuid4", lambda: type("U", (), {"hex": version_hex})())

        if old_path:
            Path(old_path).parent.mkdir(parents=True, exist_ok=True)
            Path(old_path).write_bytes(b"old")

        result = await upload_profile_photo(candidate_id, FakeUploadFile(content, filename, content_type))
        stored = json.loads(fake_session.executed[0]["rd"])
        return result, stored, fake_session, Path(old_path) if old_path else None

    first_result, first_stored, first_session, _ = asyncio.run(
        run_upload({}, b"first", "first.png", "image/png", "rev-first")
    )
    assert first_result["photo_url"] == "/api/candidate/cand-123/photo/view?rev=rev-first"
    assert first_stored["photo_url"] == "/api/candidate/cand-123/photo/view?rev=rev-first"
    assert first_stored["photo_version"] == "rev-first"
    assert first_session.committed is True
    assert first_stored["photo_file_path"].endswith("rev-first.png")

    second_result, second_stored, second_session, old_path = asyncio.run(
        run_upload(
            first_stored,
            b"second",
            "second.webp",
            "image/webp",
            "rev-second",
        )
    )
    assert second_result["photo_url"] == "/api/candidate/cand-123/photo/view?rev=rev-second"
    assert second_stored["photo_url"] == "/api/candidate/cand-123/photo/view?rev=rev-second"
    assert second_stored["photo_version"] == "rev-second"
    assert second_session.committed is True
    assert old_path is not None
    assert not old_path.exists()
    assert Path(second_stored["photo_file_path"]).exists()


def test_delete_profile_photo_clears_stored_reference_and_file(tmp_path, monkeypatch):
    import server

    class FakeSession:
        def __init__(self):
            self.executed = []
            self.committed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, stmt, params):
            self.executed.append(params)

        async def commit(self):
            self.committed = True

    async def run_delete():
        candidate_id = "cand-123"
        photo_dir = tmp_path / candidate_id / "photo"
        photo_dir.mkdir(parents=True, exist_ok=True)
        photo_path = photo_dir / "rev-delete.png"
        photo_path.write_bytes(b"photo")
        existing_raw_data = {
            "photo_url": "/api/candidate/cand-123/photo/view?rev=rev-delete",
            "photo_file_path": str(photo_path),
            "photo_version": "rev-delete",
        }
        fake_session = FakeSession()

        async def fake_get_candidate_row(cid):
            return {"id": cid, "raw_data": dict(existing_raw_data)}

        monkeypatch.setattr(server, "DOCS_DIR", tmp_path)
        monkeypatch.setattr(server, "_get_candidate_row", fake_get_candidate_row)
        monkeypatch.setattr(server, "SessionLocal", lambda: fake_session)

        result = await delete_profile_photo(candidate_id)
        stored = json.loads(fake_session.executed[0]["rd"])
        return result, stored, fake_session, photo_path

    result, stored, session, photo_path = asyncio.run(run_delete())
    assert result == {"photo_url": None, "status": "deleted"}
    assert session.committed is True
    assert stored.get("photo_url") is None
    assert stored.get("photo_file_path") is None
    assert stored.get("photo_version") is None
    assert not photo_path.exists()
