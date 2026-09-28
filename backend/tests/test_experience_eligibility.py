import sys

sys.path.insert(0, "backend")

from candidate_job_matching_service import experience_eligibility, normalize_job_experience


def test_supported_experience_formats_normalize_to_common_bounds():
    assert normalize_job_experience("3+ years") == (3.0, None)
    assert normalize_job_experience("5 years") == (5.0, 5.0)
    assert normalize_job_experience("minimum 4 years") == (4.0, None)
    assert normalize_job_experience("at least 6 years") == (6.0, None)
    assert normalize_job_experience("2-4 years") == (2.0, 4.0)
    assert normalize_job_experience("2 to 4 years") == (2.0, 4.0)
    assert normalize_job_experience("1\N{EN DASH}3 yrs") == (1.0, 3.0)


def test_minimum_experience_matrix():
    assert not experience_eligibility(2, "6+ years")["eligible"]
    assert not experience_eligibility(2, "8+ years")["eligible"]
    assert not experience_eligibility(5, "8+ years")["eligible"]
    assert experience_eligibility(7, "5+ years")["eligible"]
    assert experience_eligibility(2, "1-3 years")["eligible"]
    assert experience_eligibility(2, "2+ years")["eligible"]
    assert experience_eligibility(3, "2-4 years")["eligible"]


def test_upper_bound_violation_is_hard_rejection():
    result = experience_eligibility(5, "2-4 years")
    assert result["decision"] == "rejected"
    assert result["rejection_reason"] == "candidate_above_job_maximum"


def test_unknown_requirements_are_neutral_not_exact_matches():
    result = experience_eligibility(5, "Experience not specified")
    assert result["eligible"] is True
    assert result["job_min_years"] is None
    assert result["job_max_years"] is None

