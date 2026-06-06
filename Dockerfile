# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: build tools for any wheels that need them; curl for healthchecks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching). Copy only metadata so a
# code change doesn't bust the dependency layer.
COPY pyproject.toml ./
# Editable install needs the package dirs to exist at build time.
COPY . .
RUN pip install --upgrade pip && pip install ".[telegram]"

EXPOSE 5000

# Default command runs the Streamlit web app. The worker/bot override this in
# docker-compose.
CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", "--server.port=5000", "--server.headless=true"]
