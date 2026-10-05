FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y poppler-utils postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "psql \"$DATABASE_URL\" -v ON_ERROR_STOP=1 -f /app/migrations/20261005_unified_candidate_intake.sql && exec uvicorn server:app --host 0.0.0.0 --port $PORT"]
