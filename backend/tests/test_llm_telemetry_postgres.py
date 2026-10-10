"""Real-PostgreSQL coverage for LLM request-to-candidate correlation.

This suite never falls back to DATABASE_URL.  It needs an explicitly supplied,
local, dedicated POSTGRES_TEST_DATABASE_URL and isolates each test in a newly
created schema that is dropped during teardown.
"""
import asyncio
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


TEST_DATABASE_URL = os.environ.get("POSTGRES_TEST_DATABASE_URL")


def _safe_test_url(value: str | None) -> bool:
    """Accept only an explicitly named local PostgreSQL test database."""
    if not value:
        return False
    try:
        url = make_url(value)
    except Exception:
        return False
    database = (url.database or "").lower()
    host = (url.host or "").lower()
    return (
        url.drivername.startswith("postgresql")
        and host in {"localhost", "127.0.0.1", "::1"}
        and "test" in database
        and "railway" not in host
    )


if not _safe_test_url(TEST_DATABASE_URL):
    pytest.skip(
        "LLM PostgreSQL integration tests require a local, dedicated "
        "POSTGRES_TEST_DATABASE_URL whose database name contains 'test'",
        allow_module_level=True,
    )


BACKEND = Path(__file__).parents[1]
MIGRATIONS = BACKEND / "migrations"
REPORT_SQL = (BACKEND / "llm_usage_queries.sql").read_text(encoding="utf-8")
CANDIDATE_REPORT = REPORT_SQL[REPORT_SQL.index("-- Candidate-level report."):]


async def _apply_sql_file(conn, path: Path) -> None:
    # These migrations contain only ordinary semicolon-terminated statements.
    for statement in path.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            await conn.execute(text(statement))


async def _insert_event(conn, *, request_id, candidate_id=None, input_tokens=1,
                        output_tokens=2, total_tokens=3, is_retry=False,
                        previous_attempt_failed=False, is_fallback=False):
    await conn.execute(text("""
        INSERT INTO llm_usage_events
            (provider, model, workflow, candidate_id, request_id, attempt,
             success, input_tokens, output_tokens, total_tokens, total_attempts,
             is_retry, previous_attempt_failed, is_fallback)
        VALUES
            ('fixture-provider', 'fixture-model', 'resume', :candidate_id,
             :request_id, 1, TRUE, :input_tokens, :output_tokens, :total_tokens,
             1, :is_retry, :previous_attempt_failed, :is_fallback)
    """), {
        "request_id": request_id,
        "candidate_id": candidate_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "is_retry": is_retry,
        "previous_attempt_failed": previous_attempt_failed,
        "is_fallback": is_fallback,
    })


@pytest.fixture
def postgres():
    """A fresh schema on a connection string that has passed the safety gate."""
    async def create():
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        schema = "llm_telemetry_it_" + uuid.uuid4().hex
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await _apply_sql_file(conn, MIGRATIONS / "20261008_llm_usage_events.sql")
            await _apply_sql_file(conn, MIGRATIONS / "20261010_llm_usage_event_correlation.sql")
            await conn.execute(text("CREATE TABLE candidate_persistence (id TEXT PRIMARY KEY)"))
        return engine, schema

    engine, schema = asyncio.run(create())
    try:
        yield engine, schema
    finally:
        async def destroy():
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await engine.dispose()
        asyncio.run(destroy())


async def _in_schema(engine, schema, callback):
    async with engine.begin() as conn:
        await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        return await callback(conn)


