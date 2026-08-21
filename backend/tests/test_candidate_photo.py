"""Unit tests for candidate profile photo helpers."""
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
    _validate_candidate_photo_upload,
    MAX_PROFILE_PHOTO_BYTES,
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
