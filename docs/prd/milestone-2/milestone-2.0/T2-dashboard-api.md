# T2 — Dashboard 讀 API（equity_curve / 歸因 / 切換時間軸）

> 2.0 任務 **2/4** ｜ 共用契約見 [`README.md`](README.md) ｜ **依賴：T1**

## 做什麼
把 T1 落地的資料，聚合成 dashboard 前端要消費的 JSON。**全是 GET = 唯讀 = allow-rule auto。** `/manual` 同步補這幾張表。**只讀不寫**——寫入/捕捉是 T1，頁面是 T3。

### 1. 擴充 `GET /pnl?since=`
- 取代 1.0 的 `equity_curve: []` stub：從 `pnl_snapshots` 讀回 `equity_curve: [[ts_ms, equity], ...]`。
- `realized` 從 closed positions 累計（不再是 `None`）；`unrealized` / `equity` 維持取 exchange（即時）。
- 預設窗 **30 日**（`?since` 覆寫）；回傳 `window_days`。

### 2. `GET /performance?since=`
- per-strategy 歸因：`[{strategy, realized_pnl, n_trades, win_rate, avg_pnl, open_qty}]`。
- 來自 closed positions GROUP BY `strategy`（realized_pnl / 筆數 / 勝率 / 均值）+ open positions 的 `open_qty`。
- 這就是「哪個策略在哪種盤 work」的數字依據（上層 §7.7 per-strategy attribution）。

### 3. `GET /strategy_history?since=`
- `strategy_state` 時間軸：`[{set_at_ms, symbol, strategy, reason, set_by}]`。
- 給前端在權益曲線上標切換點（垂直線）+ tooltip 顯示 `reason`（= B5 疊圖的資料源）。

## 驗收
- [ ] `GET /pnl` 回非空 `equity_curve`（筆數與 `pnl_snapshots` 一致）、`realized` 非 null。
- [ ] `GET /performance` 對至少一個策略回 `realized_pnl` + `n_trades`。
- [ ] `GET /strategy_history` 回得到 1.0（T6）以來每次 `/strategy` 的 `reason`。
- [ ] 三個端點都是 GET、唯讀 allow-rule 放行；`/manual` 三張表都有文件。

## 不在本任務
- 前端頁面（T3）。
- 任何寫入 / 資料捕捉（T1）。
