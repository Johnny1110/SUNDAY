#!/usr/bin/env bash
# Smoke-test a RUNNING Sunday engine — validates the milestone-1.0 + milestone-3
# HTTP contract directly (no swarm needed; curls Sunday on :7777).
#
#   ./scripts/smoke.sh [base_url]
#
# Exit non-zero if any check fails. Curling Sunday directly bypasses the evva
# permission gate (that gate is on the agent's bash tool, not on Sunday).
set -u
BASE=${1:-http://127.0.0.1:7777}
pass=0 fail=0

ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $1"; fail=$((fail+1)); }

# code URL -> echoes HTTP status
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

echo "== read endpoints (expect 200) =="
for ep in "/status" "/signals?symbol=BTCUSDT" "/market?symbol=BTCUSDT&tf=1h&limit=50" \
          "/positions" "/pnl" "/manual" "/strategy/outcomes?symbol=BTCUSDT"; do
  c=$(code "$BASE$ep")
  [ "$c" = "200" ] && ok "GET $ep" || bad "GET $ep (got $c)"
done

echo "== /signals shape (decision panel) =="
sig=$(curl -s "$BASE/signals?symbol=BTCUSDT")
echo "$sig" | grep -q '"votes"' && echo "$sig" | grep -q '"regime"' \
  && ok "/signals has regime + votes" || bad "/signals shape"

echo "== /status legibility (M3-T1) =="
st=$(curl -s "$BASE/status")
echo "$st" | grep -q '"as_of_ts"' && ok "/status has as_of_ts" || bad "/status missing as_of_ts"

echo "== defensive /strategy (M3-T4) =="
# reason required -> 400
c=$(code -X POST "$BASE/strategy" -H 'Content-Type: application/json' \
      -d '{"symbol":"BTCUSDT","strategy":"flat"}')
[ "$c" = "400" ] && ok "missing reason -> 400" || bad "missing reason (got $c)"

# stale expected_current -> 409
c=$(code -X POST "$BASE/strategy" -H 'Content-Type: application/json' \
      -d '{"symbol":"BTCUSDT","strategy":"mean_reversion","reason":"x","expected_current":"__nope__"}')
[ "$c" = "409" ] && ok "stale expected_current -> 409" || bad "stale guard (got $c)"

# valid switch -> 200 + resulting_status
resp=$(curl -s -X POST "$BASE/strategy" -H 'Content-Type: application/json' \
      -d '{"symbol":"BTCUSDT","strategy":"flat","reason":"smoke test"}')
echo "$resp" | grep -q '"resulting_status"' && ok "valid switch returns resulting_status" \
  || bad "valid switch (no resulting_status): $resp"

echo "== /envelope lever (M1.1) =="
c=$(code -X POST "$BASE/envelope" -H 'Content-Type: application/json' -d '{"max_leverage":2}')
[ "$c" = "400" ] && ok "envelope missing reason -> 400" || bad "envelope reason guard (got $c)"
resp=$(curl -s -X POST "$BASE/envelope" -H 'Content-Type: application/json' \
      -d '{"reason":"smoke test","max_leverage":2}')
echo "$resp" | grep -q '"envelope"' && ok "envelope set returns resulting_status.envelope" \
  || bad "envelope set (no envelope): $resp"

echo
echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
