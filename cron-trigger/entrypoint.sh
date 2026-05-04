#!/bin/sh
set -eu
# POST morning brief. Set CRON_TARGET_URL on the Railway cron service (full URL to endpoint).
# Optional: CRON_SECRET must match BotCore's CRON_SECRET if that env is set there.
URL="${CRON_TARGET_URL:-}"
if [ -z "$URL" ]; then
  echo "ERROR: CRON_TARGET_URL is not set (e.g. https://your-app.up.railway.app/api/cron/morning-market-brief)" >&2
  exit 1
fi

if [ -n "${CRON_SECRET:-}" ]; then
  exec curl -fsS -X POST "$URL" \
    -H "Content-Type: application/json" \
    -H "X-Cron-Secret: ${CRON_SECRET}" \
    -d "${CRON_BODY:-{}}"
else
  exec curl -fsS -X POST "$URL" \
    -H "Content-Type: application/json" \
    -d "${CRON_BODY:-{}}"
fi
