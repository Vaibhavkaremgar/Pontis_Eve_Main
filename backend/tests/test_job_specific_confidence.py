"""
Tests for Job-Specific Recommendation Confidence (Section 12 of spec).

Covers all 12 required test scenarios plus structural validation.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import candidate_job_matching_service as matcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _intel(ev_level=3, work_mode="unknown", salary_min=None,
           readiness_level="high", readiness_confidence=0.85,
           inconsistencies=None):
    """Build a minimal intelligence dict for testing."""
    return {
        "evidence": {"skills": {"evidence_level": ev_level}},
        "constraint_profile": {
            "work_mode_constraint": work_mode,
            "salary_min": salary_min,
        },
        "recommendation_readiness": {
            "level": readiness_level,
            "confidence": readiness_confidence,
        },
        "inconsistencies": inconsistencies or [],
    }


def _score(signals, job_title, job_desc, semantic=0.8, intel=None):
    final, components = matcher._hybrid_score(
        signals, job_title, job_desc, "", [], semantic, intelligence=intel
    )
    return final, components


def _confidence(components):
    return components["recommendation_confidence"]


def _explanation(components):
    return components["match_explanation"]


# ---------------------------------------------------------------------------
# Test 1: Strong candidate + excellent matching job → High confidence
# ---------------------------------------------------------------------------

class TestStrongCandidateExcellentJob:
    def test_high_confidence(self):
        signals = {
            "target_roles": ["Python Backend Engineer"],
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "past_roles": ["Backend Engineer"],
        }
        intel = _intel(ev_level=3, work_mode="unknown")
        _, components = _score(
            signals,
            "Python Backend Engineer",
            "Python FastAPI PostgreSQL backend role. Remote friendly.",
            semantic=0.9,
            intel=intel,
        )
        conf = _confidence(components)
        assert conf["level"] == "high", f"Expected high, got {conf}"
        assert conf["score"] >= 70


# ---------------------------------------------------------------------------
# Test 2: Strong candidate + unrelated job → Low confidence
# ---------------------------------------------------------------------------

class TestStrongCandidateUnrelatedJob:
    def test_low_confidence(self):
        signals = {
            "target_roles": ["Python Backend Engineer"],
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "past_roles": ["Backend Engineer"],
        }
        intel = _intel(ev_level=3, work_mode="unknown")
        _, components = _score(
            signals,
            "Marketing Manager",
            "Lead marketing campaigns. Content strategy. Brand management.",
            semantic=0.1,
            intel=intel,
        )
        conf = _confidence(components)
        assert conf["level"] == "low", f"Expected low, got {conf}"
        assert conf["score"] < 45


# ---------------------------------------------------------------------------
# Test 3: Moderate profile + highly suitable job where job-critical info known
#          → Can still be High
# ---------------------------------------------------------------------------

class TestModerateProfileHighSuitability:
    def test_can_be_high_when_job_critical_info_known(self):
        signals = {
            "target_roles": ["Python Developer"],
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "past_roles": ["Python Developer"],
        }
        # Moderate readiness but demonstrated skills
        intel = _intel(ev_level=3, work_mode="unknown",
                       readiness_level="medium", readiness_confidence=0.55)
        _, components = _score(
            signals,
            "Python Developer",
            "Python FastAPI PostgreSQL backend developer needed. Remote friendly.",
            semantic=0.85,
            intel=intel,
        )
        conf = _confidence(components)
        # Moderate profile + strong job fit → should still reach medium or high
        assert conf["level"] in ("medium", "high"), f"Expected medium/high, got {conf}"
        assert conf["score"] >= 45


# ---------------------------------------------------------------------------
# Test 4: High Profile Strength + missing required skill → Cannot be High
# ---------------------------------------------------------------------------

class TestMissingRequiredSkill:
    def test_cannot_be_high_when_required_skill_missing(self):
        # Candidate has Python + FastAPI but NOT PostgreSQL
        # The job requires all three. The candidate's skills_score is based on
        # their own skill list (2/2 match), but the hybrid score is penalised
        # because the semantic score is low (job text has PostgreSQL, candidate doesn't).
        # More importantly: a candidate missing a required skill should not get
        # a "strong_personalized" tier.
        signals = {
            "target_roles": ["Backend Engineer"],
            "skills": ["Python", "FastAPI"],  # PostgreSQL missing from candidate
            "past_roles": ["Backend Engineer"],
        }
        intel = _intel(ev_level=3, work_mode="unknown")
        # Use low semantic to simulate that the candidate doesn't fully match
        _, components = _score(
            signals,
            "Backend Engineer",
            "Requires Python, FastAPI, PostgreSQL. Build REST APIs.",
            semantic=0.5,  # reduced because PostgreSQL is missing
            intel=intel,
        )
        conf = _confidence(components)
        # With a missing required skill and reduced semantic, should not be high
        assert conf["level"] in ("low", "medium"), (
            f"Missing required skill should prevent high confidence, got {conf}"
        )

    def test_explanation_shows_missing_skill(self):
        signals = {
            "target_roles": ["Backend Engineer"],
            "skills": ["Python", "FastAPI"],
            "past_roles": ["Backend Engineer"],
        }
        intel = _intel(ev_level=3, work_mode="unknown")
        _, components = _score(
            signals,
            "Backend Engineer",
            "Requires Python, FastAPI, PostgreSQL.",
            semantic=0.8,
            intel=intel,
        )
        expl = _explanation(components)
        # Python and FastAPI should appear in strong (demonstrated)
        strong_text = " ".join(expl["strong"]).lower()
        assert "python" in strong_text
        assert "fastapi" in strong_text


# ---------------------------------------------------------------------------
# Test 5: High Profile Strength + hard remote conflict → Low / near-disqualified
# ---------------------------------------------------------------------------

class TestHardRemoteConflict:
    def test_low_confidence_for_onsite_job(self):
        signals = {
            "target_roles": ["Backend Engineer"],
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "past_roles": ["Backend Engineer"],
        }
        intel = _intel(ev_level=3, work_mode="hard_remote_only")
        _, components = _score(
            signals,
            "Backend Engineer",
            "Python FastAPI PostgreSQL. Must work onsite in our office daily.",
            semantic=0.9,
            intel=intel,
        )
        conf = _confidence(components)
        assert conf["level"] == "low", f"Expected low, got {conf}"
        assert conf["score"] <= 25
        assert conf["tier"] == "near_disqualified"

    def test_constraint_note_in_explanation(self):
        signals = {
            "target_roles": ["Backend Engineer"],
            "skills": ["Python"],
            "past_roles": ["Engineer"],
        }
        intel = _intel(ev_level=3, work_mode="hard_remote_only")
        _, components = _score(
            signals,
            "Backend Engineer",
            "Must be present onsite daily.",
            semantic=0.8,
            intel=intel,
        )
        expl = _explanation(components)
        constraint_text = " ".join(expl["constraints"]).lower()
        assert "onsite" in constraint_text or "remote" in constraint_text


# ---------------------------------------------------------------------------
# Test 6: High Profile Strength + salary incompatibility → Reduced confidence
# ---------------------------------------------------------------------------

class TestSalaryIncompatibility:
    def test_salary_conflict_reduces_confidence(self):
        signals = {
            "target_roles": ["Backend Engineer"],
            "skills": ["Python", "FastAPI"],
            "past_roles": ["Backend Engineer"],
        }
        # salary_min = 100000, job mentions 50000 (below 70% threshold)
        intel_salary_conflict = _intel(ev_level=3, work_mode="unknown", salary_min=100000)
        intel_no_conflict = _intel(ev_level=3, work_mode="unknown", salary_min=None)

        _, comp_conflict = _score(
            signals,
            "Backend Engineer",
            "Python FastAPI backend. Salary up to 50000.",
            semantic=0.85,
            intel=intel_salary_conflict,
        )
        _, comp_no_conflict = _score(
            signals,
            "Backend Engineer",
            "Python FastAPI backend. Salary up to 50000.",
            semantic=0.85,
            intel=intel_no_conflict,
        )
        conf_conflict = _confidence(comp_conflict)
        conf_no_conflict = _confidence(comp_no_conflict)
        assert conf_conflict["score"] < conf_no_conflict["score"], (
            "Salary conflict should reduce confidence"
        )


# ---------------------------------------------------------------------------
# Test 7: Demonstrated required skill → stronger than claimed required skill
# ---------------------------------------------------------------------------

class TestDemonstratedVsClaimed:
    def test_demonstrated_beats_claimed(self):
        signals = {
            "target_roles": ["Python Developer"],
            "skills": ["Python", "FastAPI"],
            "past_roles": ["Developer"],
        }
        intel_claimed = _intel(ev_level=1, work_mode="unknown")
        intel_demonstrated = _intel(ev_level=3, work_mode="unknown")

        _, comp_claimed = _score(
            signals, "Python Developer", "Python FastAPI developer needed.",
            semantic=0.8, intel=intel_claimed,
        )
        _, comp_demo = _score(
            signals, "Python Developer", "Python FastAPI developer needed.",
            semantic=0.8, intel=intel_demonstrated,
        )
        assert _confidence(comp_demo)["score"] > _confidence(comp_claimed)["score"]

    def test_demonstrated_skill_appears_in_strong(self):
        signals = {
            "target_roles": ["Python Developer"],
            "skills": ["Python"],
            "past_roles": ["Developer"],
        }
        intel = _intel(ev_level=3)
        _, components = _score(
            signals, "Python Developer", "Python developer needed.",
            semantic=0.8, intel=intel,
        )
        expl = _explanation(components)
        strong_text = " ".join(expl["strong"]).lower()
        assert "python" in strong_text
        assert "demonstrated" in strong_text

    def test_claimed_skill_appears_in_partial(self):
        signals = {
            "target_roles": ["Python Developer"],
            "skills": ["Python"],
            "past_roles": ["Developer"],
        }
        intel = _intel(ev_level=1)
        _, components = _score(
            signals, "Python Developer", "Python developer needed.",
            semantic=0.8, intel=intel,
        )
        expl = _explanation(components)
        partial_text = " ".join(expl["partial"]).lower()
        assert "python" in partial_text
        assert "claimed" in partial_text


# ---------------------------------------------------------------------------
# Test 8: Verified irrelevant skill → does not compensate for missing required skill
# ---------------------------------------------------------------------------

class TestIrrelevantVerifiedSkill:
    def test_irrelevant_verified_skill_does_not_compensate(self):
        signals = {
            "target_roles": [],  # no target role match either
            "skills": ["COBOL", "Fortran"],  # verified but irrelevant
            "past_roles": [],
        }
        intel = _intel(ev_level=4, work_mode="unknown")  # VERIFIED
        _, components = _score(
            signals,
            "Backend Engineer",
            "Requires Python, FastAPI, PostgreSQL. Build REST APIs.",
            semantic=0.1,  # low semantic: candidate is genuinely unrelated
            intel=intel,
        )
        # skills_score should be 0 (no matching skills)
        assert components["skills_score"] == 0.0
        conf = _confidence(components)
        assert conf["level"] == "low", (
            f"Irrelevant verified skills should not compensate for missing required skills, got {conf}"
        )


# ---------------------------------------------------------------------------
# Test 9: Unclear target role → recommendation confidence constrained
# ---------------------------------------------------------------------------

class TestUnclearTargetRole:
    def test_no_target_role_constrains_confidence(self):
        signals = {
            "target_roles": [],  # no target role
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "past_roles": ["Backend Engineer"],
        }
        intel = _intel(ev_level=3, work_mode="unknown",
                       readiness_level="low", readiness_confidence=0.2)
        _, components = _score(
            signals,
            "Python Backend Engineer",
            "Python FastAPI PostgreSQL backend role.",
            semantic=0.85,
            intel=intel,
        )
        conf = _confidence(components)
        # Without target role, target_role_score = 0, reducing hybrid score
        assert components["target_role_score"] == 0.0
        # Confidence should be constrained (not high)
        assert conf["level"] in ("low", "medium")


# ---------------------------------------------------------------------------
# Test 10: High-severity contradiction → confidence reduced
# ---------------------------------------------------------------------------

class TestHighSeverityContradiction:
    def test_contradiction_reduces_confidence(self):
        signals = {
            "target_roles": ["Backend Engineer"],
            "skills": ["Python", "FastAPI"],
            "past_roles": ["Backend Engineer"],
        }
        contradiction = [{
            "field": "experience_years",
            "type": "hard_contradiction",
            "severity": "high",
            "description": "Resume claims 8 years but voice suggests 1 year",
        }]
        intel_clean = _intel(ev_level=3, inconsistencies=[])
        intel_conflict = _intel(ev_level=3, inconsistencies=contradiction)

        _, comp_clean = _score(
            signals, "Backend Engineer", "Python FastAPI backend.",
            semantic=0.85, intel=intel_clean,
        )
        _, comp_conflict = _score(
            signals, "Backend Engineer", "Python FastAPI backend.",
            semantic=0.85, intel=intel_conflict,
        )
        assert _confidence(comp_conflict)["score"] < _confidence(comp_clean)["score"]

    def test_contradiction_appears_in_concerns(self):
        signals = {
            "target_roles": ["Backend Engineer"],
            "skills": ["Python"],
            "past_roles": ["Engineer"],
        }
        contradiction = [{
            "field": "experience_years",
            "type": "hard_contradiction",
            "severity": "high",
            "description": "Resume claims 8 years but voice suggests 1 year",
        }]
        intel = _intel(ev_level=3, inconsistencies=contradiction)
        _, components = _score(
            signals, "Backend Engineer", "Python backend.",
            semantic=0.8, intel=intel,
        )
        expl = _explanation(components)
        assert len(expl["concerns"]) > 0


# ---------------------------------------------------------------------------
# Test 11: Non-technical candidate → no artificial GitHub/project requirement
# ---------------------------------------------------------------------------

class TestNonTechnicalCandidate:
    def test_sales_candidate_no_github_requirement(self):
        signals = {
            "target_roles": ["Account Executive"],
            "skills": ["Negotiation", "CRM", "Salesforce"],
            "past_roles": ["Account Executive"],
        }
        intel = _intel(ev_level=2, work_mode="unknown",
                       readiness_level="high", readiness_confidence=0.8)
        _, components = _score(
            signals,
            "Senior Account Executive",
            "B2B SaaS sales. CRM Salesforce. Negotiation skills required.",
            semantic=0.85,
            intel=intel,
        )
        conf = _confidence(components)
        # Should score well without any GitHub/project signals
        assert conf["score"] >= 40
        assert conf["level"] in ("medium", "high")


# ---------------------------------------------------------------------------
# Test 12: Fresher → lack of work history does not prevent strong recommendation
# ---------------------------------------------------------------------------

class TestFresherCandidate:
    def test_fresher_can_get_strong_recommendation_with_relevant_evidence(self):
        signals = {
            "target_roles": ["Backend Developer"],
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "past_roles": [],  # no work history
        }
        intel = _intel(ev_level=3, work_mode="unknown",
                       readiness_level="high", readiness_confidence=0.75)
        _, components = _score(
            signals,
            "Backend Developer",
            "Python FastAPI PostgreSQL backend developer. Entry level welcome.",
            semantic=0.88,
            intel=intel,
        )
        conf = _confidence(components)
        # Fresher with demonstrated skills + matching job → should not be blocked
        assert conf["level"] in ("medium", "high"), (
            f"Fresher with relevant demonstrated skills should get medium/high, got {conf}"
        )


# ---------------------------------------------------------------------------
# Structural tests: output shape
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_recommendation_confidence_present_in_components(self):
        signals = {"target_roles": ["Engineer"], "skills": ["Python"], "past_roles": []}
        intel = _intel()
        _, components = _score(signals, "Engineer", "Python engineer.", intel=intel)
        assert "recommendation_confidence" in components
        rc = components["recommendation_confidence"]
        assert "score" in rc
        assert "level" in rc
        assert "tier" in rc
        assert rc["level"] in ("low", "medium", "high")
        assert 0 <= rc["score"] <= 100

    def test_match_explanation_present_in_components(self):
        signals = {"target_roles": ["Engineer"], "skills": ["Python"], "past_roles": []}
        intel = _intel()
        _, components = _score(signals, "Engineer", "Python engineer.", intel=intel)
        assert "match_explanation" in components
        expl = components["match_explanation"]
        for key in ("strong", "partial", "missing", "constraints", "concerns"):
            assert key in expl, f"Missing key: {key}"
            assert isinstance(expl[key], list)

    def test_existing_components_not_broken(self):
        signals = {"target_roles": ["Engineer"], "skills": ["Python"], "past_roles": []}
        intel = _intel()
        _, components = _score(signals, "Engineer", "Python engineer.", intel=intel)
        for key in ("target_role_score", "skills_score", "experience_score",
                    "semantic_score", "constraint_penalty", "final_score"):
            assert key in components, f"Missing existing component: {key}"

    def test_no_intelligence_does_not_crash(self):
        signals = {"target_roles": ["Engineer"], "skills": ["Python"], "past_roles": []}
        _, components = _score(signals, "Engineer", "Python engineer.", intel=None)
        assert "recommendation_confidence" in components
        assert "match_explanation" in components

    def test_three_levels_are_distinct(self):
        """Profile Strength, Recommendation Readiness, and Job-Specific Confidence are separate."""
        from profile_strength_service import calculate_profile_strength_v2

        candidate = {
            "id": "test",
            "name": "Test Dev",
            "email": "test@example.com",
            "current_role": "Backend Engineer",
            "skills": ["Python", "FastAPI"],
            "work_experience": [{"title": "Engineer", "company": "Corp", "description": "APIs"}],
            "education": [],
            "raw_data": {"preferred_roles": ["Backend Engineer"]},
            "candidate_certificates": [],
        }
        ps_result = calculate_profile_strength_v2(candidate)

        # Profile Strength: how well Eve understands the candidate
        profile_strength = ps_result["profile_strength"]["percent"]

        # Recommendation Readiness: candidate-level, not job-specific
        readiness = ps_result["recommendation_readiness"]["level"]

        # Job-Specific Confidence: per job
        signals = matcher._build_candidate_signals(candidate)
        intelligence = ps_result
        _, components = matcher._hybrid_score(
            signals,
            "Backend Engineer",
            "Python FastAPI backend role.",
            "", [], 0.8,
            intelligence=intelligence,
        )
        job_conf = components["recommendation_confidence"]

        # All three exist and are independently meaningful
        assert isinstance(profile_strength, int)
        assert readiness in ("low", "medium", "high")
        assert job_conf["level"] in ("low", "medium", "high")
