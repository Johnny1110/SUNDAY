# query-sunday 唯讀查詢 Sunday（諮詢角色用）

Sunday 在 `http://127.0.0.1:7777`。用 **`http_request`** 工具唯讀查詢（GET 自動放行，免審批）。你**不拉任何 lever**。

> **多標的**：引擎跑一籃子（`SUNDAY_SYMBOLS`）。`GET /status` 的 `symbols[]` 每標的一筆；`/signals?symbol=` 逐標的查。

## 常用唯讀端點

```jsonc
{ "method": "GET", "url": "http://127.0.0.1:7777/status" }                                  // 當值策略/倉位/曝險/回撤/equity/mode
{ "method": "GET", "url": "http://127.0.0.1:7777/signals", "query": { "symbol": "BTCUSDT" } }   // 決策面板(指標已算好,別自己算)
{ "method": "GET", "url": "http://127.0.0.1:7777/positions" }                               // 持倉 + 進場理由
{ "method": "GET", "url": "http://127.0.0.1:7777/pnl", "query": { "since": "2026-06-01" } } // 已/未實現損益 + 權益曲線
{ "method": "GET", "url": "http://127.0.0.1:7777/strategy/outcomes", "query": { "symbol": "BTCUSDT" } }  // 每次切換的結果
```

## 回報

查完用 `send_message` 把發現交給 **friday**（leader）——簡潔、可執行、附依據。**你只觀察與建議，不下令。**
細節：`http_request` 取 `GET http://127.0.0.1:7777/manual`。
