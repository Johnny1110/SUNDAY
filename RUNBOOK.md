# Sunday — Runbook (milestone-6: agent-native Binance proxy)

How to run, configure, and **validate** the proxy. The pure logic is unit-tested anywhere
(stdlib only); the live exchange / websocket / dashboard run in *your* environment (they
need `pip install`, network, and a testnet key — none exist in the CI sandbox).

## TL;DR

```bash
cd engine
python3 -m venv .venv && . .venv/bin/activate
pip install -e .                  # fastapi / uvicorn / pydantic-settings / ccxt / websockets
cp .env.example .env              # fill BINANCE_TESTNET_KEY / SECRET (market data needs no key)
python -m sunday                  # serves :7777 — dashboard is pre-built, no Node needed
```

Open `http://127.0.0.1:7777/dashboard` (human) or `GET /manual` (the agent API contract).

---

## 0. What's verified where (read this first)

| Layer | How it's verified | Where |
| --- | --- | --- |
| indicators / pagination / alerts rule / monitor math / indices parsers / events | **60 unit tests, stdlib only** — run anywhere | `./scripts/run-tests.sh` |
| store.py (sqlite) + alert/monitor engines | unit-tested in `:memory:` with injected notifiers | `tests/test_store.py`, `test_alerts.py`, `test_monitor.py` |
| exchange.py / routers / pricehub (ccxt + FastAPI + ws) | syntax-checked (`py_compile`); **integration-tested by you** | `./scripts/smoke.sh` against a running engine |
| dashboard (TS + Vue 3) | `npm run build` + `vue-tsc` typecheck (green) | `engine/sunday/web/` |

> Honest boundary: the build sandbox has no `pip`/`ccxt`/network/testnet key, so the live
> exchange calls, the websocket monitor/alert webhooks, and the served dashboard are
> validated by **you** with the smoke test + a browser. Everything is correct-by-construction
> + syntax-checked + covered by the stdlib unit tests where the logic is pure.

---

## 1. Prerequisites

- **Python ≥ 3.11**. (No Postgres, no Redis — a single sqlite file is the only state.)
- **Node ≥ 20** — *optional*; only to rebuild the dashboard. A pre-built `dist/` is committed,
  so the engine serves the UI with no Node installed.
- **Binance USDⓈ-M testnet** API key + secret — only for trading endpoints; market data needs none.
  Get one at <https://testnet.binancefuture.com> (log in → API Key).

```bash
cd engine
python3 -m venv .venv && . .venv/bin/activate
pip install -e .                      # fastapi / uvicorn / pydantic-settings / ccxt / websockets
cp .env.example .env                  # fill BINANCE_TESTNET_KEY / SECRET (NEVER commit .env)
```

> The testnet keys live **only** in `engine/.env` (gitignored). Agents never see them —
> all exchange access is inside Sunday.

## 2. Dashboard (pre-built — rebuild only after UI changes)

The dashboard ships **committed, pre-built** at `engine/sunday/web/dist/`, so `python -m sunday`
serves it immediately — **no Node needed to run**. Rebuild only when you change the UI:

```bash
cd engine/sunday/web && npm install && npm run build   # → web/dist (served at / and /ui)
npm run typecheck                                       # optional: vue-tsc
```

## 3. Run Sunday

```bash
cd engine && . .venv/bin/activate
python -m sunday                      # serves :7777, opens sunday.db, starts the realtime hub
```

## 4. Configuration (`engine/.env`)

All knobs live in `engine/.env` (template: `.env.example`). The ones you'll touch:

| Var | Default | What |
| --- | --- | --- |
| `BINANCE_TESTNET_KEY` / `_SECRET` | — | testnet account creds (trading). Market data needs none. |
| `EVVA_WEBHOOK_URL` | …`/api/swarm/sunday/event` | where `position_pnl` / `price_alert` webhooks POST |
| `SUNDAY_HOST` / `SUNDAY_PORT` | 127.0.0.1 / 7777 | HTTP bind |
| `SQLITE_PATH` | sunday.db | alerts + monitor config |
| `MONITOR_ENABLED` / `MONITOR_STEP_PCT` | true / 5 | position-PnL monitor on/off + webhook % step |
| `MONITOR_POLL_SEC` | 15 | position-book refresh / REST backstop cadence |
| `WS_ENABLED` | true | websocket price hub (false → monitor/alerts on REST polling only) |
| `INDICES_TTL_FAST/MACRO/FEARGREED` | 300/600/3600 | indices cache seconds |

## 5. Validate (smoke + browser)

```bash
./scripts/run-tests.sh                # 60 unit tests, green (stdlib only)
./scripts/smoke.sh                    # curls every /api/* group on :7777; checks the old API is 404
```

Then open `http://127.0.0.1:7777/dashboard` and walk the pages: **Markets** (sort/filter/paginate) →
**Chart** (interval switch + indicators + funding) → **Trade** (place a leveraged TP/SL order on
testnet) → **Account** (positions/PnL/orders/trades) → **Indices** → **Alerts** (arm a price alert
+ watch the monitor). Anything the UI does, an agent can do with the same token-free `/api/*` calls
documented in `GET /manual`.

### Realtime webhooks → swarm

With an evva swarm running (`evva service start` + `evva swarm .`), Sunday POSTs `position_pnl`
(every `MONITOR_STEP_PCT`% move on an open position) and `price_alert` (when an alert fires) to
`EVVA_WEBHOOK_URL`. To eyeball the payload without a swarm, point `EVVA_WEBHOOK_URL` at any HTTP
echo endpoint (e.g. a one-liner `nc -l` or webhook.site).

