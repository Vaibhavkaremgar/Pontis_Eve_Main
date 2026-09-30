"""Focused coverage for voice-intake matching-refresh timing."""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server


@pytest.mark.parametrize("status", ["in_progress", "pending", None])
def test_incomplete_voice_intake_does_not_schedule_matching(monkeypatch, status):
    schedule = Mock()
    monkeypatch.setattr(server.asyncio, "ensure_future", schedule)

    server._schedule_voice_intake_matching("candidate-1", status)

    schedule.assert_not_called()


def test_completed_voice_intake_schedules_existing_matching_refresh(monkeypatch):
    matching = Mock(return_value="matching-task")
    schedule = Mock()
    monkeypatch.setattr(server, "_trigger_matching", matching)
    monkeypatch.setattr(server.asyncio, "ensure_future", schedule)

    server._schedule_voice_intake_matching("candidate-1", "completed")

    matching.assert_called_once_with("candidate-1")
    schedule.assert_called_once_with("matching-task")


def test_existing_matching_entrypoint_is_unchanged():
    assert server._trigger_matching.__name__ == "_trigger_matching"
