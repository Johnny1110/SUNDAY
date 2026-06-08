# query-sunday 唯讀查 Sunday 的風險狀態（risk-monitor 對抗式風控用）

Sunday 在 `http://127.0.0.1:7777`。用 **`http_request`** 唯讀查（GET 免審批）。預設**不拉 lever**——除非 operator 透過 `permissions.json` 授你 RP-11 safe-halt 窄 lever（見 system prompt），那時可 `POST /halt {mode:"safe"}`。

## 踢館 / 巡檢主力端點（GET）

```jsonc
{ "method": "GET", "url": "http://127.0.0.1:7777/risk" }                                    // ★ 封套 vs 即時讀數 + 各上限使用率 + 當前違規 + 近期熔斷事件
{ "method": "GET", "url": "http://127.0.0.1:7777/status" }                                  // 全台姿態：聚合曝險 + 每標的 basket（相關性風險看這裡）
{ "method": "GET", "url": "http://127.0.0.1:7777/desk", "query": { "symbol": "BTCUSDT" } }  // funding/OI/基差——擁擠度、反身性風險
{ "method": "GET", "url": "http://127.0.0.1:7777/positions" }                               // 持倉（side / qty / stop）
{ "method": "GET", "url": "http://127.0.0.1:7777/thesis", "query": { "symbol": "BTCUSDT" } } // 正在被踢的 thesis（方向 / conviction / invalidation）
```

## 踢館框架（找它為什麼會錯，要具體）

- **下檔**：`invalidation` 夠近、合理嗎？stop 在哪、下檔多深？
- **擁擠度**：`/desk` 的 funding / OI 顯示大家擠同一邊嗎 → 反身性逆轉、被掃。
- **相關性**：`/status` 的 `basket` + 聚合 `exposure_usd`——這方向和其他倉位疊加同一風險嗎（BTC/ETH/SOL 高相關，曝險會偷偷加總）？
- **迫近事件 / funding 逆風 / 流動性**？

## 回報 friday

`send_message`：**支持 / 反對 + 具體理由 + 建議的 conviction 上限**。巡檢逼近/越界 → 警告 friday，必要時建議 `halt`（或獲授時自行 `POST /halt {mode:"safe"}`）。**你只觀察與建議（除窄 lever 外），不替 friday 拍板。** 細節 `GET /manual`。
