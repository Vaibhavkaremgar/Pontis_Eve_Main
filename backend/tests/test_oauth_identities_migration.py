import json
from pathlib import Path


BACKEND = Path(__file__).parents[1]
MIGRATIONS = BACKEND / "migrations"


def test_oauth_migrations_are_ordered_in_railway_predeploy():
    config = json.loads((BACKEND / "railway.json").read_text(encoding="utf-8"))
    commands = config["deploy"]["preDeployCommand"]
    migration_names = [
        "20261005_unified_candidate_intake.sql",
        "20261008_llm_usage_events.sql",
        "20261013_oauth_transactions.sql",
        "20261016_oauth_identities.sql",
    ]
    positions = [next(i for i, command in enumerate(commands) if name in command) for name in migration_names]
    assert positions == sorted(positions)
    assert all("ON_ERROR_STOP=1" in commands[position] for position in positions)


def test_oauth_identities_migration_has_safe_identity_constraints():
    sql = (MIGRATIONS / "20261016_oauth_identities.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS oauth_identities" in sql
    assert "id UUID PRIMARY KEY" in sql
    assert "candidate_id UUID NOT NULL REFERENCES candidates(id) ON DELETE CASCADE" in sql
    assert "UNIQUE (provider, provider_subject)" in sql
    assert "UNIQUE (candidate_id, provider)" in sql
    assert "CHECK (provider IN ('google', 'linkedin'))" in sql
    assert "DROP CONSTRAINT IF EXISTS oauth_identities_provider_check" in sql
    assert "ADD CONSTRAINT oauth_identities_provider_check" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_oauth_identities_candidate" in sql


def test_oauth_identity_migration_is_non_destructive_and_idempotent():
    sql = (MIGRATIONS / "20261016_oauth_identities.sql").read_text(encoding="utf-8").upper()
    assert "CREATE TABLE IF NOT EXISTS" in sql
    assert "DROP TABLE" not in sql
    assert "DELETE FROM" not in sql
    assert "TRUNCATE" not in sql
