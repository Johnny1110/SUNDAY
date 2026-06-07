# T3 — 策略 + 確定性風控 + 帳本（`/strategy` · `/halt`）

> 1.0 任務 **3/6** ｜ 共用契約見 [`README.md`](README.md) ｜ **依賴：T2**

## 做什麼
讓 Sunday **會自己交易**：依當值策略下單、受確定性風控約束、把一切落帳本。並開兩根 lever 端點。

## 交付
- `engine/sunday/strategy.py`：
  - `momentum`：1h 計 EMA20/EMA50，EMA20>EMA50→目標多、<→目標空；只持一個方向。
  - `flat`：目標無倉（既有倉平掉）。
  - 產生「目標倉位」→ 交給風控 → 經 T2 adapter 執行。每次決策寫 `signals`（含 `indicators_json`、`action`）。
- `engine/sunday/risk.py`（**確定性、非 LLM**）：下單入口檢查 `max_position_usd`/`max_total_exposure_usd`/`max_leverage`，越線**拒單**並寫 `risk_events`；進場**必掛** stop（`stop_pct`）。封套數值寫死在 config。
- `store.py` 帳本寫入：`orders`/`fills`/`positions`(含 `strategy`,`entry_reason`)/`pnl_snapshots`/`strategy_state`。**每筆 tag `strategy`**。
- `app.py` 接：
  - `POST /strategy {symbol, strategy, reason}` → **idempotent set**；切換當值策略、`reason` 落 `strategy_state`、依新策略即時調倉。
  - `POST /halt {reason, mode}` → `flat`=平倉+停；`safe`=凍新倉（既有倉留 stop）。
- `GET /status` 改成**真的**：回當值 `strategy` + `strategy_rationale`（如「EMA20>EMA50 趨勢偏多」）+ 真倉位 + 曝險/equity。

## Done
- `POST /strategy {strategy:momentum, reason:"…"}` → 開倉（在封套內、有 stop）+ 落帳 + `strategy_state.reason` 查得到。
- 故意打一筆超過 `max_position_usd` 的單 → **被拒** + `risk_events` 有一筆。
- `POST /halt {mode:flat}` → 平倉 + 停。
- `/status` 反映真實當值策略與倉位。

## 不在本任務
- regime 偵測 / 主動發 webhook（T4）、`/envelope` lever 與 drawdown breaker（1.1）。
