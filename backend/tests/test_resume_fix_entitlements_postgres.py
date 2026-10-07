"""PostgreSQL integration tests for resume-fix entitlements."""
import asyncio
import os
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
if os.environ.get("POSTGRES_TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = os.environ["POSTGRES_TEST_DATABASE_URL"]
if not os.environ.get("DATABASE_URL"):
    pytest.skip("PostgreSQL integration tests require DATABASE_URL", allow_module_level=True)
import server


@pytest.fixture(scope="module")
def db():
    engine = create_async_engine(server.DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        asyncio.run(_ping(factory))
    except Exception as exc:
        asyncio.run(engine.dispose())
        pytest.skip(f"PostgreSQL is not available: {exc}")
    yield factory
    asyncio.run(engine.dispose())


async def _ping(factory):
    async with factory() as s:
        await s.execute(text("SELECT 1"))


@pytest.fixture
def data(db):
    ids = {k: str(uuid.uuid4()) for k in ("a", "b", "j1", "j2", "r1", "r2", "r3")}

    async def create():
        await server._ensure_schema()
        async with db() as s:
            await s.execute(text("""INSERT INTO candidates (id, name, email, created_at, updated_at)
                VALUES (:a, 'Resume Fix Test A', :ea, now(), now()),
                       (:b, 'Resume Fix Test B', :eb, now(), now())"""),
                            {"a": ids["a"], "b": ids["b"], "ea": f"{ids['a']}@test.invalid", "eb": f"{ids['b']}@test.invalid"})
            await s.execute(text("""INSERT INTO job_descriptions (id, title, company_name, description, is_active, created_at, updated_at)
                VALUES (:j1, 'Test Job A', 'Integration Co', 'test', true, now(), now()),
                       (:j2, 'Test Job B', 'Integration Co', 'test', true, now(), now())"""), ids)
            await s.execute(text("""INSERT INTO candidate_job_recommendations
                (id, candidate_id, job_id, match_score, recommendation_rank, created_at, generated_at)
                VALUES (:r1, :a, :j1, 1, 1, now(), now()), (:r2, :a, :j2, 1, 1, now(), now()),
                       (:r3, :b, :j1, 1, 1, now(), now())"""), ids)
            await s.commit()
    asyncio.run(create())
    yield ids

    async def remove():
        async with db() as s:
            await s.execute(text("DELETE FROM candidates WHERE id IN (:a, :b)"), ids)
            await s.commit()
    asyncio.run(remove())


def call(fn, *args):
    return asyncio.run(fn(*args))


def counts(db, ids):
    async def run():
        async with db() as s:
            return (await s.execute(text("""SELECT
                (SELECT count(*) FROM candidate_resume_fix_entitlements WHERE candidate_id=:a),
                (SELECT count(*) FROM candidate_resume_fix_credit_claims WHERE candidate_id=:a AND claim_kind='charged'),
                (SELECT starter_credits_remaining FROM candidate_resume_fix_credit_balances WHERE candidate_id=:a)"""), ids)).one()
    return asyncio.run(run())


def test_entitlements_are_job_and_candidate_scoped_and_reused(db, data):
    first = call(server._claim_resume_fix_entitlement, data["a"], {}, data["r1"], date.today())
    repeat = call(server._claim_resume_fix_entitlement, data["a"], {}, data["r1"], date.today())
    call(server._claim_resume_fix_entitlement, data["a"], {}, data["r2"], date.today())
    call(server._claim_resume_fix_entitlement, data["b"], {}, data["r3"], date.today())
    assert first["claim_id"] != repeat["claim_id"]
    assert counts(db, data) == (2, 1, 94)


def test_concurrent_first_request_has_one_charge(db, data):
    async def run():
        return await asyncio.gather(*[server._claim_resume_fix_entitlement(data["a"], {}, data["r1"], date.today()) for _ in range(2)])
    results = asyncio.run(run())
    assert len({r["claim_id"] for r in results}) == 2
    assert counts(db, data) == (1, 1, 97)


@pytest.mark.parametrize("failure_sql", ["candidate_resume_fix_entitlements", "candidate_resume_fix_credit_claims"])
def test_claim_transaction_rolls_back(db, data, monkeypatch, failure_sql):
    original = server.SessionLocal

    class FailingSession:
        def __init__(self): self.session = original()
        async def __aenter__(self): await self.session.__aenter__(); return self
        async def __aexit__(self, *args): return await self.session.__aexit__(*args)
        async def execute(self, statement, params=None):
            if failure_sql in str(statement) and "INSERT INTO" in str(statement):
                raise RuntimeError("forced integration failure")
            return await self.session.execute(statement, params)
        async def commit(self): return await self.session.commit()
    monkeypatch.setattr(server, "SessionLocal", FailingSession)
    with pytest.raises(RuntimeError):
        call(server._claim_resume_fix_entitlement, data["a"], {}, data["r1"], date.today())
    assert counts(db, data) == (0, 0, None)


def test_save_validation_schema_idempotence_and_active_lock(db, data):
    claim = call(server._claim_resume_fix_entitlement, data["a"], {}, data["r1"], date.today())["claim_id"]
    call(server._ensure_schema)
    call(server._ensure_schema)
    call(server._validate_resume_fix_credit_claim, data["a"], {}, claim, data["r1"])
    with pytest.raises(server.HTTPException):
        call(server._validate_resume_fix_credit_claim, data["a"], {}, claim, data["r1"])
    with pytest.raises(server.HTTPException):
        call(server._validate_resume_fix_credit_claim, data["b"], {}, claim, data["r1"])

    async def verify():
        async with db() as s:
            unique = (await s.execute(text("""
                SELECT 1 FROM pg_constraint
                WHERE conrelid='candidate_resume_fix_entitlements'::regclass
                  AND contype='u' AND pg_get_constraintdef(oid) LIKE '%candidate_id, job_id%'
            """))).first()
            lock = await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('resume-fix-test'))"))
            preserved = (await s.execute(text("SELECT count(*) FROM candidate_resume_fix_credit_claims WHERE id=:id"), {"id": claim})).scalar()
            return unique, lock, preserved
    unique, lock, preserved = asyncio.run(verify())
    assert unique is not None and lock is not None and preserved == 1


def test_editor_repeat_claims_are_single_use_and_job_scoped(db, data):
    """A charged claim is consumed once; each intentional repeat gets a new claim."""
    first = call(server._claim_resume_fix_entitlement, data["a"], {}, data["r1"], date.today())["claim_id"]
    call(server._validate_resume_fix_credit_claim, data["a"], {}, first, data["r1"])
    second = call(server._claim_resume_fix_entitlement, data["a"], {}, data["r1"], date.today())["claim_id"]
    assert second != first
    call(server._validate_resume_fix_credit_claim, data["a"], {}, second, data["r1"])
    third = call(server._claim_resume_fix_entitlement, data["a"], {}, data["r1"], date.today())["claim_id"]
    assert third not in {first, second}
    call(server._validate_resume_fix_credit_claim, data["a"], {}, third, data["r1"])
    with pytest.raises(server.HTTPException):
        call(server._validate_resume_fix_credit_claim, data["a"], {}, second, data["r1"])
    with pytest.raises(server.HTTPException):
        call(server._validate_resume_fix_credit_claim, data["b"], {}, third, data["r1"])
