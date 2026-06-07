# T4 — regime 偵測 + `notify()` webhook + `/heartbeat` + watchdog

> 1.0 任務 **4/6** ｜ 共用契約見 [`README.md`](README.md) ｜ **依賴：T3**

## 做什麼
讓 Sunday **會主動喚醒 swarm**（event-gated）：偵測 regime 變化 → 發 webhook；並做 heartbeat + 最小安全地板。這是「swarm→Sunday」之外**另一條邊界**（Sunday→swarm）。

## 交付
- `engine/sunday/events.py`：`notify(title, body, data=None, to="leader")` → `POST {EVVA_WEBHOOK_URL}`；每次發送寫 `webhook_log`。
- regime 偵測器（在 strategy.py 或獨立）：1h bar 收盤時偵測 **EMA20×EMA50 cross 翻轉**（或 ATR/波動帶穿越）；**redis 去抖**（每 bar ≤1 次）。觸發 → `notify("regime shift", "<觸發指標>", to="leader")`。**body 必帶觸發指標**（legibility）。
- `engine_degraded`：交易所斷線 / 連續下單失敗 / 取不到行情 → `notify(...)`。
- `app.py` 接 `POST /heartbeat {}` → 重置 redis watchdog 時間戳、回 `{ok, watchdog_reset_at}`。
- watchdog 地板：背景檢查，超過 `heartbeat_timeout`（90m）沒收到 heartbeat → **停開新倉**（既有倉留 stop）、`mode` 標記。

## Done
- 人為觸發 regime（餵讓 EMA cross 翻轉的資料，或測試 hook）→ `:8888` 收到 `regime_shift`（可先用 curl 對 `:8888/api/swarm/sunday/event` 驗，或 evva 真的起著）。
- `webhook_log` 有發送紀錄。
- `POST /heartbeat` 重置 watchdog；模擬逾時 → 進入「停開新倉」。

## 不在本任務
- friday 端怎麼反應（T5/T6 的 swarm 側）、完整雙向 dead-man + safe/flat 區分（1.1）。
