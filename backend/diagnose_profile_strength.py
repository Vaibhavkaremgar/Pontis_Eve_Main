"""Read-only profile-strength diagnostic for a single persisted candidate.

Usage (from backend):
    python diagnose_profile_strength.py 6ab604d8-4867-4846-bc73-6b03b08b881f
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from profile_strength_service import build_profile_strength_diagnostic

CANDIDATE_ID_SQL = "SELECT * FROM candidates WHERE id = :candidate_id LIMIT 1"
PREFERENCES_SQL = "SELECT * FROM candidate_preferences WHERE candidate_id = :candidate_id LIMIT 1"
CERTIFICATES_SQL = (
    "SELECT id, file_name, file_path FROM candidate_certificates "
    "WHERE candidate_id = :candidate_id ORDER BY created_at ASC"
)

async def load_and_print(candidate_id: str) -> dict:
    load_dotenv(Path(__file__).parent / ".env")
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with async_sessionmaker(bind=engine, expire_on_commit=False)() as db:
            candidate_result = await db.execute(
                text(CANDIDATE_ID_SQL),
                {"candidate_id": candidate_id},
            )
            candidate = candidate_result.mappings().fetchone()
            if candidate is None:
                raise LookupError(f"Candidate not found: {candidate_id}")
            candidate_data = dict(candidate)
            preferences_result = await db.execute(
                text(PREFERENCES_SQL),
                {"candidate_id": candidate_id},
            )
            preferences = preferences_result.mappings().fetchone()
            certificates_result = await db.execute(
                text(CERTIFICATES_SQL),
                {"candidate_id": candidate_id},
            )
            candidate_data["candidate_certificates"] = [dict(row) for row in certificates_result.mappings().all()]
        diagnostic = build_profile_strength_diagnostic(candidate_data, prefs_row=dict(preferences) if preferences else None)
        print(json.dumps(diagnostic, indent=2, default=str, sort_keys=True))
        return diagnostic
    finally:
        await engine.dispose()


if __name__ == "__main__":
    requested_id = sys.argv[1] if len(sys.argv) > 1 else "6ab604d8-4867-4846-bc73-6b03b08b881f"
    asyncio.run(load_and_print(requested_id))
