# Sunday — Claude Code 開發指引

> **milestone-6 起轉向（2026-06-09）：產品 = 為 agent 設計的 Binance USDⓈ-M 永續交易所代理。**
> 先讀 [docs/prd/milestone-6/README.md](docs/prd/milestone-6/README.md)。舊的「策略監督交易引擎」
> 方向（thesis/desk/ablation/risk-envelope）已**整批移除**，不再發展。

## 我們在蓋什麼

一個**無狀態的 Binance 代理**：agent 用通用 HTTP 工具（`http_request` / curl）操作 Sunday，就像
人類用幣安——查可下單市場、讀 K 線/指標/資金費、下永續單（槓桿/逐倉全倉/止盈止損）、查倉位損益與
訂單、看外部總經指數、設定價格與倉位提醒。Sunday 持有金鑰，agent 只需要 HTTP。

## 不可違反的不變量

1. **行情 = 主網（真實價格、免金鑰）；交易 = 測試網（假錢、需金鑰）。** `exchange.py` 兩個 ccxt
   實例：`market_ex()`（mainnet）給所有行情；`trade_ex()`（testnet sandbox）給帳戶/下單。
2. **所有 API 免 token。** Sunday 持金鑰，agent 只持 HTTP。port 上不放交易所金鑰給 agent 看。
3. **回傳大量資料的 list 一律分頁**（`pagination.paginate` 的統一信封 `{items,page,page_size,total,has_more}`）。
4. **API prefix 依模組劃分**（req 10）：`/api/markets` `/api/klines` `/api/funding` `/api/perp`
   `/api/account` `/api/indices` `/api/alerts` `/api/monitor` `/api/journal` `/api/memory`
   `/api/reports` `/api/system` `/api/admin`（admin 僅 operator 用、不對 agent 公告），各自一個
   `routers/` 模組。
5. **無 Postgres/Redis。** 唯一持久狀態 = 一個 sqlite 檔（alerts / order_log / journal / memory /
   reports / equity_snap / kv，`store.py`，stdlib `sqlite3`）。
   sqlite wrapper 用**可重入寫鎖（RLock）**序列化所有存取 + WAL/busy_timeout（多執行緒寫入會死鎖）。
6. **純邏輯 stdlib-only、可在任何環境單元測試**（indicators / pagination / alerts 規則 / monitor 數學 /
   indices parsers / events builders）。重依賴（ccxt/fastapi/config）一律**惰性 import**，別在模組頂層。
7. **realtime 用 websocket + 輪詢備援**：`pricehub.Realtime` 跑 ws mark-price 串流（testnet 餵倉位監控、
   mainnet 餵價格提醒）+ 每 `MONITOR_POLL_SEC` 輪詢備援。monitor 以「跨 bucket 才發」、alert「觸發一次」，
   故 ws 與輪詢同時跑也不會重複通知。
8. **兩條對外通道（milestone-8 擴充）**：(a) **evva swarm webhook**（agent-facing，不變）：
   Sunday → evva swarm（`events.post` → `EVVA_WEBHOOK_URL`），事件 `position_pnl` / `price_alert`，
   payload `{title,body,data,to}`，自帶 `suggested_action`；(b) **Telegram**（User-facing，可選）：
   `telegram.py`，report / price_alert / position_pnl 推 User 手機。未設 `TELEGRAM_BOT_TOKEN`/
   `TELEGRAM_CHAT_ID` 即 no-op，行為與原本完全一致。兩者皆 fire-and-forget、永不 raise；金鑰只在
   引擎側（延續不變量 2）。

## 專案結構

