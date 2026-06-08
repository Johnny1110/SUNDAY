# operate-sunday 操作 Sunday 交易引擎：查狀態、切策略、叫停、心跳（leader 專用）

Sunday 是我們的交易引擎（Binance USDⓈ-M 永續 **testnet**），在 `http://127.0.0.1:7777`。
**它自己交易，你監督它。你不下單。** 你用 **`http_request` 工具**操作它：傳 `{method, url, query?, body?}`，
拿回 `status + 解析後的 body`。

- **GET/HEAD 自動放行**（唯讀，免審批）；**lever POST 會跳 permission 審批**（僅你）。
- 完整 API 隨時用 `http_request` 取 `GET http://127.0.0.1:7777/manual`。

## 監督節奏

1. **重抓現況** — 別只信 webhook payload（那是「當時」）。先 GET `/status`。
2. **判斷** — regime 真的變了、值得切策略嗎？平靜無事就回報並 stand down。
3. **行動** — 要切策略/叫停才拉 lever（見下，**附 `reason`**）。
4. **驗證** — 切完再 GET `/status` 確認真的換了；沒換就重送。
5. **stand down** — 做完結束這一輪。

## 唯讀（GET，自動放行）

```jsonc
{ "method": "GET", "url": "http://127.0.0.1:7777/status" }       // 當值策略 + 理由 + 倉位 + 曝險 + mode
{ "method": "GET", "url": "http://127.0.0.1:7777/positions" }    // 持倉（strategy / entry_reason / stop）
{ "method": "GET", "url": "http://127.0.0.1:7777/pnl", "query": { "since": "2026-06-01" } }  // 損益 + 權益曲線
{ "method": "GET", "url": "http://127.0.0.1:7777/performance" }  // per-strategy 績效歸因（哪個策略在賺/賠）
{ "method": "GET", "url": "http://127.0.0.1:7777/strategy_history" }  // 策略切換時間軸（含 reason）
{ "method": "GET", "url": "http://127.0.0.1:7777/market", "query": { "symbol": "BTCUSDT", "tf": "1h", "limit": "100" } }
```

> User 在 `http://127.0.0.1:7777/dashboard` 看權益曲線 / 倉位 / 歸因 / 你的切換理由 / analyst commentary。

## Lever：切換策略（**僅你**；POST 會跳審批）

```jsonc
// 先 GET /status 看現況，再下令；reason 必填（會顯示在 User dashboard 的切換時間軸）
{ "method": "POST", "url": "http://127.0.0.1:7777/strategy",
  "body": { "symbol": "BTCUSDT", "strategy": "momentum", "reason": "<為什麼切：regime 讀數 + 依據>" } }
```

- 策略值：`momentum`（順勢）/ `flat`（空手，立即平倉）。
- **`reason` 必填**；漏了回 `400`。切完從回應 / 再 GET `/status` 驗證真的換了。

## Lever：叫停（緊急）

```jsonc
{ "method": "POST", "url": "http://127.0.0.1:7777/halt",
  "body": { "reason": "<為什麼>", "mode": "flat" } }   // flat=全平 / safe=凍新倉（既有倉留 stop）
```

## 心跳（你的 dead-man ping；timer 每 30m 叫你做）

```jsonc
{ "method": "POST", "url": "http://127.0.0.1:7777/heartbeat", "body": {} }
```

> Sunday 連續 90m 收不到 heartbeat → 自動進 safe-mode（凍新倉）。**別漏心跳。**

## 下令紀律

1. 切策略**前**先 GET `/status`——webhook payload 是「當時」，決策看「現在」。
2. 切策略**後**再 GET `/status` 確認真的換了；沒換就重送，別假設成功。
3. 服務重啟後先 GET `/status` 對帳再行動。

## 邊界

- **你不下單**——下單/平倉是 Sunday 的事。你只拉 meta lever（切策略 / 叫停）。
- **硬風控擋不過**——越線的單 Sunday 的 Python/交易所層仍拒（誰下令都擋）。
- **諮詢角色（analyst/risk-monitor/reporter/reviewer）不拉 lever**——他們 `send_message` 給你建議；採納或不採納，**回信告訴他們**。
- 細節、錯誤碼：`http_request` 取 `GET http://127.0.0.1:7777/manual`。
