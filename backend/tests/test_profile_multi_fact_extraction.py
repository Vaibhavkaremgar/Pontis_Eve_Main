"""Regression coverage for compound candidate answers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from server import _infer_profile_updates_from_message, _merge_voice_into_profile


def test_compound_preference_answer_is_split_into_canonical_fields():
    updates = _infer_profile_updates_from_message(
        "Looking for a full-time Java backend role in Hyderabad, "
        "expecting 7–10 LPA and can join immediately"
    )

    assert updates["preferred_roles"] == ["Java backend"]
    assert updates["employment_types"] == ["Full-time"]
    assert updates["preferred_locations"] == ["Hyderabad"]
    assert updates["salary_expectation"] == "₹7–10 LPA"
    assert updates["availability"] == "Immediately"


def test_compound_values_remain_in_their_profile_sections_when_merged():
    updates = _infer_profile_updates_from_message(
        "Looking for a full-time Java backend role in Hyderabad, "
        "expecting 7-10 LPA and can join immediately"
    )
    merged = _merge_voice_into_profile({"raw_data": {}, "skills": []}, updates)
    raw = merged["raw_data"]

    assert raw["preferred_roles"] == ["Java backend"]
    assert raw["employment_types"] == ["Full-time"]
    assert raw["preferred_locations"] == ["Hyderabad"]
    assert raw["salary_expectation"] == "₹7–10 LPA"
    assert raw["availability"] == "Immediately"
    assert "Hyderabad" not in raw["preferred_roles"]
    assert "LPA" not in raw["preferred_locations"]
