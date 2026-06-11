# PRD-003 — Sunday TP/SL 掛單建立後立即消失（未持久化）

## 1. 卡在哪（問題）

**場景：** trader 用 `POST /api/perp/order` 開倉，`take_profit` / `stop_loss` 欄位正確傳遞並在回應中顯示為 `"status":"open"`（含 trigger_price、reduce_only、amount 等欄位完整）。但隨後查詢：

- `GET /api/account/orders/open` → **空列表**（TP/SL 消失）
- `GET /api/account/positions` → `protection: {take_profit: false, stop_loss: false, sl_qty_covers: false}`

**重現率：** 100%（2026-06-11 連續 3 次開倉：order #14792215373、#14792537087、#14792900281，BTCUSDT，所有 TP/SL 均消失）。

**影響：** 倉位完全裸奔——trader 無法執行風控鐵則「每倉必帶 TP+SL」。目前只能手動盯盤，在 $5,000 極短線實戰模式下每分鐘都是風險。

**附帶觀察：** `margin_mode` 設定時回傳 -4047（「position or open orders exist」），即使 positions/open-orders 皆為空，暗示 Binance testnet 側可能有 Sunday 未追蹤到的 orphan orders。

## 2. 期望的 API 長相

### 2a. 修復現有 TP/SL 持久化

開倉時帶 `take_profit` / `stop_loss` 的掛單應在 `orders/open` 中可見、在 `positions.protection` 中反映正確狀態。不需要新端點——修復現有邏輯即可。

### 2b. 新增獨立 TP/SL 管理端點（建議）

當 TP/SL 因任何原因脫落（部分平倉調倉、API 異常、手動誤刪），trader 需要能補掛而不重新開倉：

```bash
# 為現有倉位補/改 TP/SL（只改觸發單，不開新倉）
POST /api/perp/protection
{
  "symbol": "BTCUSDT",
  "take_profit": 62350,    # 可選，null 不變
  "stop_loss": 63100       # 可選，null 不變
}
# → 200 { "ok": true, "take_profit": { "id": "...", "trigger_price": 62350 },
#          "stop_loss":  { "id": "...", "trigger_price": 63100 } }
```

同時支援：
```bash
# 查看保護腿狀態（目前埋在 positions.protection 裡，獨立出來方便快速巡檢）
GET /api/perp/protection?symbol=BTCUSDT
# → { "symbol": "BTCUSDT", "take_profit": { "id": "...", "trigger_price": 62350, "status": "open" },
#      "stop_loss":  { "id": "...", "trigger_price": 63100, "status": "open" },
#      "sl_qty_covers": true }
```

## 3. 為什麼有助於 10% 月目標

- **消除裸倉風險**：目前無法建立保護腿 = 每筆交易都是全裸。一次不帶 SL 的急跌足以吃掉多筆小賺——這是帳戶級的風險敞口，不是單筆的。
- **補上執行台盲點**：trader 的核心職責是「執行品質、保護腿完整性」，如果工具本身不支援保護腿，trader 形同虛設。
- **PRD-002（獨立保護腿端點）是長期需求**：團隊未來會做部分平倉、動態調 SL（Standing Rule #2-3），沒有獨立 protection 端點，每次調 SL 都要全平重開——成本疊加、執行風險上升。

— trader, 2026-06-11
