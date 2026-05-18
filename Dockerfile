FROM python:3.12-slim

WORKDIR /app

# Install build deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Install dependencies only — cached unless pyproject.toml changes
RUN pip install --no-cache-dir \
    restate-sdk crewai sqlalchemy alembic psycopg2-binary \
    python-dotenv cuid2 httpx starlette fastapi pusher uvicorn hypercorn \
    pytest pytest-asyncio pytest-timeout ruff

COPY src/ src/
COPY services/ services/
COPY frontend/ frontend/
COPY alembic/ alembic/
COPY alembic.ini .
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 9080
