# T6 — 端到端串接 + demo + 驗收 A1–A8

> 1.0 任務 **6/6** ｜ 共用契約見 [`README.md`](README.md) ｜ **依賴：T1–T5 全完成**

## 做什麼
把引擎（T1–T4）與 swarm（T5）串成完整迴路，照腳本演一遍，打勾 milestone 級驗收 A1–A8。**收尾任務，不新增功能**——只串接、跑通、驗證、補洞。

## demo 腳本（§ overview §1 的迴路）
1. 起 postgres + redis；`engine/` 跑（uvicorn）；確認 `curl :7777/manual`、`/status` 正常、testnet 連得上。
2. `evva service start`；在 `sunday/` 跑 `evva swarm .` → 註冊 space `sunday`。
3. Sunday 以 `flat` 起始。**人為製造 regime**：餵讓 EMA cross 翻轉的 1h 資料（或測試 hook 直接打一發 `regime_shift`）。
4. Sunday `notify("regime shift", "...EMA20 上穿 EMA50...", to="leader")` → `:8888` → friday 信箱。
5. friday 醒來讀事件 → `send_message` 指派 analyst「評估這個 regime」。
6. analyst 醒來 → `curl /market`、`/status`（+ 選配 web 新聞）→ `send_message` 回 friday「偏多，建議切 momentum，理由…」。
7. friday `curl -sX POST /strategy {symbol:BTCUSDT, strategy:momentum, reason:"analyst 判趨勢偏多…"}`（**permission ask → User 在 Web 准**）→ Sunday 切 momentum、開倉、掛 stop。
8. friday `curl /status` 驗證 `strategy=momentum`、倉位出現。
9. friday `curl -sX POST /halt {reason:"demo 結束", mode:"flat"}` → Sunday 平倉、停。
10. 全程在 `:8888`：事件、friday↔analyst 訊息、permission 審批、lever 行使都看得到；Sunday 端 `strategy_state.reason`、`signals`、`orders` 有紀錄。

## 驗收（A1–A8，逐項打勾）
- [ ] **A1 迴路**：步驟 4–9 跑通且 `:8888` 每步可見。
- [ ] **A2 兩條邊界**：Sunday→swarm webhook、swarm→Sunday bash+curl；**evva 內零 Sunday-specific code**（repo 無 Sunday code）。
- [ ] **A3 legibility**：`/status.strategy_rationale`、倉位 `entry_reason`、`regime_shift` 觸發指標都有。
- [ ] **A4 決策留痕**：`/strategy` 的 `reason` 落 `strategy_state` 可查回。
- [ ] **A5 風控**：展示一次「越線下單被拒」（`risk_events` 有一筆）；進場有交易所 stop。
- [ ] **A6 permission**：唯讀 curl 不跳審批；`POST /strategy`/`/halt` 跳審批且 Web 標明發起 agent。
- [ ] **A7 資料**：`orders/fills/positions/pnl/signals/strategy_state` 有資料且 tag `strategy`。
- [ ] **A8 halt**：`POST /halt {mode:flat}` 確定性平倉 + 停。

## 不在本任務
- V1（≥3 日連續）、完整雙向 dead-man、完整 event-gating——那是 **1.1 / 1.2**。
- 觀察記錄：1.0 純 `bash`+curl 的 ergonomics 痛點 → 寫一句結論，餵 1.1 是否導入 `http_request`（上層 §6.4）。