## 6. API surface (all token-free; `GET /manual` for full detail)

| Group | Endpoints | Req |
| --- | --- | --- |
| markets | `GET /api/markets` · `/api/markets/{symbol}` | 0 |
| klines  | `GET /api/klines` · `/api/klines/indicators` | 2 |
| funding | `GET /api/funding` · `/api/funding/history` | 2 |
| perp    | `POST /api/perp/order｜leverage｜margin-mode｜close` · `DELETE /api/perp/order/{id}｜orders` | 1 |
| account | `GET /api/account/positions｜balance｜pnl｜orders/open｜orders｜trades` | 3 |
| indices | `GET /api/indices` · `/api/indices/{key}` | 4 |
| alerts  | `POST｜GET /api/alerts` · `DELETE /api/alerts/{id}` | 6 |
| monitor | `GET /api/monitor` · `POST /api/monitor/config` | 5 |
| system  | `GET /health` · `/manual` · `/dashboard` | — |

Lists paginate with `?page=&page_size=` → `{ items, page, page_size, total, has_more }`.

## 7. Troubleshooting

- **`503 BINANCE_TESTNET_KEY not set`** on `/api/account/*` or `/api/perp/*` — add the testnet
  key+secret to `engine/.env`. Market-data endpoints (`markets`/`klines`/`funding`/`indices`) don't need it.
- **`/dashboard` shows a placeholder** — `engine/sunday/web/dist/` is missing; rebuild (§2). A fresh clone has it committed.
- **websocket warnings in the log** — the hub auto-reconnects and the REST backstop (`MONITOR_POLL_SEC`)
  keeps monitor/alerts working regardless; set `WS_ENABLED=false` to run poll-only.
- **`/api/indices/*` returns `stale: true` / `available: false`** — a free upstream (Stooq/Yahoo/
  CoinGecko) hiccuped; the last good value is served and refreshes on the next TTL window.
- **`502 exchange error: …`** — ccxt reached Binance and it errored (rate limit / bad symbol);
  the cause is in the message.
- **port 7777 already in use** — set `SUNDAY_PORT` in `.env`.

## 8. Reset

```bash
rm -f engine/sunday.db                # wipe alerts + monitor config (rebuilt empty on next boot)
```

## 9. Scope note

milestone-6 is the pivot from the old supervised-trading engine to an **agent-native exchange
proxy** (markets / klines+indicators / funding / perp orders / account / indices / alerts /
monitor). The previous strategy/thesis/desk/ablation API was removed. Follow-up (not part of this
build): refresh `evva-swarm.yml` + `agents/` so the swarm *consumer* uses the new `/api/*` surface.

## 10. sunday-mcp sidecar (milestone-9 — typed MCP tools for the swarm)

A stateless, keyless sidecar that serves the hot-path API as typed MCP tools
(`mcp__sunday__*`, 22 of them) on `http://127.0.0.1:7780/mcp`. It only talks to the local
engine on `:7777`; the engine itself is untouched. Background: `docs/prd/milestone-9/`.

### Run / stop

```bash
cd engine && . .venv/bin/activate
pip install -e '.[mcp]'               # once — the mcp SDK is an optional extra
python -m sunday_mcp                  # serves :7780/mcp (env: SUNDAY_MCP_PORT / SUNDAY_BASE_URL)
```

Run it wherever the engine runs (same autostart mechanism). Stopping = kill the process — it
holds no state. macOS gotcha: `pkill -f "python -m sunday_mcp"` misses the venv process
(ps resolves it to capital-P `Python …`); use `pkill -fi`.

### Health & regression

```bash
curl -s :7780/healthz        # {"ok":true,…,"engine":{"reachable":true,"status":200}}
./scripts/smoke-mcp.sh       # full regression — NOTE: the trade-chain part places and
                             # closes a real TESTNET order on SMOKE_SYMBOL (default BTCUSDT)
```

Two health layers: `ok` = sidecar up; `engine.reachable` = the engine behind it.

### Failure handling

| Symptom | Meaning | Action |
| --- | --- | --- |
| sidecar down / `mcp__sunday__*` tool errors | agents auto-degrade to `http_request` + `GET /manual` (invariant S6) and note the degraded channel in their reports | not urgent — restart `python -m sunday_mcp` when convenient |
| `healthz` `ok` but `engine.reachable: false` | the engine is the sick part | fix the engine first (§7); the sidecar needs no touch |
| a write tool answered “placed-or-not UNKNOWN” | the connection died mid-write; the order MAY have landed | reconcile via `open_orders` / `positions` BEFORE any retry — never blind-resend a write |

### Kill-switch (back to milestone-6 behaviour)

`.evva/settings.json` → add `"disabled": true` inside the `sunday` server entry (or delete the
file) → restart the swarm. Members lose the `mcp__sunday__*` catalog and operate on
`http_request` alone (S6). Re-enable: revert the edit, restart the swarm again.

### Sidecar restart vs swarm (self-heal)

Designed behaviour: while the sidecar is down a member's `mcp__sunday__*` call fails as a tool
error (they degrade per the one-line rule); after `python -m sunday_mcp` comes back the next
call succeeds — no swarm restart needed. **Verify this once during rollout step 2** (kill the
sidecar, watch one member degrade, restart it, watch the next call succeed) and record the
result here.
