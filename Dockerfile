# syntax=docker/dockerfile:1
# trixie ships postgresql-client 17; pg_dump must be >= the server (16).
FROM python:3.12-slim-trixie AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: build tools for any wheels that need them; curl for healthchecks;
# postgresql-client for scripts/backup_db.sh (run by the scheduler service).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching). Copy only metadata so a
# code change doesn't bust the dependency layer.
COPY pyproject.toml ./
# Editable install needs the package dirs to exist at build time.
COPY . .
RUN pip install --upgrade pip && pip install ".[telegram,worker,billing,research,pdf]"

EXPOSE 5000

# Default command runs the Streamlit web app. The worker/bot override this in
# docker-compose.
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", "--server.port=5000", "--server.headless=true"]
