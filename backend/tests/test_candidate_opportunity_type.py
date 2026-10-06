import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server


def test_explicit_internship_preference_is_extracted():
    assert server._infer_profile_updates_from_message(
        "I'm looking for a Python internship."
    )["opportunity_type"] == "internship"


def test_explicit_job_preference_is_extracted():
    assert server._infer_profile_updates_from_message(
        "I'm looking for full-time Java developer jobs."
    )["opportunity_type"] == "job"


def test_unrelated_fresher_or_intern_context_does_not_infer_internship():
    assert "opportunity_type" not in server._infer_profile_updates_from_message(
        "I'm a fresher and worked with internal internship documentation."
    )


def test_switching_away_from_internships_is_job():
    assert server._infer_profile_updates_from_message(
        "I'm no longer looking for internships; I'm looking for full-time jobs."
    )["opportunity_type"] == "job"


def test_profile_update_allowlist_accepts_only_normalized_values():
    assert server._sanitize_profile_updates({"opportunity_type": "internship"}) == {
        "opportunity_type": "internship"
    }
    assert server._sanitize_profile_updates({"opportunity_type": "fresher"}) == {}
