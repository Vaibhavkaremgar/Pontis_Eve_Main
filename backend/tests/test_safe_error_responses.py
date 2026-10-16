from fastapi import HTTPException

from server import _safe_http_error_detail


def test_safe_error_details_cover_timeout_and_standard_statuses():
    assert _safe_http_error_detail(504, "provider timeout") == "The request is taking longer than expected. Please try again."
    assert _safe_http_error_detail(401, "invalid JWT") == "Your session has expired. Please sign in again."
    assert _safe_http_error_detail(429, "provider quota") == "Too many requests. Please wait a moment and try again."
    assert _safe_http_error_detail(500, "database password leaked") == "Something went wrong on our server. Please try again later."


def test_safe_error_detail_preserves_meaningful_client_validation_message():
    assert _safe_http_error_detail(422, "Please upload a PDF.") == "Please upload a PDF."
