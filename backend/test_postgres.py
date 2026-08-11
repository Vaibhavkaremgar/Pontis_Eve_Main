import asyncio
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_async_engine(DATABASE_URL)

async def test_connection():
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))
        print("PostgreSQL connection successful:", result.scalar())

    await engine.dispose()

asyncio.run(test_connection())