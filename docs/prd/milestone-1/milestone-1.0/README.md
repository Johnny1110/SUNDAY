# Milestone 1.0 — 最小端到端監督迴路（overview + 任務索引）

> **狀態：✅ 完成（2026-06-07）** — T1–T6 全 commit；端到端 live demo 在 testnet 跑通：`regime_shift → friday → 指派 analyst → analyst 交叉驗證後建議 → friday POST /strategy → Sunday 開倉（momentum short）`。驗收 A1–A8 達 7/8（A6 的 lever-ask 因 demo 階段採 permit-all 暫緩，機制已就位、改回 `default` 即驗）。

> 上層：[`../../sunday-project-prd.md`](../../sunday-project-prd.md) ｜ milestone-1 index：[`../README.md`](../README.md)
> = 上層 §10 的 **S0**。本資料夾把 1.0 拆成 **session 大小的任務檔（T1–T6）**——**一個 session 做一個 T，不要一次吞整個 milestone**。
> 繼承上層全部不變量（[`../../../../CLAUDE.md`](../../../../CLAUDE.md) 10 條）。

## 1. 目標（DoD）

在 **Binance USDⓈ-M testnet** 上跑出最小但**端到端**的監督迴路，全程在 evva `:8888` 看得到：

> **Sunday 偵測 regime → webhook 喚醒 friday → friday 指派 analyst 評估 → analyst 查行情後建議 → friday `POST /strategy`（附 `reason`）切策略 → Sunday 反映 → friday 能 `POST /halt`。**

證明「**架構通**」（自主交易 + 通用工具監督 + 兩條 HTTP 邊界 + 一根 lever + kill + 一次多 agent 協作）。**不是**證明賺錢。

## 2. 範圍

**In**：單一 `BTCUSDT`@1h；`momentum`+`flat`；size/exposure 熔斷 + 進場 stop；regime 偵測→`regime_shift`；postgres 帳本 + redis；HTTP API（§3）+ `/manual` + legible `/status`；friday + analyst 兩角 + skill + permission；webhook；最小 heartbeat 地板。
**Out（→1.1/1.2/Gate-2）**：risk/reporter/reviewer、`mean_reversion`、`/envelope`、drawdown breaker、完整雙向 dead-man、`/commentary`、多標的籃子、耐久 run、dashboard、`http_request` 工具（1.0 先純 curl，觀察 ergonomics）。

## 3. 共用契約：Sunday HTTP API（1.0；`/manual` 要文件化這張表）

base `http://127.0.0.1:7777`。讀=auto-allow（allow-rule）；POST lever=ask。

| 方法 | 端點 | 入 | 出（重點） | 哪個任務 |
| --- | --- | --- | --- | --- |
| GET | `/manual` | — | markdown 全文 | T1 |
| GET | `/status` | — | `{alive,mode,symbol,strategy,strategy_rationale,position\|null,exposure_usd,leverage,equity,pnl_day,last_event_ts,swarm_heartbeat_ok}` | T1 stub→T3/T4 實 |
| GET | `/market` | `?symbol&tf&limit` | `{symbol,tf,ohlcv:[[ts,o,h,l,c,v]...]}` | T2 |
| GET | `/positions` | — | `[{symbol,side,qty,entry,mark,upnl,stop,strategy,entry_reason}]` | T2/T3 |
| GET | `/pnl` | `?since` | `{realized,unrealized,equity_curve:[[ts,equity]...]}` | T2/T3 |
| POST | `/strategy` | `{symbol,strategy,reason}` | `{ok,symbol,strategy,applied_at}` | T3 |
| POST | `/halt` | `{reason,mode:"safe"\|"flat"}` | `{ok,mode}` | T3 |
| POST | `/heartbeat` | `{}` | `{ok,watchdog_reset_at}` | T4 |

出站 webhook（`notify()`→`POST :8888/api/swarm/sunday/event`，`to:leader`）：`regime_shift`、`engine_degraded`（T4）。
**legibility（硬需求）**：`/status.strategy_rationale`、`position.entry_reason`、`regime_shift` 觸發指標都要帶理由。

## 4. 共用契約：postgres schema（1.0 子集，modeling-grade）