def test_migrations_apply_and_enforce_schema(postgres):
    engine, schema = postgres

    async def verify(conn):
        columns = (await conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'llm_usage_events'
        """))).scalars().all()
        primary_key = (await conn.execute(text("""
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'llm_usage_request_candidate_links'::regclass
              AND contype = 'p'
        """))).scalar()
        with pytest.raises(Exception):
            async with conn.begin_nested():
                await conn.execute(text("""
                    INSERT INTO llm_usage_request_candidate_links (request_id, candidate_id)
                    VALUES ('missing-candidate', NULL)
                """))
        assert {"session_id", "vapi_call_id", "is_fallback"} <= set(columns)
        assert primary_key == 1

    asyncio.run(_in_schema(engine, schema, verify))


def test_event_before_and_after_association_and_conflict(postgres):
    engine, schema = postgres

    async def verify(conn):
        from llm_telemetry import _associate_candidate_on_connection, _reconcile_events_for_request

        await _insert_event(conn, request_id="opaque-before")
        await _associate_candidate_on_connection(conn, "opaque-before", "candidate-a")
        await _associate_candidate_on_connection(conn, "opaque-after", "candidate-b")
        await _insert_event(conn, request_id="opaque-after")
        await _reconcile_events_for_request(conn, "opaque-after")
        await _associate_candidate_on_connection(conn, "opaque-before", "candidate-b")
        rows = (await conn.execute(text("""
            SELECT request_id, candidate_id FROM llm_usage_events ORDER BY request_id
        """))).all()
        link = (await conn.execute(text("""
            SELECT candidate_id FROM llm_usage_request_candidate_links
            WHERE request_id = 'opaque-before'
        """))).scalar()
        assert rows == [("opaque-after", "candidate-b"), ("opaque-before", "candidate-a")]
        assert link == "candidate-a"

    asyncio.run(_in_schema(engine, schema, verify))


def test_savepoint_failure_and_outer_rollback(postgres):
    engine, schema = postgres

    async def verify(conn):
        from llm_telemetry import associate_candidate_in_transaction

        class FailingAssociation:
            def begin_nested(self):
                return conn.begin_nested()

            async def execute(self, statement, params=None):
                if "INSERT INTO llm_usage_request_candidate_links" in str(statement):
                    raise RuntimeError("forced association failure")
                return await conn.execute(statement, params)

        await associate_candidate_in_transaction(FailingAssociation(), "savepoint-fail", "candidate-savepoint")
        await conn.execute(text("INSERT INTO candidate_persistence (id) VALUES ('candidate-savepoint')"))
        assert (await conn.execute(text("SELECT count(*) FROM llm_usage_request_candidate_links"))).scalar() == 0

    asyncio.run(_in_schema(engine, schema, verify))

    async def committed_candidate(conn):
        assert (await conn.execute(text("""
            SELECT count(*) FROM candidate_persistence WHERE id='candidate-savepoint'
        """))).scalar() == 1

    asyncio.run(_in_schema(engine, schema, committed_candidate))

    async def rollback_case():
        from llm_telemetry import associate_candidate_in_transaction
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await conn.execute(text("INSERT INTO candidate_persistence (id) VALUES ('candidate-rollback')"))
                await associate_candidate_in_transaction(conn, "outer-rollback", "candidate-rollback")
            finally:
                await transaction.rollback()
        async with engine.begin() as conn:
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            assert (await conn.execute(text("SELECT count(*) FROM candidate_persistence WHERE id='candidate-rollback'"))).scalar() == 0
            assert (await conn.execute(text("SELECT count(*) FROM llm_usage_request_candidate_links WHERE request_id='outer-rollback'"))).scalar() == 0

    asyncio.run(rollback_case())


def test_candidate_report_preserves_unknown_tokens_and_null_request_count(postgres):
    engine, schema = postgres

    async def verify(conn):
        await _insert_event(conn, request_id="report-request", candidate_id="candidate-report", input_tokens=10, output_tokens=20, total_tokens=30)
        await _insert_event(conn, request_id="report-request", candidate_id="candidate-report", input_tokens=3, output_tokens=4, total_tokens=7, is_retry=True, previous_attempt_failed=True, is_fallback=True)
        await _insert_event(conn, request_id=None, candidate_id="candidate-report", input_tokens=1, output_tokens=2, total_tokens=None)
        row = (await conn.execute(text(CANDIDATE_REPORT), {
            "start_at": "2000-01-01T00:00:00+00:00",
            "end_at": "2100-01-01T00:00:00+00:00",
        })).mappings().one()
        assert row["provider_attempts"] == 3
        assert row["logical_requests"] == 1
        assert row["uncorrelated_attempts"] == 1
        assert row["retry_attempts"] == 1
        assert row["fallback_attempts"] == 1
        assert row["unknown_token_attempts"] == 1
        assert (row["input_tokens"], row["output_tokens"], row["total_tokens"]) == (14, 26, 37)

    asyncio.run(_in_schema(engine, schema, verify))
