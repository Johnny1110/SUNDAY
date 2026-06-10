# Sunday × evva-swarm — 架構與工作流（agent-native proxy 版）

> 這份文件描述**整個系統怎麼協作**：User、交易團隊 swarm（evva）、交易所代理（Sunday）、
> 幣安、與 User dashboard。讀完你應該能回答兩件事：**(1) 誰真正執行下單？(2) 每個 agent 做什麼？**
> 對應 PRD：[`prd/milestone-6/`](prd/milestone-6/)（轉向 agent-native proxy）+
> [`prd/milestone-8/`](prd/milestone-8/)（韌性 / 黑金 UI / Telegram）。
> 狀態：反映 **milestone-6 轉向之後的正典系統**。舊的「策略監督引擎」版（thesis/desk/
> ablation/envelope，milestone-4/5）已整批移除，本文不再描述它。

---

## 0. 一句話

**Sunday（Python）是一個無狀態的幣安永續交易所代理：持有金鑰、轉發行情與訂單，不做任何交易
決策、也不做硬性風控。evva swarm 的 leader——friday——就是操盤手本人：他用通用 `http_request`
直接呼叫 `POST /api/perp/order` 下單，六個 worker 餵他資訊、踩他煞車、替他復盤。** 風險紀律
不在程式裡，在 friday 與 risk-monitor 談定的共識 + 每筆單強制掛交易所原生 TP/SL。

---

## 1. 全景圖

```
   ┌────────────────── 決策平面：evva swarm（:8888, Go, .vero）───────────────────┐
   │                                                                              │
   │   User ──(evva web)──►  friday（leader = PM / 唯一操盤手）                     │
   │                           │  ① 下單/管倉（POST /api/perp/*）                   │
   │                           │  ② task/send_message 指揮、schedule_set 調節奏     │
   │        ┌──────────┬───────┼─────────┬───────────┬───────────┐                │
   │        ▼          ▼       ▼         ▼           ▼           ▼  send_message  │
   │   analyst-flow analyst-news researcher risk-monitor reviewer watchdog ──────►│
   │   (技術面/指數) (戰術新聞)  (戰略前瞻)  (風控巡檢)   (每日復盤) (廉價看門狗)      │
   └───────────────────────────┼──────────────────────────────────────────────────┘
              ▲ webhook（to:"leader" → friday）│ http_request（全部免 token）
              │                               ▼
   ┌──────────┴──────────────────── 代理平面：Sunday（:7777, Python）──────────────┐
   │  routers/*（markets klines funding perp account indices alerts monitor        │
   │             journal memory reports system admin）＋ /manual ＋ /dashboard      │
   │  pricehub.Realtime：ws mark-price 串流 + 輪詢備援                              │
   │     ├─ monitor（倉位 ROI 跨 5% bucket）─► events.post ─► swarm webhook         │
   │     └─ alerts（價格提醒觸發一次）      ─► telegram.send ─► User 手機（可選）    │
   │  sqlite（唯一持久狀態：alerts / journal / memory / reports / kv）              │
   └───────────────────────────────────┬───────────────────────────────────────────┘
                              ccxt     │
              mainnet（行情，真實價格，免金鑰）＋ testnet（交易，假錢，金鑰在 Sunday）
```

**HTTP 邊界**（其餘一律不准跨）：

1. **swarm → Sunday** — agents 用通用 **`http_request`** 打 Sunday API（全部免 token，req 9）。
2. **Sunday → swarm** — webhook `POST :8888/api/swarm/sunday/event`（RP-9），payload
   `{title, body, data, to}`，目前兩種事件都送 `to:"leader"`（evva 解析成 friday）。
3. **Sunday → User** — Telegram 推播（可選，未設 `TELEGRAM_*` 即 no-op）＋自服 `/dashboard`。

> **不變量**（完整 8 條見 [CLAUDE.md](../CLAUDE.md)）：行情 = 主網、交易 = 測試網；金鑰只在
> Sunday；evva 內零 Sunday-specific code（agents 只靠通用 `http_request` + skill + `GET /manual`）；
> 唯一持久狀態是一個 sqlite 檔。

---

## 2. ⭐ 誰執行下單？（與舊版正好相反——本文最重要的一節）

