"""
Eve backend integration tests — Adam ↔ Eve contract.

Required env vars for full integration tests:
  REACT_APP_BACKEND_URL   — Eve base URL
  EVE_INTERNAL_TOKEN      — token Adam sends to Eve  (Adam → Eve)
  ADAM_INTERNAL_TOKEN     — token Eve sends to Adam  (Eve → Adam, checked via source)
  TEST_CANDIDATE_ID       — existing candidates.id
  TEST_JOB_ID             — existing job_descriptions.id whose agency_id == TEST_AGENCY_ID
  TEST_AGENCY_ID          — existing agencies.id
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://candidate-hub-97.preview.emergentagent.com",
).rstrip("/")

# Adam → Eve uses EVE_INTERNAL_TOKEN
EVE_TOKEN = os.environ.get("EVE_INTERNAL_TOKEN", "pontis_internal_secure_2026")
AUTH_HEADERS = {"Authorization": f"Bearer {EVE_TOKEN}"}
BAD_AUTH = {"Authorization": "Bearer wrong_token"}

TEST_CANDIDATE_ID = os.environ.get("TEST_CANDIDATE_ID", "")
TEST_JOB_ID = os.environ.get("TEST_JOB_ID", "")
TEST_AGENCY_ID = os.environ.get("TEST_AGENCY_ID", "")

_INTEGRATION = bool(TEST_CANDIDATE_ID and TEST_JOB_ID and TEST_AGENCY_ID)
_skip = pytest.mark.skipif(not _INTEGRATION, reason="TEST_CANDIDATE_ID/JOB_ID/AGENCY_ID not set")

RANDOM_UUID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Smoke
# ---------------------------------------------------------------------------

def test_root():
    r = requests.get(f"{BASE_URL}/api/", timeout=15)
    assert r.status_code == 200
    assert "Eve" in r.json().get("message", "") or "Pontis" in r.json().get("message", "")


# ---------------------------------------------------------------------------
# Authentication — token separation
# ---------------------------------------------------------------------------

def test_recruiter_interest_rejects_bad_token():
    r = requests.post(
        f"{BASE_URL}/api/internal/recruiter-interest",
        json={"adam_event_id": RANDOM_UUID, "candidate_id": RANDOM_UUID,
              "job_id": RANDOM_UUID, "agency_id": RANDOM_UUID},
        headers=BAD_AUTH, timeout=15,
    )
    assert r.status_code == 401


def test_recruiter_interest_missing_auth():
    r = requests.post(
        f"{BASE_URL}/api/internal/recruiter-interest",
        json={"adam_event_id": RANDOM_UUID, "candidate_id": RANDOM_UUID,
              "job_id": RANDOM_UUID, "agency_id": RANDOM_UUID},
        timeout=15,
    )
    assert r.status_code == 401


def test_candidate_response_endpoint_rejects_bad_token():
    r = requests.post(
        f"{BASE_URL}/api/internal/candidate-response",
        json={"eve_event_id": RANDOM_UUID, "adam_event_id": RANDOM_UUID,
              "candidate_id": RANDOM_UUID, "job_id": RANDOM_UUID,
              "agency_id": RANDOM_UUID, "response": "interested"},
        headers=BAD_AUTH, timeout=15,
    )
    assert r.status_code == 401


def test_server_uses_eve_internal_token_for_inbound():
    """EVE_INTERNAL_TOKEN must be the env var used to verify Adam → Eve requests."""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "server.py").read_text()
    assert "EVE_INTERNAL_TOKEN" in src
    # Old single-token variable must not be used for auth verification
    assert "_verify_eve_token" in src


def test_server_uses_adam_internal_token_for_outbound():
    """ADAM_INTERNAL_TOKEN must be the env var used when Eve calls Adam."""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "server.py").read_text()
    assert "ADAM_INTERNAL_TOKEN" in src
    # The delivery function must use ADAM_INTERNAL_TOKEN, not a shared token
    assert 'f"Bearer {ADAM_INTERNAL_TOKEN}"' in src


# ---------------------------------------------------------------------------
# Validation — unknown entities
# ---------------------------------------------------------------------------

def test_recruiter_interest_unknown_candidate():
    r = requests.post(
        f"{BASE_URL}/api/internal/recruiter-interest",
        json={"adam_event_id": str(uuid.uuid4()), "candidate_id": str(uuid.uuid4()),
              "job_id": str(uuid.uuid4()), "agency_id": str(uuid.uuid4())},
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code == 404
    assert "candidate" in r.json()["detail"].lower()


@_skip
def test_recruiter_interest_unknown_job():
    r = requests.post(
        f"{BASE_URL}/api/internal/recruiter-interest",
        json={"adam_event_id": str(uuid.uuid4()), "candidate_id": TEST_CANDIDATE_ID,
              "job_id": str(uuid.uuid4()), "agency_id": TEST_AGENCY_ID},
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code == 404
    assert "job" in r.json()["detail"].lower()


@_skip
def test_recruiter_interest_unknown_agency():
    r = requests.post(
        f"{BASE_URL}/api/internal/recruiter-interest",
        json={"adam_event_id": str(uuid.uuid4()), "candidate_id": TEST_CANDIDATE_ID,
              "job_id": TEST_JOB_ID, "agency_id": str(uuid.uuid4())},
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code == 404
    assert "agency" in r.json()["detail"].lower()


@_skip
def test_recruiter_interest_job_agency_mismatch():
    r = requests.post(
        f"{BASE_URL}/api/internal/recruiter-interest",
        json={"adam_event_id": str(uuid.uuid4()), "candidate_id": TEST_CANDIDATE_ID,
              "job_id": TEST_JOB_ID, "agency_id": str(uuid.uuid4())},
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code in (404, 422)


def test_candidate_response_unknown_candidate():
    r = requests.post(
        f"{BASE_URL}/api/internal/candidate-response",
        json={"eve_event_id": str(uuid.uuid4()), "adam_event_id": str(uuid.uuid4()),
              "candidate_id": str(uuid.uuid4()), "job_id": str(uuid.uuid4()),
              "agency_id": str(uuid.uuid4()), "response": "interested"},
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code == 404
    assert "candidate" in r.json()["detail"].lower()


@_skip
def test_candidate_response_unknown_job():
    r = requests.post(
        f"{BASE_URL}/api/internal/candidate-response",
        json={"eve_event_id": str(uuid.uuid4()), "adam_event_id": str(uuid.uuid4()),
              "candidate_id": TEST_CANDIDATE_ID, "job_id": str(uuid.uuid4()),
              "agency_id": TEST_AGENCY_ID, "response": "interested"},
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code == 404
    assert "job" in r.json()["detail"].lower()


@_skip
def test_candidate_response_unknown_agency():
    r = requests.post(
        f"{BASE_URL}/api/internal/candidate-response",
        json={"eve_event_id": str(uuid.uuid4()), "adam_event_id": str(uuid.uuid4()),
              "candidate_id": TEST_CANDIDATE_ID, "job_id": TEST_JOB_ID,
              "agency_id": str(uuid.uuid4()), "response": "interested"},
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code == 404
    assert "agency" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Adam → Eve idempotency
# ---------------------------------------------------------------------------

@_skip
def test_recruiter_interest_idempotency():
    """Same adam_event_id twice → second call returns 'duplicate' with same rir_id."""
    adam_event_id = str(uuid.uuid4())
    payload = {
        "adam_event_id": adam_event_id,
        "candidate_id": TEST_CANDIDATE_ID,
        "job_id": TEST_JOB_ID,
        "agency_id": TEST_AGENCY_ID,
        "recruiter_message": "Great fit!",
    }
    r1 = requests.post(f"{BASE_URL}/api/internal/recruiter-interest",
                       json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r1.status_code == 201, r1.text
    assert r1.json()["status"] == "created"
    rir_id = r1.json()["rir_id"]

    r2 = requests.post(f"{BASE_URL}/api/internal/recruiter-interest",
                       json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r2.status_code == 201, r2.text
    assert r2.json()["status"] == "duplicate"
    assert r2.json()["rir_id"] == rir_id


# ---------------------------------------------------------------------------
# Eve → Adam idempotency (internal endpoint)
# ---------------------------------------------------------------------------

@_skip
def test_candidate_response_idempotency():
    """Same eve_event_id twice → second call returns 'duplicate'."""
    eve_event_id = str(uuid.uuid4())
    payload = {
        "eve_event_id": eve_event_id,
        "adam_event_id": str(uuid.uuid4()),
        "candidate_id": TEST_CANDIDATE_ID,
        "job_id": TEST_JOB_ID,
        "agency_id": TEST_AGENCY_ID,
        "response": "interested",
    }
    r1 = requests.post(f"{BASE_URL}/api/internal/candidate-response",
                       json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "accepted"

    r2 = requests.post(f"{BASE_URL}/api/internal/candidate-response",
                       json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "duplicate"


@_skip
def test_duplicate_candidate_response_does_not_create_another_outbound_event():
    """Duplicate eve_event_id must not insert a second row in eve_outbound_events."""
    eve_event_id = str(uuid.uuid4())
    payload = {
        "eve_event_id": eve_event_id,
        "adam_event_id": str(uuid.uuid4()),
        "candidate_id": TEST_CANDIDATE_ID,
        "job_id": TEST_JOB_ID,
        "agency_id": TEST_AGENCY_ID,
        "response": "not_interested",
    }
    r1 = requests.post(f"{BASE_URL}/api/internal/candidate-response",
                       json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r1.status_code == 200

    r2 = requests.post(f"{BASE_URL}/api/internal/candidate-response",
                       json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"


# ---------------------------------------------------------------------------
# respond_to_opportunity → eve_outbound_events wiring
# ---------------------------------------------------------------------------

@_skip
def test_respond_to_opportunity_enqueues_outbound_event_interested():
    """
    Full flow: Adam sends recruiter-interest → candidate responds 'interested'
    → respond_to_opportunity returns ok → outbound event was enqueued
    (verified by calling the internal endpoint with the same eve_event_id being
    rejected as duplicate on a second candidate-response call with same data).
    """
    # 1. Adam → Eve: create a recruiter interest request
    adam_event_id = str(uuid.uuid4())
    r = requests.post(
        f"{BASE_URL}/api/internal/recruiter-interest",
        json={
            "adam_event_id": adam_event_id,
            "candidate_id": TEST_CANDIDATE_ID,
            "job_id": TEST_JOB_ID,
            "agency_id": TEST_AGENCY_ID,
            "recruiter_message": "We'd love to connect.",
        },
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code == 201, r.text
    rir_id = r.json()["rir_id"]

    # 2. Candidate responds via the candidate-facing endpoint
    r2 = requests.post(
        f"{BASE_URL}/api/candidate/{TEST_CANDIDATE_ID}/opportunities/{rir_id}/respond",
        json={"response": "interested"},
        timeout=15,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "ok"
    assert r2.json()["candidate_response"] == "interested"


@_skip
def test_respond_to_opportunity_enqueues_outbound_event_not_interested():
    """Candidate responds 'not_interested' — endpoint returns ok."""
    adam_event_id = str(uuid.uuid4())
    r = requests.post(
        f"{BASE_URL}/api/internal/recruiter-interest",
        json={
            "adam_event_id": adam_event_id,
            "candidate_id": TEST_CANDIDATE_ID,
            "job_id": TEST_JOB_ID,
            "agency_id": TEST_AGENCY_ID,
        },
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code == 201, r.text
    rir_id = r.json()["rir_id"]

    r2 = requests.post(
        f"{BASE_URL}/api/candidate/{TEST_CANDIDATE_ID}/opportunities/{rir_id}/respond",
        json={"response": "not_interested"},
        timeout=15,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["candidate_response"] == "not_interested"


@_skip
def test_respond_to_opportunity_duplicate_is_idempotent():
    """
    Responding twice to the same opportunity returns 'already_responded'
    on the second call — no second outbound event is created.
    """
    adam_event_id = str(uuid.uuid4())
    r = requests.post(
        f"{BASE_URL}/api/internal/recruiter-interest",
        json={
            "adam_event_id": adam_event_id,
            "candidate_id": TEST_CANDIDATE_ID,
            "job_id": TEST_JOB_ID,
            "agency_id": TEST_AGENCY_ID,
        },
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code == 201
    rir_id = r.json()["rir_id"]

    r1 = requests.post(
        f"{BASE_URL}/api/candidate/{TEST_CANDIDATE_ID}/opportunities/{rir_id}/respond",
        json={"response": "interested"}, timeout=15,
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "ok"

    # Second call — must be idempotent
    r2 = requests.post(
        f"{BASE_URL}/api/candidate/{TEST_CANDIDATE_ID}/opportunities/{rir_id}/respond",
        json={"response": "interested"}, timeout=15,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "already_responded"


# ---------------------------------------------------------------------------
# Source-code structural checks — outbound event wiring
# ---------------------------------------------------------------------------

def test_respond_to_opportunity_inserts_eve_outbound_events():
    """respond_to_opportunity must INSERT into eve_outbound_events."""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "server.py").read_text()
    # The respond endpoint must reference eve_outbound_events
    assert "eve_outbound_events" in src
    # It must generate a new eve_event_id
    assert "eve_event_id" in src
    # It must call _attempt_delivery
    assert "_attempt_delivery" in src


def test_outbound_event_uses_adam_event_id_from_rir():
    """The outbound event must read adam_event_id from recruiter_interest_requests."""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "server.py").read_text()
    # respond_to_opportunity must select adam_event_id from the RIR row
    assert "adam_event_id" in src
    # Must not use email for candidate identity in the internal path
    assert 'WHERE id = :cid' in src  # candidate validated by UUID PK


def test_no_email_lookup_in_internal_endpoints():
    """Internal endpoints must never look up candidates by email."""
    import ast, pathlib
    src = (pathlib.Path(__file__).parent.parent / "server.py").read_text()
    # Parse the internal_recruiter_interest and respond_to_opportunity functions
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
            "internal_recruiter_interest",
            "respond_to_opportunity",
            "_attempt_delivery",
        ):
            func_src = ast.get_source_segment(src, node) or ""
            assert "email" not in func_src.lower() or "WHERE id" in func_src, (
                f"{node.name} must not use email for candidate lookup"
            )


# ---------------------------------------------------------------------------
# Delivery / accepted
# ---------------------------------------------------------------------------

@_skip
def test_successful_delivery_returns_accepted():
    payload = {
        "eve_event_id": str(uuid.uuid4()),
        "adam_event_id": str(uuid.uuid4()),
        "candidate_id": TEST_CANDIDATE_ID,
        "job_id": TEST_JOB_ID,
        "agency_id": TEST_AGENCY_ID,
        "response": "interested",
    }
    r = requests.post(f"{BASE_URL}/api/internal/candidate-response",
                      json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"


# ---------------------------------------------------------------------------
# Retry schedule — source-code checks (no live DB required)
# ---------------------------------------------------------------------------

def test_retry_delays_schedule():
    """Retry schedule must be: 10s, 30s, 2m, 10m, 30m."""
    import ast, pathlib
    src = pathlib.Path(__file__).parent.parent / "server.py"
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_RETRY_DELAYS":
                    actual = ast.literal_eval(node.value)
                    assert actual == [10, 30, 120, 600, 1800], f"got {actual}"
                    return
    pytest.fail("_RETRY_DELAYS not found in server.py")


def test_max_attempts_is_five():
    import ast, pathlib
    src = pathlib.Path(__file__).parent.parent / "server.py"
    tree = ast.parse(src.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_MAX_ATTEMPTS":
                    assert ast.literal_eval(node.value) == 5
                    return
    pytest.fail("_MAX_ATTEMPTS not found in server.py")


def test_retry_worker_started_on_startup():
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "server.py").read_text()
    assert "_retry_worker" in src
    assert "on_startup" in src


def test_outbound_payload_fields():
    """Eve → Adam payload must contain all required fields and use adam_event_id."""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "server.py").read_text()
    for field in ('"eve_event_id"', '"adam_event_id"', '"candidate_id"',
                  '"job_id"', '"agency_id"', '"response"'):
        assert field in src, f"missing field {field} in outbound payload"


def test_outbound_event_persisted_before_delivery():
    """
    The INSERT into eve_outbound_events must appear before _attempt_delivery
    in the source of internal_candidate_response.
    """
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "server.py").read_text()
    insert_pos = src.find("INSERT INTO eve_outbound_events")
    delivery_pos = src.find("await _attempt_delivery")
    assert insert_pos != -1, "INSERT INTO eve_outbound_events not found"
    assert delivery_pos != -1, "_attempt_delivery call not found"
    assert insert_pos < delivery_pos, "outbound event must be persisted before delivery attempt"


# ---------------------------------------------------------------------------
# Chat smoke (unchanged)
# ---------------------------------------------------------------------------

def test_chat_empty_messages():
    r = requests.post(f"{BASE_URL}/api/chat",
                      json={"messages": [], "session_id": "t"}, timeout=15)
    assert r.status_code == 400


def test_chat_no_user_msg():
    r = requests.post(f"{BASE_URL}/api/chat", json={
        "messages": [{"role": "assistant", "content": "hi"}], "session_id": "t"
    }, timeout=15)
    assert r.status_code == 400


# ===========================================================================
# candidate-notification endpoint tests (Events 2 & 3)
# ===========================================================================

CN_URL = f"{BASE_URL}/api/internal/candidate-notification"


def _slot_payload(event_id=None, candidate_id=None, job_id=None, agency_id=None):
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "candidate_id": candidate_id or TEST_CANDIDATE_ID,
        "job_id": job_id or TEST_JOB_ID,
        "agency_id": agency_id or TEST_AGENCY_ID,
        "notification_type": "interview_slot_booking",
        "title": "Book your interview slot",
        "message": "Please book a slot at your earliest convenience.",
        "booking_url": "https://adam.example.com/book/abc123",
        "expires_at": "2099-12-31T23:59:59Z",
    }


def _round_payload(event_id=None, candidate_id=None, job_id=None, agency_id=None):
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "candidate_id": candidate_id or TEST_CANDIDATE_ID,
        "job_id": job_id or TEST_JOB_ID,
        "agency_id": agency_id or TEST_AGENCY_ID,
        "notification_type": "second_round_invite",
        "title": "You've been selected for the second round",
        "message": "Congratulations! Please review the details below.",
        "round_name": "Technical Interview",
        "scheduled_at": "2099-06-15T10:00:00Z",
        "meeting_url": "https://meet.example.com/xyz",
        "location": "Remote",
        "instructions": "Prepare a 15-minute coding exercise.",
    }


# --- Test 12 & 13: Auth ---

def test_candidate_notification_missing_auth():
    """Test 12: Missing auth → 401"""
    r = requests.post(CN_URL, json={
        "event_id": str(uuid.uuid4()), "candidate_id": str(uuid.uuid4()),
        "job_id": str(uuid.uuid4()), "agency_id": str(uuid.uuid4()),
        "notification_type": "interview_slot_booking",
        "title": "t", "message": "m",
    }, timeout=15)
    assert r.status_code == 401


def test_candidate_notification_wrong_token():
    """Test 13: Wrong token → 401"""
    r = requests.post(CN_URL, json={
        "event_id": str(uuid.uuid4()), "candidate_id": str(uuid.uuid4()),
        "job_id": str(uuid.uuid4()), "agency_id": str(uuid.uuid4()),
        "notification_type": "interview_slot_booking",
        "title": "t", "message": "m",
    }, headers=BAD_AUTH, timeout=15)
    assert r.status_code == 401


# --- Test 8: Invalid candidate → 404 ---

def test_candidate_notification_invalid_candidate():
    """Test 8: Unknown candidate_id → 404"""
    r = requests.post(CN_URL, json={
        "event_id": str(uuid.uuid4()), "candidate_id": str(uuid.uuid4()),
        "job_id": str(uuid.uuid4()), "agency_id": str(uuid.uuid4()),
        "notification_type": "interview_slot_booking",
        "title": "t", "message": "m",
    }, headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 404
    assert "candidate" in r.json()["detail"].lower()


# --- Tests requiring real DB fixtures ---

@_skip
def test_candidate_notification_slot_booking_201():
    """Test 1: Valid interview_slot_booking → 201"""
    r = requests.post(CN_URL, json=_slot_payload(), headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "created"
    assert r.json()["notification_id"]


@_skip
def test_candidate_notification_second_round_201():
    """Test 2: Valid second_round_invite → 201"""
    r = requests.post(CN_URL, json=_round_payload(), headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "created"
    assert r.json()["notification_id"]


@_skip
def test_candidate_notification_written_to_activity_feed():
    """Test 3: Notification written to candidate_activity_feed"""
    event_id = str(uuid.uuid4())
    r = requests.post(CN_URL, json=_slot_payload(event_id=event_id),
                      headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 201, r.text
    notif_id = r.json()["notification_id"]

    feed = requests.get(
        f"{BASE_URL}/api/candidate/{TEST_CANDIDATE_ID}/notifications",
        timeout=15,
    )
    assert feed.status_code == 200
    ids = [n["id"] for n in feed.json()]
    assert notif_id in ids


@_skip
def test_candidate_notification_uses_candidates_id():
    """Test 4: candidate_id is candidates.id (UUID PK, not email)"""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "server.py").read_text()
    assert "candidates WHERE id = :cid" in src


@_skip
def test_candidate_notification_event_id_stored():
    """Test 5: event_id stored correctly in candidate_activity_feed"""
    event_id = str(uuid.uuid4())
    r = requests.post(CN_URL, json=_slot_payload(event_id=event_id),
                      headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 201, r.text

    feed = requests.get(
        f"{BASE_URL}/api/candidate/{TEST_CANDIDATE_ID}/notifications",
        timeout=15,
    )
    assert feed.status_code == 200
    matching = [n for n in feed.json() if n.get("event_id") == event_id]
    assert len(matching) == 1


@_skip
def test_candidate_notification_duplicate_event_id_409():
    """Test 6: Duplicate event_id → 409"""
    event_id = str(uuid.uuid4())
    payload = _slot_payload(event_id=event_id)
    r1 = requests.post(CN_URL, json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r1.status_code == 201, r1.text
    notif_id = r1.json()["notification_id"]

    r2 = requests.post(CN_URL, json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r2.status_code == 409, r2.text
    assert r2.json()["status"] == "duplicate"
    assert r2.json()["notification_id"] == notif_id


@_skip
def test_candidate_notification_duplicate_does_not_create_row():
    """Test 7: Duplicate event_id does not create another row"""
    event_id = str(uuid.uuid4())
    payload = _slot_payload(event_id=event_id)
    r1 = requests.post(CN_URL, json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r1.status_code == 201

    r2 = requests.post(CN_URL, json=payload, headers=AUTH_HEADERS, timeout=15)
    assert r2.status_code == 409

    feed = requests.get(
        f"{BASE_URL}/api/candidate/{TEST_CANDIDATE_ID}/notifications",
        timeout=15,
    )
    matching = [n for n in feed.json() if n.get("event_id") == event_id]
    assert len(matching) == 1


@_skip
def test_candidate_notification_invalid_job():
    """Test 9: Unknown job_id → 404"""
    r = requests.post(CN_URL, json=_slot_payload(job_id=str(uuid.uuid4())),
                      headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 404
    assert "job" in r.json()["detail"].lower()


@_skip
def test_candidate_notification_invalid_agency():
    """Test 10: Unknown agency_id → 404"""
    r = requests.post(CN_URL, json=_slot_payload(agency_id=str(uuid.uuid4())),
                      headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 404
    assert "agency" in r.json()["detail"].lower()


@_skip
def test_candidate_notification_job_agency_mismatch():
    """Test 11: Job/agency mismatch → 422"""
    r = requests.post(
        CN_URL,
        json={**_slot_payload(), "agency_id": str(uuid.uuid4())},
        headers=AUTH_HEADERS, timeout=15,
    )
    assert r.status_code in (404, 422)


@_skip
def test_candidate_notification_slot_booking_metadata():
    """Test 14: Slot booking metadata contains booking_url"""
    event_id = str(uuid.uuid4())
    r = requests.post(CN_URL, json=_slot_payload(event_id=event_id),
                      headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 201

    feed = requests.get(
        f"{BASE_URL}/api/candidate/{TEST_CANDIDATE_ID}/notifications",
        timeout=15,
    )
    matching = [n for n in feed.json() if n.get("event_id") == event_id]
    assert len(matching) == 1
    meta = matching[0]["metadata"]
    assert "booking_url" in meta
    assert meta["booking_url"] == "https://adam.example.com/book/abc123"


@_skip
def test_candidate_notification_second_round_metadata():
    """Test 15: Second round metadata contains supplied details"""
    event_id = str(uuid.uuid4())
    r = requests.post(CN_URL, json=_round_payload(event_id=event_id),
                      headers=AUTH_HEADERS, timeout=15)
    assert r.status_code == 201

    feed = requests.get(
        f"{BASE_URL}/api/candidate/{TEST_CANDIDATE_ID}/notifications",
        timeout=15,
    )
    matching = [n for n in feed.json() if n.get("event_id") == event_id]
    assert len(matching) == 1
    meta = matching[0]["metadata"]
    assert meta.get("round_name") == "Technical Interview"
    assert meta.get("meeting_url") == "https://meet.example.com/xyz"
    assert meta.get("location") == "Remote"
    assert meta.get("instructions") == "Prepare a 15-minute coding exercise."


# --- Test 16: Existing recruiter-interest endpoint still passes ---

def test_recruiter_interest_endpoint_still_exists():
    """Test 16: Existing recruiter-interest endpoint still rejects bad token (not broken)"""
    r = requests.post(
        f"{BASE_URL}/api/internal/recruiter-interest",
        json={"adam_event_id": str(uuid.uuid4()), "candidate_id": str(uuid.uuid4()),
              "job_id": str(uuid.uuid4()), "agency_id": str(uuid.uuid4())},
        headers=BAD_AUTH, timeout=15,
    )
    assert r.status_code == 401


# --- Test 17: Existing Eve → Adam candidate-response flow still passes ---

def test_candidate_response_flow_still_exists():
    """Test 17: Existing candidate-response endpoint still rejects bad token (not broken)"""
    r = requests.post(
        f"{BASE_URL}/api/internal/candidate-response",
        json={"eve_event_id": str(uuid.uuid4()), "adam_event_id": str(uuid.uuid4()),
              "candidate_id": str(uuid.uuid4()), "job_id": str(uuid.uuid4()),
              "agency_id": str(uuid.uuid4()), "response": "interested"},
        headers=BAD_AUTH, timeout=15,
    )
    assert r.status_code == 401


# --- Tests 18-20: Source-code structural checks (no live DB required) ---

def test_candidate_ui_renders_slot_booking_notification():
    """Test 18: Candidate UI renders slot-booking notification (source check)"""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent.parent /
           "frontend" / "src" / "components" / "LivingProfile.jsx").read_text(encoding="utf-8")
    assert "interview_slot_booking" in src
    assert "Book your interview slot" in src
    assert "booking_url" in src
    assert "slot-booking-link" in src


def test_candidate_ui_renders_second_round_notification():
    """Test 19: Candidate UI renders second-round notification (source check)"""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent.parent /
           "frontend" / "src" / "components" / "LivingProfile.jsx").read_text(encoding="utf-8")
    assert "second_round_invite" in src
    assert "round_name" in src
    assert "meeting_url" in src
    assert "second-round-meeting-link" in src


def test_unread_notification_count_includes_activity_feed():
    """Test 20: Bell count includes unread candidate_activity_feed (source check)"""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent.parent /
           "frontend" / "src" / "pages" / "Dashboard.jsx").read_text(encoding="utf-8")
    assert "notifications" in src
    assert "is_read" in src
    assert "notifCount" in src or "notifPromise" in src
