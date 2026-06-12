# operate-desk 用 Sunday 下永續單、管倉位、對帳（friday 執行 SOP）

Sunday 是 Binance USDⓈ-M 交易所代理。**行情=主網真價，下單=測試網。** 交易權只在你（friday）手上——決策過了標準，就照這份 SOP 執行。

**通道（混合制）**：熱路徑**優先用 `mcp__sunday__*` 工具**（typed schema，輸出已整形好直接讀）；工具不可用（tool error / server 不在）時退回 **`http_request`** 直打 `http://127.0.0.1:7777`（`{method,url,query?,body?}` → `status + body`，完整 API `GET /manual`），並在回報裡註明走了降級通道。下表每節並列兩版。

## 一筆交易的執行節奏

1. **Pre-flight（平行查齊）**：憲法的風控共識（開場已讀）＋ `market_get`（精度/限額/最大槓桿）＋ `balance`·`pnl_drawdown`（free 夠嗎、加單後曝險）。違反共識 → 不下；共識不存在 → 先和 risk-monitor 談定。
2. **整備**：槓桿/保證金模式與現況不同才 `set_leverage_margin`（先查後設）。
3. **下單**：`place_order`——schema 已強制 **take_profit + stop_loss + memo + agent**，缺一張不開。
4. **驗證**：讀工具回應的成交狀態與腿 id → `protection_status`（TP/SL 腿、`sl_qty_covers`）→ limit 未成交看 `open_orders`，必要時 `alarm_set` 回查。工具輸出尾行的 `next:` 提醒就是這步。
5. **落盤**：持倉理由 + standing rules 寫回憲法（`PUT /api/memory/friday`，長尾端點走 http_request）；重大進出場 `POST /api/reports` 通報 User。

## 查（唯讀）

**MCP（主）**：`market_get {symbol}` · `positions {}` · `pnl_drawdown {}`（pnl+drawdown 一次合併）· `balance {}` · `open_orders {symbol?}` · `protection_status {symbol}`。

**http_request（降級）**：

```jsonc
{ "method":"GET", "url":"http://127.0.0.1:7777/api/markets/BTCUSDT" }                          // 精度/限額/最大槓桿
{ "method":"GET", "url":"http://127.0.0.1:7777/api/account/positions" }                        // 倉位 + protection + liq_distance_pct
{ "method":"GET", "url":"http://127.0.0.1:7777/api/account/pnl" }                              // 總曝險 total_notional / exposure_pct
{ "method":"GET", "url":"http://127.0.0.1:7777/api/account/balance" }                          // equity / free / used
{ "method":"GET", "url":"http://127.0.0.1:7777/api/account/orders/open", "query":{ "page":"1" } }
{ "method":"GET", "url":"http://127.0.0.1:7777/api/perp/protection", "query":{ "symbol":"BTCUSDT" } }  // 單一標的保護腿狀態
```

## 下單

**MCP（主）**：

```jsonc
mcp__sunday__place_order {
  "agent":"friday", "symbol":"BTCUSDT", "side":"buy", "type":"market",
  "notional_usd":200,                       // 或 qty（張數）——恰好擇一
  "leverage":5, "margin_mode":"isolated",
  "take_profit":75000, "stop_loss":60000,   // 觸發價，schema 強制必帶
  "memo":"（你的決策理由，≤300 字，給 User 看）"
}
```

**http_request（降級）**：`POST /api/perp/order`，body 同上欄位（無 `agent`，改 header `X-Agent: friday`）。

- `side` buy/sell · `type` market/limit（limit 要帶 `price`，market 不准帶）。
- **`take_profit` / `stop_loss` 是觸發價**；Sunday 掛成 reduce-only TP/SL 腿（MARK_PRICE 觸發）。觸發價已在觸發區會被 400 擋下（防一掛即市價平倉）——limit 單先不帶會立觸的腿，成交後用 `set_protection` 補掛。
- 倉位大小換算交給 `calc`：風險額 ÷ |entry − stop_loss| → qty；qty × entry → notional。

## 管倉

**MCP（主）**：`close_position {agent,symbol}`（市價平倉，自動清孤兒腿並回報清了哪些）· `set_protection {agent,symbol,take_profit?,stop_loss?}`（至少一腿；先掛新後撤舊，不裸奔）· `set_leverage_margin {agent,symbol,leverage?,margin_mode?}` · `cancel_order {agent,symbol,order_id}` · `cancel_all_orders {agent,symbol}`（**會連 TP/SL 腿一起撤**——在倉時用完必須立刻 `set_protection` 補回）。

**http_request（降級）**：

```jsonc
{ "method":"POST",   "url":"http://127.0.0.1:7777/api/perp/close",         "headers":{ "X-Agent":"friday" }, "body":{ "symbol":"BTCUSDT" } }   // 市價平倉（reduce-only，自動清孤兒腿）
{ "method":"POST",   "url":"http://127.0.0.1:7777/api/perp/protection",    "headers":{ "X-Agent":"friday" }, "body":{ "symbol":"BTCUSDT", "stop_loss":62000 } }  // 補/改 TP/SL（null = 該腿不動）
{ "method":"POST",   "url":"http://127.0.0.1:7777/api/perp/leverage",      "headers":{ "X-Agent":"friday" }, "body":{ "symbol":"BTCUSDT", "leverage":10 } }
{ "method":"POST",   "url":"http://127.0.0.1:7777/api/perp/margin-mode",   "headers":{ "X-Agent":"friday" }, "body":{ "symbol":"BTCUSDT", "mode":"cross" } }
{ "method":"DELETE", "url":"http://127.0.0.1:7777/api/perp/orders",        "query":{ "symbol":"BTCUSDT" } }  // 撤該標所有掛單
{ "method":"DELETE", "url":"http://127.0.0.1:7777/api/perp/order/123456",  "query":{ "symbol":"BTCUSDT" } }  // 撤單一掛單
```

- **改 TP/SL 一律走 `set_protection`**（引擎先掛新、後撤舊）→ 驗 `protection_status`，不留裸倉跨回合。
- **平倉後**：確認孤兒 TP/SL 掛單已清（`close_position` 會自動清並列出 id；手動平的查 `protection_status`，輸出帶 `ORPHAN LEGS` = 孤兒腿，撤掉）。

## 錯誤碼速查

| 錯誤 | 含義 | 動作 |
| --- | --- | --- |
| `-4016 PERCENT_PRICE` | 價格離現價太遠 | 貼近現價重掛或改 market |
| `-1021 timestamp` | 時鐘偏移（Sunday 會自動校時重試） | 連續出現 → `POST /api/reports`（kind:"system"） |
| 400 參數錯 | 精度/限額不符，或 TP/SL 在會立觸的一側 | 對照 `market_get` 與錯誤說明修正再送 |
| 503 / 連不上 | Sunday 異常 | 等 30–60s 重試一次，仍失敗照 RUNBOOK.md 用 bash 重啟；先補齊半完成的保護腿 |

寫入工具回「placed-or-not UNKNOWN」（連線中斷）= 單可能已落地：**先 `open_orders`/`positions` 對帳再決定，嚴禁盲目重送。** 同一動作最多重試 2 次，再失敗停手：`POST /api/reports` 通報 User，現場記進記憶。

## 紀律

1. 沒有 stop_loss 的倉位不准存在；調倉後必驗 `protection_status`。
2. 共識為界：違反就縮單或先和 risk-monitor 重談，不准先斬後奏。
3. 下單前看現況、下單後驗結果、重啟後先對帳（positions/open_orders）。
4. 走了降級通道（http_request）就在回報裡註明；細節隨時 `GET /manual`。