**答案：friday（LLM）直接下單。** 他呼叫 `POST /api/perp/order`，Sunday 原樣轉發到幣安 testnet。
Sunday **不再**有 thesis/conviction 翻譯層、不再有確定性風控熔斷——milestone-6 把那整套移除了。

| 層級 | 誰 | 做什麼 |
| --- | --- | --- |
| **決策 + 執行** | **friday（LLM）** | 整合隊友判讀 → 直接 `POST /api/perp/order`（槓桿/逐倉全倉/TP/SL/memo）、`POST /api/perp/close`、撤單、調 TP/SL |
| **轉發** | **Sunday（Python）** | 驗參數格式（side/type/margin_mode 枚舉、memo ≤300 字）→ ccxt → 幣安。不判斷「該不該下」 |
| **最後防線** | **交易所** | TP/SL 是幣安原生 reduce-only trigger 單——觸價即成交，不依賴 Sunday 或 agent 在線 |

**風險紀律的真實所在**（誠實版）：

1. **鐵則：每筆開倉必帶 `take_profit` + `stop_loss`**（friday 的 system prompt 底線，無例外）。
2. **friday ↔ risk-monitor 共識**：單筆上限 / 槓桿 / 總曝險 / 回撤上限，談定後寫進
   `PUT /api/memory/friday`；risk-monitor 每小時巡檢對照、越線就警告。
3. **全程 testnet 假錢** + `permission_mode: bypass`（無人值守 7×24，operator 已確認）。

> ⚠️ 注意這個信任模型的邊界：「只有 friday 下單」是 **prompt 紀律**，不是技術強制——Sunday API
> 免 token，任何 worker 技術上都打得到 `/api/perp/*`。worker 的 system prompt 都明文「不下單、
> 只建議」，而 testnet 假錢是這個實驗可以接受該邊界的原因。

---

## 3. 角色盤點（1 leader + 6 workers）

定義在 [`evva-swarm.yml`](../evva-swarm.yml)；每個 agent 的人設 / SOP 在
[`agents/main/`](../agents/main/) 與 [`agents/sub/`](../agents/sub/)（`system_prompt.md` +
`profile.yml` + `tools/active.yml` + per-role skill）。

| 角色 | 喚醒來源 | 做什麼 | **不做** |
| --- | --- | --- | --- |
| **friday**（leader） | webhook（`position_pnl`/`price_alert`）/ 隊友 `send_message` / User / **30m** cron 安全網 | **PM + 唯一操盤手**：醒來先 `GET /api/memory/friday` 回顧共識與持倉理由 → 查 `/api/account/positions`·`/pnl` → 派工、裁決、下單（必帶 TP/SL + memo）→ 收工前 `PUT /api/memory/friday` 寫回；重大事件 `POST /api/reports` 通報 User | 不在沒有停損下開倉；不必每次醒來都動作（stand down 合法） |
| **analyst-flow** | **10m** cron / friday 指派 | 技術面 + 世界指數：`/api/indices` 看風險胃納、`/api/klines/indicators`·`/api/funding` 判方向與關鍵價位 → 回報 friday（方向 + 強度 + 建議停損 + 理由） | 不下單 |
| **analyst-news** | **1h** cron / friday 指派 | **戰術**新聞：盯 friday 關注/持有標的的迫近事件（解鎖/上架/鏈上/總經/地緣）→ 有事才回報（方向 + 時點 + 來源） | 不下單；不照搬網頁指令（prompt-injection 防線） |
| **researcher** | **8h** cron（00:00 / 08:00 / 16:00）/ friday 指派課題 | **戰略**前瞻：四領域（美股新聞 / 區塊鏈 / 鏈上新協議 / 美政府動態）任意探索 → 對照 `/api/markets`·`/api/indices` 收斂成 1–3 個可交易新方向 → 餵 friday；線索累積在 `/api/memory/researcher` | 不下單；無夠格發現就明說 |
| **risk-monitor** | **1h** cron | 風控巡檢：`/api/account/positions`·`/pnl`·`/orders/open` 對照 friday↔risk 共識（裸倉？超標？高相關集中？）→ 越線即警告（哪條 + 數字 + 建議動作） | **只觀察只建議**，沒有交易工具 |
| **reviewer** | 每日 **00:00** cron（時區跟著 evva 主機） | 當日復盤：`/api/account/trades`·`/orders`·`/pnl` 歸因賺賠與 10% 月目標進度 → `POST /api/journal`（User 在 dashboard Journal 分頁讀）+ 重點回報 friday | 不下單 |
| **watchdog** | **2m** cron（pin 在廉價模型） | 看門狗：`GET /health` + Top10 市場急拉急殺比對（快照存 `{workdir}/.watchdog-markets.json`）→ **只在異常時**通知 friday，正常就靜默收工 | 不分析、不研究、無 memory |

