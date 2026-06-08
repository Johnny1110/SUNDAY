# T2 — 兩個防禦式 skill + permission allow-rules

> milestone-3 任務 **2/6** ｜ 契約見 [`README.md`](README.md) §2 ｜ **依賴：無引擎依賴（可現在就做，最 on-theme）**

## 做什麼

把 agent 的「訓練」寫好：兩個 per-role skill，recipe **可直接複製、自帶防禦**（含 `jq` 解析、下令前後驗證、stale 處理），對齊上層 §7.10 下令紀律。這是 RP-10 per-agent skill 的 dogfood，也是最便宜就能買到監督品質的地方。

## 交付

- **`agents/main/friday/skills/operate-sunday/SKILL.md`**（leader）：唯讀 recipe + lever recipe（`/strategy`+`reason`、`/halt`、`/heartbeat`）+ **§7.10 三紀律** + 「細節 `curl :7777/manual`」。**含 milestone-3 增強**：用 `GET /signals` 做決策、用 POST 回應本身驗證、過期用 `expected_current` 重送。
- **`agents/sub/analyst/skills/query-sunday/SKILL.md`**（諮詢角色）：唯讀 recipe（`/signals`、`/status`、`/market`、`/positions`、`/pnl`）+「把*方向 + 建議策略 + 理由* `send_message` 回 friday」範本；**不碰 lever**。
- **permission allow-rules**（落點對照 evva settings/config）：唯讀 curl（`/status`·`/signals`·`/market`·`/positions`·`/pnl`·`/strategy/outcomes`·`/manual`·`/heartbeat`）放行；`POST /strategy`·`/halt` **不放行 → 維持 ask**。

## Done

- 兩 skill 載入後：agent 唯讀 curl 不跳審批、lever POST 跳審批且標明發起 agent。
- `operate-sunday` 的 recipe 含「下令後驗證」「stale 重送」**可複製**範本。

## 不在本任務

- 引擎端 `/signals`·`/strategy/outcomes` 實作（T1/T3）。
- 其餘三角色（risk-monitor/reporter/reviewer）的 skill——沿用 `query-sunday`，milestone-1.1 補。
