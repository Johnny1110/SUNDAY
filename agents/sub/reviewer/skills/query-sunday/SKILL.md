# query-sunday 唯讀查詢 Sunday（諮詢角色用）

Sunday 在 `http://127.0.0.1:7777`。用 **`http_request`** 工具唯讀查詢（GET 自動放行，免審批）。你**不拉任何 lever**。

## 常用唯讀端點（復盤主力 = `/performance` + `/strategy_history`）

```jsonc
{ "method": "GET", "url": "http://127.0.0.1:7777/performance" }  // per-strategy 績效歸因（realized_pnl / n_trades / win_rate / avg_pnl）
{ "method": "GET", "url": "http://127.0.0.1:7777/strategy_history" }  // 每次切換的時間/標的/策略/reason
{ "method": "GET", "url": "http://127.0.0.1:7777/pnl", "query": { "since": "2026-06-01" } }  // 當日損益 + 權益曲線
{ "method": "GET", "url": "http://127.0.0.1:7777/positions" }
{ "method": "GET", "url": "http://127.0.0.1:7777/status" }
```

## 回報

查完用 `send_message` 把發現交給 **friday**（leader）——簡潔、可執行、附依據。**你只觀察與建議，不下令。**
細節：`http_request` 取 `GET http://127.0.0.1:7777/manual`。