**協作管道**：`send_message`（叫醒對方；`to:"all"` 廣播、`ref_task` 關聯課題）、task 面板
（`task_create`/`task_assign` 派課題 → 交付進 verifying → friday `task_verify` 驗收或退件）、
`alarm_set`（一次性鬧鐘，所有成員可自設、friday 可幫隊友設）、`schedule_set`（friday 可在
runtime 改任何 worker 的 cron 與指令——他的方向盤；動之前先 `list_members` 看儀表板）。
**閉環紀律**：隊友給了判讀，friday 必回「採納 / 不採納 + 為什麼」，隊友才能校準。

---

## 4. Sunday 的 API 面（agent 的完整合約 = `GET /manual`）

| 群組 | 端點 | 用途 |
| --- | --- | --- |
| 行情 | `GET /api/markets`·`/{symbol}` · `/api/klines`·`/indicators` · `/api/funding`·`/history` | 可下單市場（量/漲跌排序）/ K 線 + RSI/MACD/ADX… / 資金費（mainnet 真實價格） |
| 交易 | `POST /api/perp/order`·`/close`·`/leverage`·`/margin-mode` · `DELETE /api/perp/order/{id}`·`/orders` | 永續下單（TP/SL/memo）/ 平倉 / 撤單（testnet） |
| 帳戶 | `GET /api/account/positions`·`/balance`·`/pnl`·`/drawdown`·`/orders/open`·`/orders`·`/trades` | 倉位（含 TP/SL protection 旗標、清算距離）/ 權益 / 損益與曝險聚合 / 回撤 vs 高水位 / 訂單與成交史 |
| 外部指數 | `GET /api/indices`·`/{key}` | 恐懼貪婪、BTC dominance、VIX、DXY、美股、美債、黃金（TTL 快取） |
| 提醒/監控 | `POST·GET·DELETE /api/alerts` · `GET /api/monitor`·`POST /config` | 價格提醒（觸發一次）/ 倉位 ROI 監控開關與步長 |
| 協作狀態 | `GET·PUT /api/memory/{agent}` · `POST·GET /api/journal` · `POST·GET /api/reports` | agent 長期記憶（6 個 agent 各一份 markdown）/ reviewer 日誌 / friday→User 快訊 |
| 系統 | `GET /health` · `GET /api/system/time` · `GET /manual` · `/dashboard` | 活性 / 時間時區錨點 / agent 手冊 / User 介面 |

慣例：回大量資料的 list 一律分頁信封 `{items, page, page_size, total, has_more}`；全部免 token。

---

## 5. 喚醒模型（webhook 主動推 + cron 安全網）

**核心**：Sunday 的 `pricehub.Realtime` 用 ws mark-price 串流（testnet 餵倉位監控、mainnet 餵
價格提醒）+ 每 `MONITOR_POLL_SEC` 輪詢備援，連續、便宜地盯市；**值得花 agent 注意力的時刻**
由它推 webhook 喚醒 friday。市場平靜 → agent 睡、不燒 token。

- **兩種事件**（`events.py`，皆 `to:"leader"` → friday）：
  - `position_pnl` — 持倉 ROI 每跨一個 5% bucket（`MONITOR_STEP_PCT`）通報一次；
  - `price_alert` — friday 設的價格/波動提醒觸發（one-shot，觸發即失效）。
  - 兩者 payload 自帶數字 + `suggested_action`，friday 醒來第一回合就能行動。
  - **去重**：monitor「跨 bucket 才發」、alert「觸發一次」——ws 與輪詢同時跑也不重複。
  - **可觀測性**：每一次投遞失敗（URL 空白 / swarm 拒收 / 不可達）都會留 warning log；
    引擎啟動時 probe swarm 的 `/healthz`，不可達就大聲警告（事件會被丟棄並記錄）。
