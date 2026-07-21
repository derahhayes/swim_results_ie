# Explicit Dockerfile instead of relying on Railway's Nixpacks/Railpack
# Python+uv auto-detection - that auto-detection hit two different failures
# across two consecutive deploys (a build-context path issue, then
# `pip install uv==` with an empty auto-detected version), the second of
# which was non-deterministic: the exact same auto-detection had produced a
# valid `uv==0.4.30` on the previous deploy. Pinning the uv version here via
# astral's own recommended Docker pattern removes that whole failure class.
FROM python:3.12-slim

WORKDIR /app

# Official astral-sh pattern for getting a pinned uv binary into an image
# without a network-dependent `pip install uv==<version>` step.
COPY --from=ghcr.io/astral-sh/uv:0.4.30 /uv /uvx /usr/local/bin/

# vendor/hytek-parser (see pyproject.toml's [tool.uv.sources]) is part of
# this same build context - COPY . . picks it up, no separate step needed.
COPY . .

RUN uv sync --frozen

EXPOSE 8000

CMD ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
