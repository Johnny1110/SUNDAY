#!/usr/bin/env bash
# Smoke-test a RUNNING Sunday engine — validates the milestone-4/5 HTTP contract
# directly (no swarm needed; curls Sunday on :7777).
#
#   ./scripts/smoke.sh [base_url]
#
# Exit non-zero if any check fails. Curling Sunday directly bypasses the evva
# permission gate (that gate is on the agent's http_request tool, not on Sunday).
# Assumes a running Sunday with testnet keys; only mutations are flat/envelope (safe).
set -u
BASE=${1:-http://127.0.0.1:7777}
pass=0 fail=0

ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $1"; fail=$((fail+1)); }

# code URL -> echoes HTTP status
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

echo "== read endpoints (expect 200) =="
for ep in "/status" "/desk" "/desk?symbol=BTCUSDT" "/advisor?symbol=BTCUSDT" \
          "/market?symbol=BTCUSDT&tf=1h&limit=50" "/positions" "/pnl" "/risk" \
          "/thesis?symbol=BTCUSDT" "/theses?limit=5" "/ablation" "/performance" "/manual"; do
  c=$(code "$BASE$ep")
  [ "$c" = "200" ] && ok "GET $ep" || bad "GET $ep (got $c)"
done

echo "== /desk basket shape (milestone-4 'where to look') =="
desk=$(curl -s "$BASE/desk")
echo "$desk" | grep -q '"basket"' && ok "/desk has basket" || bad "/desk shape"

echo "== /advisor decision panel shape (regime + votes) =="
adv=$(curl -s "$BASE/advisor?symbol=BTCUSDT")
echo "$adv" | grep -q '"votes"' && echo "$adv" | grep -q '"regime"' \
  && ok "/advisor has regime + votes" || bad "/advisor shape"

echo "== /status basket-aware (milestone-5) =="
st=$(curl -s "$BASE/status")
echo "$st" | grep -q '"basket"' && ok "/status has basket" || bad "/status missing basket"
echo "$st" | grep -q '"swarm_heartbeat_ok"' && ok "/status has swarm_heartbeat_ok" || bad "/status missing swarm_heartbeat_ok"

echo "== defensive /strategy (reason required + optimistic concurrency) =="
# blank reason (present but empty) reaches apply_strategy -> 400 (omitting it = Pydantic 422)
c=$(code -X POST "$BASE/strategy" -H 'Content-Type: application/json' \
      -d '{"symbol":"BTCUSDT","strategy":"flat","reason":""}')
[ "$c" = "400" ] && ok "blank reason -> 400" || bad "reason guard (got $c)"

# stale expected_current -> 409 (the milestone-3 optimistic-concurrency guard, re-wired in m5)
c=$(code -X POST "$BASE/strategy" -H 'Content-Type: application/json' \
      -d '{"symbol":"BTCUSDT","strategy":"mean_reversion","reason":"x","expected_current":"__nope__"}')
[ "$c" = "409" ] && ok "stale expected_current -> 409" || bad "stale guard (got $c)"

# valid switch -> 200 + applied flag (switching to flat is the safe mutation)
resp=$(curl -s -X POST "$BASE/strategy" -H 'Content-Type: application/json' \
      -d '{"symbol":"BTCUSDT","strategy":"flat","reason":"smoke test"}')
echo "$resp" | grep -q '"applied"' && ok "valid switch returns applied flag" \
  || bad "valid switch (no applied): $resp"

echo "== defensive /thesis (milestone-4 primary lever; only 400-paths, no real position opened) =="
c=$(code -X POST "$BASE/thesis" -H 'Content-Type: application/json' \
      -d '{"symbol":"BTCUSDT","direction":"long","conviction":0.3,"rationale":""}')
[ "$c" = "400" ] && ok "thesis blank rationale -> 400" || bad "thesis reason guard (got $c)"

c=$(code -X POST "$BASE/thesis" -H 'Content-Type: application/json' \
      -d '{"symbol":"BTCUSDT","direction":"sideways","conviction":0.3,"rationale":"x"}')
[ "$c" = "400" ] && ok "thesis bad direction -> 400" || bad "thesis direction guard (got $c)"

echo "== /envelope lever (reason required; full caps) =="
c=$(code -X POST "$BASE/envelope" -H 'Content-Type: application/json' \
      -d '{"max_position_usd":1500,"max_total_exposure_usd":3000,"max_leverage":3,"max_drawdown_pct":5,"stop_pct":0.02,"reason":""}')
[ "$c" = "400" ] && ok "envelope blank reason -> 400" || bad "envelope reason guard (got $c)"
resp=$(curl -s -X POST "$BASE/envelope" -H 'Content-Type: application/json' \
      -d '{"max_position_usd":1500,"max_total_exposure_usd":3000,"max_leverage":3,"max_drawdown_pct":5,"stop_pct":0.02,"reason":"smoke test"}')
echo "$resp" | grep -q '"envelope"' && ok "envelope set returns envelope" \
  || bad "envelope set (no envelope): $resp"

echo
echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