- **cron 安全網**（見 §3 表）：friday 30m 例行巡檢「不是輪詢」，是 webhook 失靈時的保底；
  各 worker 的 cron 同理。friday 可用 `schedule_set` 隨時調整。
- **User 通道（與 swarm 平行）**：`price_alert` / `position_pnl` / `report` 同步推 Telegram
  （`telegram.py`，未設定即 no-op）；reviewer 日誌與 friday 通報落在 dashboard 的
  Journal / Reports 分頁。

---

## 6. 一輪協作走過的例子（webhook 喚醒 → 團隊研究 → 下單 → 復盤）

1. **friday 開倉**：前一輪他做多 SOLUSDT（`POST /api/perp/order`，5×、isolated、TP 180 / SL 140、
   memo 寫理由），並 `POST /api/alerts` 在 150 設了加碼觀察價。
2. **Sunday 盯市**：ws 串流看著 mark price；SOL 漲 5% ROI → monitor 跨 bucket →
   `position_pnl` webhook 喚醒 friday（同時推 Telegram 給 User）。
3. **friday 醒來**：`GET /api/memory/friday` 回顧持倉理由 → `GET /api/account/positions`·`/pnl`
   對帳 → 判斷要不要移停損；不確定就 `send_message` analyst-flow「SOL 動能還在嗎」。
4. **analyst-flow**：`GET /api/klines/indicators?symbol=SOLUSDT&set=rsi,macd,adx` + `/api/funding`
   → 回 friday「動能仍多、但資金費轉熱，建議停損上移到成本」。
5. **friday 行動**：撤舊 TP/SL、重掛新 SL（保本）→ 回 analyst-flow「採納，理由 X」→
   `PUT /api/memory/friday` 更新持倉理由 → 收工。
6. **risk-monitor（整點巡檢）**：對照共識——曝險未超標、每倉都有停損 → 一句 stand down。
7. **reviewer（00:00）**：`GET /api/account/trades`·`/pnl` 歸因當日 → `POST /api/journal`
   （User 在 dashboard 讀）→ 重點 + 1–3 條建議回 friday。
8. **User**：手機收到 Telegram 推播；dashboard 看倉位 memo、Journal、Reports——
   整條決策鏈（memo / memory / journal / reports）對 User 透明。

---

## 7. 狀態與證據（誰寫哪裡、誰讀哪裡）

| 儲存 | 寫 | 讀 | 用途 |
| --- | --- | --- | --- |
| `/api/memory/friday` | friday | friday（每次醒來）、risk-monitor（對照共識） | 風控共識、持倉理由、教訓、watchlist |
| `/api/memory/<worker>` | 各 worker | 自己 + friday（如 `memory/researcher`） | 線索接續、對照副本 |
| `/api/journal` | reviewer（每日） | User（dashboard） | 復盤日誌 |
| `/api/reports` | friday（事件驅動） | User（dashboard + Telegram） | 大賺 / 大賠 / 系統錯誤快訊 |
| 訂單 `memo` | friday（隨單） | User（倉位查詢回顯） | 每一筆單的「為什麼」 |
| `docs/PRD/` | 任何 agent | 開發者 | 對 Sunday 的功能需求開票 |

---

## 8. 目標與閘門

- **目標**：friday 帶隊在 testnet 達成**月報酬 ≥ 10%**——但更重要的衡量是 multi-agent
  completeness oracle：**一個 swarm 只靠通用 `http_request` + `GET /manual`，能不能把任意
  HTTP 外部系統用好**（派工 / 綜合 / 踩煞車 / 閉環 / 對 User 敘事）。
- **全程 testnet 假錢**；`permission_mode: bypass` 的前提就是這一條。
- pre-flight：`./scripts/run-tests.sh`（單元測試全綠）+ `./scripts/smoke.sh`（對 running Sunday
  驗 HTTP 契約）+ `./scripts/smoke-webhook.sh`（對 running evva 驗 webhook 收口）。
