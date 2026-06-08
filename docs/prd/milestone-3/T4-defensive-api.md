# T4 — 防呆契約（POST 回 state + idempotent + `expected_current` + staleness）

> milestone-3 任務 **4/6** ｜ 契約見 [`README.md`](README.md) §2 ｜ **依賴**：M1.0-T3 lever 端點（impl 折入一起做）

## 做什麼

把上層 §7.10 的三條下令紀律從「prompt 叮嚀」變「**API 機制保證**」，順 agent 既有的「工具回可糾正錯誤 → 重試」直覺（M3-D4，對齊 evva 的 task 轉移 / `send_message` 收件人驗證）。

## 交付

- **所有 POST lever 回傳套用後的完整 state**：`/strategy`·`/envelope`·`/halt` 回 `{ok, applied, resulting_status: {...}}` → **「下令後驗證」免費**（agent 不必再 `curl /status`）。
- **`POST /strategy` 收選填 `expected_current`**（如 `"momentum"`）：與當前當值策略不符 → **不套用**、回 `409 + {error:"stale", current_status:{...}}`（可糾正，agent 重抓 `/status` 後重送）。idempotent set 不變（設兩次同狀態 = 一次）。
- **`GET /status` 的 `as_of_ts` + `last_lever`**（T1 已加）讓 agent 自判視圖是否過期。
- **`/restart` 例外**：非冪等、需確認鍵（不套用 idempotent / expected_current）。

## Done

- **A2**：`POST /strategy` 後 agent 從**回應本身**確認新 state（無第二趟 curl）。
- **A3**：帶舊 `expected_current` → 收 `409 + 當前 state`，重抓重送成功。

## 不在本任務

- 引擎策略邏輯本體（M1.0-T3）；webhook payload（T5）。
