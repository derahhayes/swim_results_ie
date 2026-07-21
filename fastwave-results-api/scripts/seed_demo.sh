#!/usr/bin/env bash
# Seeds a demo meet against whatever DATABASE_URL/DATABASE_URL_DIRECT are
# currently exported in the environment - run this from a local machine
# with *production* env vars exported to seed the Railway deploy's DB,
# not against your dev branch.
#
# Ingests the Michael Bowles 2026 fixture, publishes it, and prints the
# meet id so you can drill into it manually or feed it to scripts/smoke.sh.
#
# Usage:
#   export DATABASE_URL=postgresql+asyncpg://...        # production Neon, pooled
#   export DATABASE_URL_DIRECT=postgresql+psycopg2://... # production Neon, direct
#   ./scripts/seed_demo.sh [uploaded-by-email]

set -euo pipefail
cd "$(dirname "$0")/.."

UPLOADED_BY="${1:-demo@fastwaveresults.ie}"
FIXTURE="tests/fixtures/michael_bowles_2026.hy3"
MEET_NAME="Michael Bowles 2026.05.30"

if [ -z "${DATABASE_URL:-}" ] || [ -z "${DATABASE_URL_DIRECT:-}" ]; then
  echo "DATABASE_URL and DATABASE_URL_DIRECT must be exported (pointed at the" >&2
  echo "target environment) before running this - see the usage comment above." >&2
  exit 1
fi

echo "Ingesting $FIXTURE..."
uv run python -m app.ingestion.cli "$FIXTURE" --uploaded-by "$UPLOADED_BY"

MEET_ID=$(uv run python -m app.cli list-meets | grep "$MEET_NAME" | awk '{print $1}')

if [ -z "$MEET_ID" ]; then
  echo "Could not find \"$MEET_NAME\" in list-meets output after ingest - see output above." >&2
  exit 1
fi

echo "Publishing meet $MEET_ID..."
uv run python -m app.cli publish-meet "$MEET_ID"

echo ""
echo "Seeded meet id: $MEET_ID"