```
engine/sunday/
├── app.py            FastAPI 組裝（lifespan + router 掛載 + 系統路由 + realtime 啟停）
├── exchange.py       雙 ccxt（mainnet 行情 / testnet 交易）；時鐘偏移加固（防 -1021）
├── store.py          sqlite（alerts/order_log/journal/memory/reports/equity_snap/kv）；RLock 寫鎖
├── config.py         proxy 設定（pydantic-settings）
├── indicators.py     純指標：sma/ema/rsi/bollinger/adx/macd/atr
├── pagination.py     統一分頁 + 排序
├── indices.py        外部指數 feed + TTL 快取（F&G/CoinGecko/Stooq+Yahoo）
├── alerts.py         價格提醒規則 + 引擎（注入式 notify）
├── monitor.py        倉位 ROI/bucket 監控（注入式 notify）
├── protection.py     純風控數學：TP/SL 腿/裸倉判定、曝險聚合、清算距離、回撤
├── pricehub.py       Realtime：ws 串流 + 輪詢備援
├── events.py         evva webhook builders + post（stdlib urllib）
├── telegram.py       User-facing Telegram 推播（stdlib urllib；未設定即 no-op）
├── routers/          每模組一檔（markets/klines/funding/perp/account/indices/alerts/monitor/
│                     journal/memory/reports/system/admin）
├── manual.md         agent API 手冊（GET /manual）
└── web/              TS + Vue 3 dashboard（Vite → dist/，FastAPI 自服於 / 與 /ui）
```

## 技術棧

- 引擎：Python ≥ 3.11，FastAPI + uvicorn，ccxt（USDⓈ-M），`websockets`，stdlib `sqlite3`。
- 前端：Vite + Vue 3 + TypeScript + vue-router + lightweight-charts（`npm run build` → `dist/`，committed）。
- 無 pandas/numpy（指標為純 Python）；webhook 用 urllib（無 httpx）。

## 與 evva 的關係（重要）

- evva 是 swarm runtime，**獨立 Go 專案在 `../evva`**。本專案是 evva swarm 的**被使用對象**：agent 用
  通用 `http_request` 操作 Sunday，Sunday 用 webhook 回推事件。**不從這裡改 evva。**
- Sunday 只消費 evva 公開介面（`POST /api/swarm/{ref}/event`，RP-9）。這仍是 multi-agent completeness
  oracle 的重點：swarm 只靠通用工具 + `/manual` 文件就能驅動任意 HTTP 外部系統。

## 慣例

- testnet 金鑰放 `engine/.env`，**永不 commit**（已 gitignore）。
- commit 用 conventional prefix（`feat`/`fix`/`chore`/`docs`/`refactor`/`test`）。
- 測試貼著程式碼（`tests/*_test` 不適用；本 repo 用 `tests/test_*.py`，`./scripts/run-tests.sh`）。
- 動工前先確認沒違反上面 8 條不變量。新 SQLite store 一律沿用 RLock 寫鎖模式。

## 現況

- **milestone-9 = sunday-mcp sidecar（typed MCP 工具通道，混合制）**（見
  [docs/prd/milestone-9/README.md](docs/prd/milestone-9/README.md)，S1–S7 新增不變量先讀）：
  `engine/sunday_mcp/` 無狀態 sidecar（`python -m sunday_mcp`，:7780/mcp → :7777，金鑰永不進
  sidecar），22 個工具 = 13 唯讀 + 8 寫入 + ping，外加 `sunday://manual` resource。寫入 schema
  強制 `agent`/`take_profit`/`stop_loss`/`memo`（裸單不可表達）；`validate.py` 純函式交叉驗證；
  寫入零自動重試、連線失敗回 placed-or-not UNKNOWN。`http_request` + `/manual` 永遠是降級通道
  （S6）；kill-switch = `.evva/settings.json` 設 `disabled:true` 重啟 swarm。Phase 1–3 已交付
  （單測 + testnet 全鏈路 smoke 綠）；Phase 4 上線中（settings/prompts/RUNBOOK §10/manual MCP 節
  已 commit，兩週評估與裁決見 PRD-9.4）。
- **milestone-6 = agent-native proxy（地基，現行）。** **milestone-8 = 韌性 / 黑金 UI / Telegram**
  （見 [docs/prd/milestone-8/README.md](docs/prd/milestone-8/README.md)）：
  (1) Binance `-1021` 時鐘偏移加固（`_signed` round-trip 校時 + 落後安全偏壓 + recvWindow 10s +
  自癒重試；ccxt `trade_ex` 開 `adjustForTimeDifference`）；(2) 前端改黑金配色、全面 responsive
  不跑版（`.split`/`.split-r` + 側欄抽屜）；(3) `telegram.py` 把 report / 提醒 / 持倉損益推 User 手機。
