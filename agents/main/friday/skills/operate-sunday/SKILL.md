# operate-sunday 操作 Sunday 交易引擎：查狀態、切策略、叫停、心跳（leader 專用）

Sunday 是我們的交易引擎（Binance USDⓈ-M 永續 **testnet**），在 `http://127.0.0.1:7777`。
**它自己交易，你監督它。你不下單。** 你用 **`http_request` 工具**操作它（不是 shell/curl）：
傳結構化的 `{method, url, headers?, query?, body?}`，拿回 `status + 解析後的 body`。

- **GET/HEAD 自動放行**（唯讀，免審批）；**POST/PUT/DELETE 會跳 permission 審批**（lever，僅你）。
- **非 2xx 也會回傳**（不是錯誤）——所以你讀得到 409/400 的 body 並據以反應。
- 完整 API 隨時用 `http_request` 取 `GET http://127.0.0.1:7777/manual`。

---

## 監督節奏（每次被喚醒都照這個走）

1. **重抓現況** — 別只信喚醒你的 webhook payload（那是「當時」）。先 GET `/status`（+需要時 `/signals`）。
2. **判斷** — regime 真的變了、值得切策略嗎？平靜無事就回報並 stand down。
3. **行動** — 要切策略/叫停才拉 lever（見下，**附 `reason`**）。
4. **驗證** — 從 lever 回應的 `resulting_status` 確認；沒換或 409 就重判重送。
5. **stand down** — 做完結束這一輪，省 token。

## 唯讀（GET，自動放行）

```jsonc
// 整體狀態：當值策略 + 理由 + 倉位 + 曝險 + as_of_ts + last_lever + votes 摘要
{ "method": "GET", "url": "http://127.0.0.1:7777/status" }

// 決策面板：每個候選策略此刻的投票 + 指標 + regime 讀數（直接讀，別自己算）
{ "method": "GET", "url": "http://127.0.0.1:7777/signals", "query": { "symbol": "BTCUSDT" } }

// 某次切換的結果（賺賠 / 筆數 / 勝率）
{ "method": "GET", "url": "http://127.0.0.1:7777/strategy/outcomes", "query": { "symbol": "BTCUSDT" } }

// 倉位 / 損益
{ "method": "GET", "url": "http://127.0.0.1:7777/positions" }
{ "method": "GET", "url": "http://127.0.0.1:7777/pnl", "query": { "since": "2026-06-01" } }
```

## Lever：切換策略（**僅你**；POST 會跳審批）

防禦式三步——先 GET 看現況、帶 `expected_current` 下令、從回應驗證：

```jsonc
// 1) 先 GET /status，記下回應裡的 "strategy"（= 當前當值策略）
// 2) 帶 expected_current 下令（若狀態已被改掉，引擎回 409 不誤套）
{ "method": "POST", "url": "http://127.0.0.1:7777/strategy",
  "body": { "symbol": "BTCUSDT", "strategy": "mean_reversion",
            "reason": "analyst 判轉震盪，ADX 跌破 20", "expected_current": "momentum" } }
// 3) 驗證：回應的 resulting_status.strategy 應為 "mean_reversion"（免再 GET 一次）
```

- **`reason` 必填**——留存給 User；漏了回 `400 reason_required`。
- **回 `409 {error:"stale", current_status}`**：你的視圖過期。讀 `current_status` 重新判斷再送一次。
- 策略值：`momentum`（順勢）/ `mean_reversion`（逆勢震盪）/ `flat`（空手，會立即平倉）。

## Lever：叫停（緊急）

```jsonc
// mode=flat 全平+停；mode=safe 凍新倉（既有倉留交易所 stop）
{ "method": "POST", "url": "http://127.0.0.1:7777/halt",
  "body": { "reason": "risk_breach 後人工複核，先凍倉", "mode": "safe" } }
```

## 心跳（你的 dead-man ping；timer 每 30m 會叫你做）

```jsonc
{ "method": "POST", "url": "http://127.0.0.1:7777/heartbeat", "body": {} }
```

> Sunday 連續 ~90m 收不到 heartbeat → 自動進 safe-mode（凍新倉）。**別漏心跳。**
> 注意：`/heartbeat` 是 POST，會跳審批；按 Allow（或對它「Always allow」讓例行心跳免審批）。

---

## 下令紀律（§7.10，違反會誤動作）

1. **切策略前先 GET `/status`**——webhook payload 是「當時」，決策要看「現在」。
2. **切策略後從回應的 `resulting_status` 驗證**——沒換或 409 就重判重送，別假設成功。
3. **服務重啟後先 GET `/status` 對帳再行動**——你恢復的記憶可能過期。

## 邊界（硬規則）

- **你不下單**——下單/平倉是 Sunday 的事。你只拉 meta lever（切策略 / 叫停）。
- **硬風控擋不過**——即使你下了越線指令，Sunday 的 Python/交易所層仍拒單。lever 是方向盤，不是油門。
- **諮詢角色（analyst/risk/reporter/reviewer）不拉 lever**——他們 `send_message` 給你建議；採納或不採納，**回信告訴他們**。
- 細節、錯誤碼、封套語意：`http_request` 取 `GET http://127.0.0.1:7777/manual`。
