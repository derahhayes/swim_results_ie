#!/usr/bin/env bash
# Smoke-tests a running deployment: /healthz, /api/v1/meets, a meet's
# events, and one event's results. Exits non-zero on any non-200 response
# or an unexpectedly empty result set - safe to wire into a post-deploy CI
# step later, run manually for now.
#
# Usage: ./scripts/smoke.sh https://your-app.up.railway.app

set -euo pipefail

BASE_URL="${1:?Usage: $0 <base_url>}"
BASE_URL="${BASE_URL%/}"
BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT

fail() {
  echo "SMOKE TEST FAILED: $1" >&2
  exit 1
}

# Curls a URL, prints the body to stdout, fails loudly on a non-200.
check_200() {
  local url="$1"
  local status
  status=$(curl -s -o "$BODY_FILE" -w "%{http_code}" "$url")
  if [ "$status" != "200" ]; then
    echo "  -> HTTP $status" >&2
    cat "$BODY_FILE" >&2
    fail "$url did not return 200"
  fi
  cat "$BODY_FILE"
}

echo "1. GET $BASE_URL/healthz"
body=$(check_200 "$BASE_URL/healthz")
echo "$body" | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert d.get('status') == 'ok', 'status not ok'
assert d.get('db') is True, 'db not connected'
" || fail "/healthz did not report a healthy DB"
echo "   OK"

echo "2. GET $BASE_URL/api/v1/meets"
body=$(check_200 "$BASE_URL/api/v1/meets")
meet_id=$(echo "$body" | python3 -c "
import json, sys
d = json.load(sys.stdin)
items = d.get('items') or []
assert items, 'no published meets returned'
print(items[0]['id'])
") || fail "/api/v1/meets returned no items - is a meet published?"
echo "   OK (meet: $meet_id)"

echo "3. GET $BASE_URL/api/v1/meets/$meet_id"
body=$(check_200 "$BASE_URL/api/v1/meets/$meet_id")
event_id=$(echo "$body" | python3 -c "
import json, sys
d = json.load(sys.stdin)
events = d.get('events') or []
assert events, 'meet has no events'
print(events[0]['id'])
") || fail "meet $meet_id has no events"
echo "   OK (event: $event_id)"

echo "4. GET $BASE_URL/api/v1/events/$event_id/results"
body=$(check_200 "$BASE_URL/api/v1/events/$event_id/results")
echo "$body" | python3 -c "
import json, sys
d = json.load(sys.stdin)
rounds = d.get('rounds') or []
assert rounds, 'no rounds returned'
assert any(r.get('results') for r in rounds), 'all rounds are empty'
" || fail "event $event_id has no results"
echo "   OK"

echo ""
echo "All smoke checks passed against $BASE_URL"
