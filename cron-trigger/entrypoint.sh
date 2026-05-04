#!/bin/sh
set -eu
# POST morning brief. Override CRON_TARGET_URL on the Railway cron service if your BotCore URL differs.
# Optional: CRON_SECRET must match BotCore's CRON_SECRET when that env is set on BotCore.
DEFAULT_URL="https://botcore-production.up.railway.app/api/cron/morning-market-brief"
URL="${CRON_TARGET_URL:-$DEFAULT_URL}"

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
