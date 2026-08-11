import asyncio
from dotenv import load_dotenv
import os
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

load_dotenv()
engine = create_async_engine(os.environ["DATABASE_URL"])

async def run():
    async with engine.connect() as c:
        r = await c.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='candidates' ORDER BY ordinal_position"
        ))
        print("--- candidates ---")
        for row in r.fetchall():
            print(f"  {row[0]}: {row[1]}")

        r2 = await c.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='candidate_certificates' ORDER BY ordinal_position"
        ))
        print("\n--- candidate_certificates ---")
        for row in r2.fetchall():
            print(f"  {row[0]}: {row[1]}")

        # Sample one candidate row to see actual data shape
        r3 = await c.execute(text("SELECT * FROM candidates LIMIT 1"))
        row = r3.fetchone()
        if row:
            keys = r3.keys()
            print("\n--- sample candidate row keys ---")
            for k in keys:
                print(f"  {k}")
    await engine.dispose()

asyncio.run(run())
