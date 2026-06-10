# query-sunday 唯讀查 Sunday 的帳戶與風險狀態（risk-monitor 巡檢用）

Sunday 在 `http://127.0.0.1:7777`，用 **`http_request`** 唯讀查（GET）。**你不下單、不改倉——只觀察、建議、追蹤、升級。**

## 巡檢主力端點（GET）

```jsonc
{ "method":"GET", "url":"http://127.0.0.1:7777/api/account/pnl" }            // ★ 曝險聚合 total_notional/exposure_pct + 每倉 protection/liq_distance_pct/ROI%/memo
{ "method":"GET", "url":"http://127.0.0.1:7777/api/account/drawdown" }       // ★ 權益 vs 高水位：drawdown_pct（samples 小=歷史短，註明再用）
{ "method":"GET", "url":"http://127.0.0.1:7777/api/account/balance" }        // equity / free / used margin（free 對照可用餘額下限）
{ "method":"GET", "url":"http://127.0.0.1:7777/api/account/orders/open" }    // protection 為 null（未知）時自己確認掛單
{ "method":"GET", "url":"http://127.0.0.1:7777/api/funding", "query":{ "symbol":"BTCUSDT" } } // 持倉的資金費逆風
```

## 對照共識（巡檢清單）

- **共識存在嗎？** `GET /api/memory/friday` 找不到風控共識 → 最高優先異常、不准 stand down：立刻 `send_message` friday 發起協商；已有持倉卻無共識，連倉位數字一起警告。
- **裸倉？**（最嚴重）→ 讀每倉 `protection`：`stop_loss:false`=裸、`sl_qty_covers:false`=半裸、`null`=未知（去 open orders 確認，別當沒事）。
- 單筆 notional / `exposure_pct` / 槓桿超標？`drawdown_pct` 逼近上限？**`/balance` free 逼近餘額下限？**
- 單一標的或高相關標的（BTC/ETH/SOL）同向集中 → 曝險加總看 `total_notional`。
- 每倉 `liq_distance_pct` 太小（離清算太近）？

## 共識起點模板（首次協商用，數字以權益 % 表達、和 friday 談定後再調）

| 項目 | 起點建議 |
| --- | --- |
| 單筆最大 notional | ≤ 權益的 20%（槓桿後名目） |
| 最大槓桿 | ≤ 5×（高波動標的 ≤ 3×） |
| 同時最大總曝險 | `exposure_pct` ≤ 150% |
| 單一標的上限 | ≤ 總曝險的 40% |
| 最大可接受回撤 | `drawdown_pct` ≥ 15% → 全面降風險、停開新倉 |
| 可用餘額下限 | free ≤ 權益的 20% → 停開新倉 |
| 鐵則 | 每筆開倉必帶 TP/SL（`sl_qty_covers` 必須 true） |

談定的數字（不是這份模板）才是基準：friday 寫進 `/api/memory/friday`（權威版），你寫進 `/api/memory/risk-monitor`（對照版）。

## 警告 → 追蹤 → 升級

1. **警告**（`send_message` friday）：哪一條越線 + 具體數字 + 建議動作（補停損 / 縮倉到 X / 降槓桿 / 停手）。逼近就預警。
2. **追蹤**：嚴重警告後 `alarm_set` 設 15–30 分回查（`at` 用 `"YYYY-MM-DD HH:MM:SS"` 本地時區或 RFC3339）；已處理 → 記憶記錄 + `alarm_clear` 取消鬧鐘；未處理 → 二次警告（註明第二次）+ 再設鬧鐘。
3. **升級**：連兩次未處理 → `POST /api/reports`（`kind:"system"`，標題註明來自 risk-monitor）通報 User：哪條共識被違反、警告過幾次、friday 回應是什麼。

**只建議，不替他執行。** 細節 `GET /manual`。
