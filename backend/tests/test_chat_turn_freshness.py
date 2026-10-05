"""Regression coverage for ordered, fresh Chat with Eve turns."""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server


@pytest.mark.asyncio
async def test_new_salary_turn_uses_current_message_not_previous_assistant_reply(monkeypatch):
    saved_window = []
    llm_inputs = []

    async def load_window(_candidate_id):
        return list(saved_window)

    async def save_window(_candidate_id, _session_id, messages):
        saved_window[:] = messages

    async def get_candidate(_candidate_id):
        return {"id": "cand-1", "raw_data": {}, "parsed_resume_json": None}

    async def apply_updates(_candidate_id, updates):
        deletions = updates.get("profile_deletions") or {}
        return {"updated": True, "deleted": deletions}

    async def noop(*_args, **_kwargs):
        return None

    async def complete(**kwargs):
        llm_inputs.append(kwargs["messages"])
        current = kwargs["messages"][-1]["content"]
        reply = (
            "I've noted your expected salary of 7-10 LPA."
            if "7-10 LPA" in current
            else "AWS Certificate has been removed."
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])

    monkeypatch.setattr(server, "_load_chat_window", load_window)
    monkeypatch.setattr(server, "_save_chat_window", save_window)
    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "_normalize_for_frontend", lambda _row: {})
    monkeypatch.setattr(server, "_build_profile_context", lambda _profile: ("profile", []))
    monkeypatch.setattr(server, "_advance_voice_intake_from_chat", noop)
    monkeypatch.setattr(server, "_trigger_matching", noop)
    monkeypatch.setattr(server, "_apply_profile_updates", apply_updates)
    monkeypatch.setattr(
        server,
        "openai_client",
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete))),
    )

    first = await server.chat(server.ChatRequest(
        candidate_id="cand-1", session_id="s1",
        messages=[server.ChatMessageIn(role="user", content="Remove AWS Certificate from Certifications")],
    ))
    second = await server.chat(server.ChatRequest(
        candidate_id="cand-1", session_id="s1",
        messages=[server.ChatMessageIn(role="user", content="I am expecting 7-10 LPA")],
    ))

    assert first.reply == "AWS Certificate has been removed."
    assert second.reply == "I've noted your expected salary of 7-10 LPA."
    assert llm_inputs[-1][-1] == {"role": "user", "content": "I am expecting 7-10 LPA"}
    assert saved_window[-1] == {"role": "assistant", "content": second.reply}


@pytest.mark.asyncio
async def test_sanitized_completion_claim_is_persisted_and_returned(monkeypatch):
    saved_window = []

    class FakeMappings:
        def fetchone(self):
            return None

    class FakeResult:
        def mappings(self):
            return FakeMappings()

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, *_args, **_kwargs):
            return FakeResult()

    async def save_window(_candidate_id, _session_id, messages):
        saved_window[:] = messages

    async def get_candidate(_candidate_id):
        return {"id": "cand-87", "raw_data": {}, "parsed_resume_json": None}

    async def noop(*_args, **_kwargs):
        return None

    async def load_empty(*_args, **_kwargs):
        return []

    async def authoritative_profile(_candidate_id):
        return {
            "profile_strength_detail": {
                "percent": 87,
                "ninety_percent_guidance": {
                    "items": [{"action": "Add another relevant project"}],
                },
            },
        }

    async def complete(**_kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content="Your profile is already 100% complete—there are no missing fields left."
        ))])

    monkeypatch.setattr(server, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(server, "_authorize_candidate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_load_chat_window", load_empty)
    monkeypatch.setattr(server, "_save_chat_window", save_window)
    monkeypatch.setattr(server, "_load_intake_ledger", load_empty)
    monkeypatch.setattr(server, "_get_candidate_row", get_candidate)
    monkeypatch.setattr(server, "_get_candidate_profile_payload", authoritative_profile)
    monkeypatch.setattr(server, "_normalize_for_frontend", lambda _row: {
        "profile_strength_detail": {"percent": 87},
    })
    monkeypatch.setattr(server, "_build_profile_context", lambda _profile: ("profile", []))
    monkeypatch.setattr(server, "_missing_canonical_preference_fields", lambda *_args: [])
    monkeypatch.setattr(server, "_advance_voice_intake_from_chat", noop)
    monkeypatch.setattr(server, "_record_demonstrated_skill_usage", noop)
    monkeypatch.setattr(server, "openai_client", SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=complete))
    ))

    response = await server.chat(server.ChatRequest(
        candidate_id="cand-87",
        session_id="s87",
        messages=[server.ChatMessageIn(
            role="user",
            content="What are the missing details to make my profile 90%?",
        )],
    ))

    assert "100% complete" not in response.reply
    assert "currently 87%" in response.reply
    assert saved_window[-1] == {"role": "assistant", "content": response.reply}
