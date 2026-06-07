# T1 — Sunday engine 骨架（FastAPI + DB + `/manual` + `/status` stub）

> 1.0 任務 **1/6** ｜ 共用契約見 [`README.md`](README.md) ｜ **依賴：無**（起點）

## 做什麼
立 Sunday 服務的骨架，讓後面的任務有地方掛。先不接交易所、不跑策略——只要服務起得來、DB 通、手冊與狀態端點有回應。

## 交付
- `engine/pyproject.toml`：FastAPI、uvicorn、ccxt、SQLAlchemy 或 psycopg、redis、pandas、numpy。
- `engine/.env.example`：`BINANCE_TESTNET_KEY`、`BINANCE_TESTNET_SECRET`、`DATABASE_URL`、`REDIS_URL`、`EVVA_WEBHOOK_URL=http://127.0.0.1:8888/api/swarm/sunday/event`。
- `engine/sunday/config.py`：讀 env。
- `engine/sunday/store.py`：postgres + redis 連線；DAO 骨架（之後任務往這加方法）。
- `engine/migrations/0001_init.sql`：**全部** schema（README §4 的 9 張表）。
- `engine/sunday/manual.md`：`/manual` 內容——先放 README §3 的 API 契約 + 策略/封套語意。
- `engine/sunday/app.py`：FastAPI；掛 `GET /manual`（回 manual.md）、`GET /status`（stub）。

## 端點
- `GET /manual` → manual.md 全文。
- `GET /status` → **stub**：欄位齊全、值先假（`{alive:true, mode:"flat", symbol:"BTCUSDT", strategy:"flat", strategy_rationale:"(stub)", position:null, exposure_usd:0, leverage:0, equity:0, pnl_day:0, last_event_ts:null, swarm_heartbeat_ok:true}`）。

## Done
- `uvicorn` 起得來；`curl :7777/manual`、`curl :7777/status` 都有正確 JSON/markdown。
- `0001_init.sql` 套用成功（9 張表都在）；redis ping 通。

## 不在本任務
- 真行情 / 下單（T2）、策略 / 風控 / 帳本寫入（T3）、regime / notify / heartbeat（T4）、swarm（T5）。