- **187 單元測試綠**（含 telegram formatter、webhook 投遞失敗 log / boot probe、
  protection 風控數學與 equity 快照測試）；前端 `vue-tsc` + `vite build` 綠、`dist/` 已重建。
- **BUG-01～04 已修復（2026-06-12，見 docs/prd/bug-report/）**：(1) TP/SL 腿改
  `workingType=MARK_PRICE` + 下單/protection 前置觸發區驗證——根因是預設 CONTRACT_PRICE 用
  **測試網成交價**判定觸發（與 agent 決策依據的主網價脫鉤），且 Algo Service 對已在觸發區的腿
  不回 -2021 而是直接成交 = 一掛即市價平倉（BUG-01/04）；(2) 倉位歸零自動撤孤兒 TP/SL 腿：
  `/api/perp/close` 平倉即清 + monitor 輪詢偵測倉位消失時清（帶 server-clock 戳記，不誤殺同窗
  重開倉的新腿）（BUG-02）；(3) 稽核帳本：`/api/perp` 寫入帶 `X-Agent` 記入 order_log
  agent/action 欄，account 訂單/成交查詢回 `agent` 並可 `?agent=` 過濾（BUG-03）。
- **PRD-005 已修復（2026-06-11）**：indicators 路徑套上 `ttlcache.StaleCache`（TTL 隨 interval
  比例、上游故障供應 last-good + `stale: true`）——調查證實程式無 1h 特定路徑，缺陷是上游
  卡頓無退化策略；新 cache 模組可給其他唯讀路徑重用（markets router 是同語義的前例）。
- **PRD-004 已修復（2026-06-11）**：`monitor.bucket()` 由 floor 改截斷向零——0 不再是階梯邊界，
  (−5%,+5%) 同屬打平帶，倉位貼著進場價震盪不再每 tick 推 webhook；`refresh_book` 在倉位身分
  （entry/qty/lev）改變時靜默重置 bucket 基準，重開倉不誤發。
- **PRD-003 已修復（2026-06-11）**：Binance 2025-12-09 把條件單遷到 Algo Service，TP/SL 腿
  從此不在 `/fapi/v1/openOrders`。`exchange.py` 讀取/撤單改兩本訂單簿合併（`algoorders.py`
  純映射，腿帶 `algo: true`），`-2011` 自動轉打 algo 簿；新增 `GET/POST /api/perp/protection`
  （先掛新腿、後撤舊腿）；`ccxt>=4.5.57` 釘版（舊版 -4120 拒掛）。
- swarm 消費端（`evva-swarm.yml` + `agents/`：1 leader friday + 6 workers）：**交易權集中在
  friday 一人**（2026-06-12 裁撤 trader 執行台——決策/執行分離造成「持有 vs 平倉」雙頭打架）：
  friday = 指揮官/PM + 唯一下單者（決策 + 親自執行 `/api/perp`、管倉對帳、調度驗收），
  risk-monitor 是唯一外部煞車；`position_pnl` webhook 一律喚醒 leader（`MONITOR_WEBHOOK_TO`
  維持預設 `leader`）。
- **已對齊 evva 第五波（RP-19~28）**：工具教學/deferred 公告/injection 防線/記憶協議由框架
  注入，persona 只寫人設與行規；成員私人記憶原生化（`agents/…/<name>/memory/`，已 gitignore），
  `/api/memory` 收斂為兩塊公告板（friday 憲法 + researcher 研究日誌）；risk-monitor 的缺陷
  告警直達 friday 並用鬧鐘追蹤（交易權集中後不再向執行台開看板提案）；共享 skills 在
  `agents/skills/`（friday 可 `skill_publish`）。
  系統協作全景見 [docs/workflow.md](docs/workflow.md)。
