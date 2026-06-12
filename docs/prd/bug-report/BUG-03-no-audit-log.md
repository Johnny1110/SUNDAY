# BUG-03 — 無訂單來源審計記錄（Audit Log）

## 嚴重程度
⚠️ Medium-High — 無法追溯訂單操作者，導致異常事件（如 HYPE 不明平倉）無法歸責

## 問題

Sunday 的訂單記錄（`GET /api/account/orders`、`/trades`）不包含操作者身份資訊。`client_order_id` 僅為隨機 hex，不攜帶 agent 識別碼。

## 實際案例

**HYPEUSDT 不明平倉**（2026-06-11 23:54）：
- Order #259885354：reduce-only market sell，70.54 HYPE @56.555
- 非 friday 指令、非 trader 執行、非 TP/SL 觸發、非 liquidation
- **無法從 API 確認操作者**——只能推測可能是其他 agent 繞過 ticket 協議，或 protection API bug
- 若有 audit log，此事件可在數秒內結案

## 影響

- 異常交易無法追溯 → 治理依賴團隊紀律而非技術強制
- 調查成本高（task #4 需交叉訊問全隊）
- 若發生在更大倉位上，可能造成重大損失而無法究責

## 修復建議

1. Sunday 從 HTTP request 中識別操作 agent（自訂 header `X-Agent` 或 evva 注入的呼叫者身份）
2. `GET /api/account/orders`、`/trades`、`/orders/open` 每筆記錄新增 `agent` 欄位
3. 可選查詢參數 `?agent=` 用於過濾
4. 舊訂單 `agent` 欄位回 `null`（向後相容）

參見 PRD-001-audit-log。

---

— friday, 2026-06-12 01:36 CST

## ✅ 已修復（2026-06-12）

照修復建議 1–4 實作，並擴大覆蓋面——**所有**訂單簿寫入都記錄，不只開倉：

1. 所有 `/api/perp` 寫入端點（order / protection / close / 撤單）接受 `X-Agent` header，
   寫入 sqlite `order_log` 新增的 `agent` / `action`（order｜protection｜close｜cancel）欄位。
   舊資料庫 connect 時自動補欄（additive migration），舊單 `agent` 回 `null`（建議 4）。
   稽核寫入為 best-effort：單已成交後存檔故障只記 log、不回 5xx（避免 agent 重試重複下單）。
2. `GET /api/account/orders`、`/orders/open`、`/trades` 每列新增 `agent`（成交經由其下單
   訂單歸屬；建議 2），並支援 `?agent=` 過濾（建議 3）。撤單記錄不會搶走原下單者的歸屬。
3. 倉位 memo 顯示不受影響：position join 只看 `action='order'` 列。

HYPE 類事件今後可直接結案：平倉單（`/close` 與 protection 換腿）現在都有 `agent` + `action`
記錄。注意 `X-Agent` 為自報身份（誠實協作前提，與 ticket 協議同層級的紀律要求）；強制身份
驗證屬 PRD-001 後續範圍。迴歸測試：`tests/test_audit_log.py`。
