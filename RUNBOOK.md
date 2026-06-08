# Sunday — Runbook (milestone-1.0 + milestone-3)

How to run the engine + swarm and **validate** the milestone. The pure decision
logic is unit-tested in CI (stdlib only); the live trading loop + swarm e2e run in
*your* environment (they need postgres, redis, testnet keys, and a running evva
service — none of which exist in the CI sandbox).

---

## 0. What's verified where (read this first)

| Layer | How it's verified | Where |
| --- | --- | --- |
| indicators / strategy / regime / risk / attribution / events / exchange-signing / execution / views | **79 unit tests, stdlib only** — run anywhere | `./scripts/run-tests.sh` |
| store.py / app.py (psycopg + FastAPI wiring) | syntax-checked (`py_compile`); **integration-tested by you** | `./scripts/smoke.sh` against a running engine |
| swarm supervision e2e (agent reads `/signals`, pulls a lever, …) | **manual validation by you** in the web UI | §4 below |

> Honest boundary: I could not run the FastAPI app or hit the testnet account API in
> the build sandbox (no pip/postgres/redis/docker). Everything below is written to be
> correct-by-construction + syntax-checked; you close the loop with the smoke test + the
> demo flow.

---

## 1. Prerequisites

- **Python ≥ 3.11**, **PostgreSQL**, **Redis** (local).
- **evva** built and on PATH (`go build ./cmd/evva` in `../evva`) — the swarm runtime.
- **Binance USDⓈ-M testnet** API key + secret.

```bash
# postgres: one-time
createdb sunday                       # or: psql -c 'CREATE DATABASE sunday;'

# engine deps + package (editable)
cd engine
python3 -m venv .venv && . .venv/bin/activate
pip install -e .                      # fastapi/uvicorn/psycopg/redis/pydantic-settings

# env: copy and fill in the testnet keys (NEVER commit .env)
cp .env.example .env
#   BINANCE_TESTNET_KEY=...   BINANCE_TESTNET_SECRET=...
#   DATABASE_URL=postgresql://root:root@localhost:5432/sunday
#   REDIS_URL=redis://localhost:6379/0
```

> The testnet keys live **only** in `engine/.env` (gitignored). Agents never see them —
> all exchange access is inside Sunday (invariant: agents hold no exchange keys).

## 2. Run Sunday (the engine)

```bash
cd engine && . .venv/bin/activate
python -m sunday                      # serves :7777, runs migrations, starts the loop
```

Validate the HTTP contract (engine only, no swarm needed):

```bash
./scripts/run-tests.sh                # 79 unit tests, green
./scripts/smoke.sh                    # curls :7777 — checks /signals, /status, and the
                                      # defensive /strategy (reason→400, stale→409, ok→resulting_status)
```

## 3. Run the swarm (the supervisors)

```bash
# from ../evva
evva service start                    # prints a session token
# from this repo root (where evva-swarm.yml lives)
evva swarm .                          # registers space "sunday"; prints a web URL
```

Open `http://127.0.0.1:8888`, paste the token, enter the **sunday** space. You'll see
`friday` (leader) + `analyst`.

### Permissions (automatic with http_request)

The agents drive Sunday via the **`http_request`** tool, which gates by method:
**GET/HEAD auto-allow** (read polling runs with no prompt) and **POST/PUT/DELETE ask**.
So the lever POSTs (`POST /strategy`, `/halt`) prompt for approval in the web UI — naming
the agent — with **no allow-rules to configure**; the milestone-3 safety boundary (A6)
holds by construction. Tip: `POST /heartbeat` also asks, so click **"Always allow"** on
friday's first heartbeat to stop the 30-min liveness ping from nagging.

> Build evva from `dev` (which now has `http_request`). To scope a *narrow* lever to a
> non-leader later (RP-B), "Always allow" that member's specific `POST .../halt` — the
> rule matches by method+url, so other levers still ask.

## 4. Validate the milestone (the demo flow)

The DoD loop (milestone-1.0 §1): **regime_shift → friday → analyst → /strategy → reflected → halt.**

1. **Wake on event.** Let the engine run; on a regime change it POSTs `regime_shift` to
   `friday`. (To force one for testing, you can switch the active strategy or restart so the
   first classification differs — or inject a webhook: `curl -X POST
   :8888/api/swarm/sunday/event -d '{"title":"regime_shift","body":"test","to":"friday"}'`.)
2. **A1 — no hand-math.** Watch friday/analyst read `GET /signals` and reason over the
   panel (votes + indicators + regime) — they should **not** pipe curl into python to compute
   EMAs. (This is the milestone-3 win.)
3. **A2 — switch + verify from response.** friday `POST /strategy` (with a `reason`); the
   200 body carries `resulting_status.strategy` — it verifies without a second curl.
4. **A3 — stale guard.** If friday sends an out-of-date `expected_current`, it gets a 409 +
   `current_status`, re-reads, retries. (smoke.sh exercises this deterministically.)
5. **A6 — gate.** The lever POST pops an approval in the web UI naming the agent; reads don't.
6. **A4 — closed loop.** After a few switches, `GET /strategy/outcomes` shows each switch's
   realized PnL / win-rate — the substrate for learning the switching policy.
7. **halt.** friday `POST /halt {mode:flat}` → the engine flattens + stops.

## 5. Reset / clean up

```bash
evva swarm stop <space-id>            # stop the swarm
dropdb sunday && createdb sunday      # wipe the ledger (or just TRUNCATE)
redis-cli del sunday:swarm_heartbeat_ts
```

## 6. Scope note

This delivers **milestone-1.0 (S0) folded with milestone-3** for a single symbol
(BTCUSDT, 1h, momentum/mean_reversion/flat). Out of scope here (milestone-1.1+):
`/envelope` lever, full risk roster (risk-monitor/reporter/reviewer), multi-symbol
basket, endurance run, the dashboard (Gate-2). The three evva RP drafts
(`docs/prd/milestone-3/evva-rps/`) are ready to file in `../evva`.