`ohlcv`、`orders`(tag `strategy`)、`fills`、`positions`(`strategy`,`entry_reason`)、`pnl_snapshots`、`strategy_state`(`symbol`,`strategy`,`reason`,`set_by`,`set_at`)、`signals`(`ts`,`symbol`,`strategy`,`indicators_json`,`action`)、`risk_events`、`webhook_log`。**每筆 order/fill/pnl tag `strategy`**——1.0 就要。（DDL 由 **T1** 的 `migrations/0001_init.sql` 建。）

## 5. 檔案樹（1.0 完成後）

```
sunday/
├── evva-swarm.yml                      (T5)
├── agents/
│   ├── main/friday/{profile.yml, system_prompt.md, tools/active.yml, skills/operate-sunday/SKILL.md}   (T5)
│   └── sub/analyst/{profile.yml, system_prompt.md, tools/active.yml, skills/query-sunday/SKILL.md}      (T5)
└── engine/
    ├── pyproject.toml, .env.example                                   (T1)
    ├── migrations/0001_init.sql                                       (T1)
    └── sunday/{config.py, store.py, app.py, manual.md,               (T1)
                exchange.py,                                           (T2)
                strategy.py, risk.py,                                  (T3)
                events.py}                                            (T4)
```

## 6. 任務索引（一個 session 一個 T）

| T | 檔 | 做什麼 | 依賴 |
| --- | --- | --- | --- |
| **T1** | [T1-engine-skeleton.md](T1-engine-skeleton.md) | FastAPI + DB/redis + migrations + `/manual` + `/status` stub | 無 |
| **T2** | [T2-testnet-adapter.md](T2-testnet-adapter.md) | ccxt USDⓈ-M testnet：行情/下單/平倉/stop；`/market`·`/positions`·`/pnl` | T1 |
| **T3** | [T3-strategy-risk-ledger.md](T3-strategy-risk-ledger.md) | momentum/flat + 風控熔斷 + 帳本 + `/strategy`(+reason)·`/halt` | T2 |
| **T4** | [T4-regime-notify-heartbeat.md](T4-regime-notify-heartbeat.md) | regime 偵測 + `notify()` webhook + `/heartbeat` + watchdog 地板 | T3 |
| **T5** | [T5-swarm-config.md](T5-swarm-config.md) | `evva-swarm.yml` + friday/analyst（prompt/tools/skill）+ permission allow-rules | 無（可與 T1–T4 平行） |
| **T6** | [T6-e2e-demo.md](T6-e2e-demo.md) | 端到端串接 + demo 腳本 + 驗收 A1–A8 | T1–T5 |

> **平行性**：T5（swarm 設定）不需引擎跑就能寫，可與 T1–T4 平行；T6 需全部完成。

## 7. milestone 級驗收（A1–A8，於 T6 驗）

- **A1 迴路**：§1 的迴路跑通且 `:8888` 每步可見。
- **A2 兩條邊界**：Sunday→swarm webhook、swarm→Sunday bash+curl；**evva 內零 Sunday-specific code**。
- **A3 legibility**：`/status` rationale、倉位 `entry_reason`、事件觸發指標都有。
- **A4 決策留痕**：`/strategy` 的 `reason` 落 `strategy_state` 可查。
- **A5 風控**：展示一次「越線下單被拒」（寫 `risk_events`）；進場有交易所 stop。
- **A6 permission**：唯讀 curl 不跳審批；lever POST 跳審批且標明發起 agent。
- **A7 資料**：帳本各表有資料且 tag `strategy`。
- **A8 halt**：`/halt {mode:flat}` 確定性平倉 + 停。

> 1.0 **不**驗 V1（≥3 日）、完整 dead-man、完整 event-gating——那是 1.1/1.2。

## 8. 1.0 決策（預設，可改）

`BTCUSDT`@1h ｜ FastAPI ｜ ccxt ｜ `max_position_usd=2000`/`max_total_exposure_usd=4000`/`max_leverage=3`/`stop_pct=2%` ｜ EMA 20/50 ｜ friday heartbeat 30m / Sunday watchdog 90m ｜ `regime_shift`→`leader` ｜ engine 在 `engine/`。
