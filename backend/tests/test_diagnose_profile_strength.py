import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from diagnose_profile_strength import (
    CANDIDATE_ID_SQL,
    CERTIFICATES_SQL,
    PREFERENCES_SQL,
)


def test_diagnostic_looks_up_candidate_by_candidates_id_and_reuses_uuid():
    """The CLI UUID is the candidates.id value, not a candidates.candidate_id field."""
    assert "FROM candidates WHERE id = :candidate_id" in CANDIDATE_ID_SQL
    assert "candidate_id" not in CANDIDATE_ID_SQL.split("WHERE", 1)[1].split("=", 1)[0]
    assert ":candidate_id" in PREFERENCES_SQL
    assert ":candidate_id" in CERTIFICATES_SQL
