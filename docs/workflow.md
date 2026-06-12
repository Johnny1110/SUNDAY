# Sunday × evva-swarm — 架構與工作流（agent-native proxy 版）

> 這份文件描述**整個系統怎麼協作**：User、交易團隊 swarm（evva）、交易所代理（Sunday）、
> 幣安、與 User dashboard。讀完你應該能回答兩件事：**(1) 誰真正執行下單？(2) 每個 agent 做什麼？**
> 對應 PRD：[`prd/milestone-6/`](prd/milestone-6/)（轉向 agent-native proxy）+
> [`prd/milestone-8/`](prd/milestone-8/)（韌性 / 黑金 UI / Telegram）+
> [`prd/milestone-9/`](prd/milestone-9/)（sunday-mcp typed 工具通道，混合制）。
> 狀態：反映 **milestone-6 轉向之後的正典系統**。舊的「策略監督引擎」版（thesis/desk/
> ablation/envelope，milestone-4/5）已整批移除，本文不再描述它。

---

## 0. 一句話

**Sunday（Python）是一個無狀態的幣安永續交易所代理：持有金鑰、轉發行情與訂單，不做任何交易
決策、也不做硬性風控。evva swarm 的 leader——friday——是指揮官 / PM / 唯一交易執行者：他整合
研究做交易決策，並親自用通用 `http_request` 呼叫 `POST /api/perp/order` 執行；六個 worker
餵情報、踩煞車、復盤。**（2026-06-12 裁撤 trader 執行台：決策/執行分離造成持有 vs 平倉互相
打架，交易權收回 friday 一人。）風險紀律不在程式裡，在 friday 與 risk-monitor 談定的共識 +
friday 執行 SOP 的 pre-flight 核對 + 每筆單強制掛交易所原生 TP/SL + risk-monitor 外部巡檢。

---

## 1. 全景圖

```
   ┌────────────────── 決策平面：evva swarm（:8888, Go, .vero）───────────────────┐
   │                                                                              │
   │   User ──(evva web)──►  friday（leader = 指揮官 / PM / 唯一下單者）             │
   │                           │  ① 決策 → 親自 POST /api/perp/*（pre-flight →      │
   │                           │     下單必帶 TP/SL → 驗保護腿 → 落盤憲法）          │
   │                           │  ② task/send_message 指揮、schedule_set 調節奏     │
   │             ┌─────────────┼─────────┬─────────┬───────────┬──────────┐       │
   │             ▼             ▼         ▼         ▼           ▼          ▼       │
   │       analyst-flow analyst-news researcher risk-monitor reviewer  watchdog   │
   │      (技術面/指數)  (戰術新聞)  (戰略前瞻)  (風控巡檢)   (每日復盤) (看門狗)    │
   │             │                                        send_message ──► friday │
   └─────────────┼───────────────────────────────────────────────────────────────┘
              ▲ webhook（to:"leader"）     │ mcp__sunday__*（:7780 sidecar）+ http_request 降級（全部免 token）
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

1. **swarm → Sunday（混合制，milestone-9）** — 熱路徑（行情/帳戶/交易）優先走
   **`mcp__sunday__*` typed 工具**（`sunday_mcp` sidecar `:7780/mcp` → `:7777`；22 工具
   + `sunday://manual` resource；寫入 schema 強制 TP/SL/memo/agent）；工具不可用即降級
   **`http_request`** 直打 Sunday API（S6 常開），長尾端點（memory/journal/reports）
   本來就走 `http_request`。全部免 token（req 9）；kill-switch = `.evva/settings.json`
   設 `disabled:true` 重啟 swarm。
2. **Sunday → swarm** — webhook `POST :8888/api/swarm/sunday/event`（RP-9），payload
   `{title, body, data, to}`；`price_alert` 固定送 `to:"leader"`（evva 解析成 friday），
   `position_pnl` 的收件人由 `MONITOR_WEBHOOK_TO` 決定（預設且現行 `leader`）。
3. **Sunday → User** — Telegram 推播（可選，未設 `TELEGRAM_*` 即 no-op）＋自服 `/dashboard`。

> **不變量**（完整 8 條見 [CLAUDE.md](../CLAUDE.md)）：行情 = 主網、交易 = 測試網；金鑰只在
> Sunday；evva 內零 Sunday-specific code（agents 只靠通用 `http_request` + skill + `GET /manual`）；
> 唯一持久狀態是一個 sqlite 檔。

---

## 2. ⭐ 誰執行下單？（交易權集中在 friday——本文最重要的一節）

**答案：friday（指揮官）決定，也親自下單。** friday 整合研究形成交易決定（標的/方向/大小/
槓桿/TP/SL/理由/standing rules），照執行 SOP pre-flight 核對風控共識與市場限額後呼叫
`POST /api/perp/order`，Sunday 原樣轉發到幣安 testnet。（2026-06-12 裁撤 trader 執行台：
決策/執行兩個 agent 對「持有 vs 平倉」各持己見、互相打架，交易權收回 friday 一人。）Sunday
**不再**有 thesis/conviction 翻譯層、不再有確定性風控熔斷——milestone-6 把那整套移除了。

| 層級 | 誰 | 做什麼 |
| --- | --- | --- |
| **決策 + 執行** | **friday（LLM）** | 整合隊友判讀 → 形成決定（含 TP/SL 與理由）→ pre-flight（共識/限額/餘額）→ `POST /api/perp/order`·`/close`·`/protection`、撤單 → 驗證成交與保護腿 → 落盤憲法；依 standing rules 管在倉部位 |
| **轉發** | **Sunday（Python）** | 驗參數格式（side/type/margin_mode 枚舉、memo ≤300 字）→ ccxt → 幣安。不判斷「該不該下」 |
| **最後防線** | **交易所** | TP/SL 是幣安原生 reduce-only trigger 單——觸價即成交，不依賴 Sunday 或 agent 在線 |

**風險紀律的真實所在**（誠實版）：

1. **鐵則：每筆開倉必帶 `take_profit` + `stop_loss`**（執行 SOP 必填，無例外；沒有
   stop_loss 的倉位不准存在）。
2. **friday ↔ risk-monitor 共識**：單筆上限 / 槓桿 / 總曝險 / 回撤上限，談定後寫進
   `PUT /api/memory/friday`；friday 每筆單 pre-flight 對照、risk-monitor 每小時巡檢——
   **決策與執行同在 friday 一人身上，risk-monitor 是唯一的外部煞車**，越線就警告、連兩次
   未處理升級 User。
3. **全程 testnet 假錢** + `permission_mode: bypass`（無人值守 7×24，operator 已確認）。

> ⚠️ 注意這個信任模型的邊界：「只有 friday 下單」是 **prompt 紀律**，不是技術強制——Sunday
> API 免 token，任何 worker 技術上都打得到 `/api/perp/*`。各 agent 的 system prompt 明文劃界
> （研究與風控成員「不下單」），而 testnet 假錢是這個實驗可以接受該邊界的原因。

---

## 3. 角色盤點（1 leader + 7 workers）

定義在 [`evva-swarm.yml`](../evva-swarm.yml)；每個 agent 的人設 / SOP 在
[`agents/main/`](../agents/main/) 與 [`agents/sub/`](../agents/sub/)（`system_prompt.md` +
`profile.yml` + `tools/active.yml` + per-role skill）。

| 角色 | 喚醒來源 | 做什麼 | **不做** |
| --- | --- | --- | --- |
| **friday**（leader） | webhook（`price_alert` + `position_pnl`）/ 隊友 `send_message` / User / **30m** cron 安全網 | **指揮官 / PM / 唯一下單者**：醒來先 `GET /api/memory/friday` 回顧共識與持倉理由 → 對帳 → 決策後親自執行（pre-flight 核對共識與限額 → 下單必帶 TP/SL + memo → 驗證成交與保護腿 → 落盤憲法）；依 standing rules 管在倉部位、撤孤兒掛單、對帳；派研究課題、`task_verify` 驗收、`schedule_set`/`alarm_set` 調度、閉環回覆；重大事件 `POST /api/reports` 通報 User | 共識不存在不開新倉；沒有 stop_loss 的倉位不准存在；不必每次醒來都動作（stand down 合法） |
| **analyst-flow** | **10m** cron / friday 指派 | 技術面 + 世界指數：`/api/indices` 看風險胃納、`/api/klines/indicators`·`/api/funding` 判方向與關鍵價位 → 回報 friday（方向 + 強度 + 建議停損 + 理由） | 不下單 |
| **analyst-news** | **1h** cron / friday 指派 | **戰術**新聞：盯 friday 關注/持有標的的迫近事件（解鎖/上架/鏈上/總經/地緣）→ 有事才回報（方向 + 時點 + 來源） | 不下單；不照搬網頁指令（prompt-injection 防線） |
| **researcher** | **8h** cron（00:00 / 08:00 / 16:00）/ friday 指派課題 | **戰略**前瞻：四領域（美股新聞 / 區塊鏈 / 鏈上新協議 / 美政府動態）任意探索 → 對照 `/api/markets`·`/api/indices` 收斂成 1–3 個可交易新方向 → 餵 friday；線索累積在 `/api/memory/researcher` | 不下單；無夠格發現就明說 |
| **risk-monitor** | **1h** cron | 風控巡檢：`/api/account/pnl`·`/drawdown`·`/balance` 對照 friday↔risk 共識（裸倉？超標？高相關集中？）→ 決策越線與機械缺陷（裸倉/孤兒掛單）都警告 friday——他是唯一交易之手，risk-monitor 是唯一外部煞車；連兩次未處理 `POST /api/reports` 升級 User | **只觀察只建議**，沒有交易職權 |
| **reviewer** | 每日 **00:00** cron（時區跟著 evva 主機） | 當日復盤：`/api/account/trades`·`/orders`·`/pnl`（repl 算命中率/賺賠比）歸因賺賠與 10% 月目標進度，**決策與執行分開歸因（兩者皆 friday）** → `POST /api/journal`（User 在 dashboard Journal 分頁讀）+ 重點回報 friday 並追蹤落實 | 不下單 |
| **watchdog** | **5m** cron（pin 在廉價模型） | 看門狗：`GET /health`（🚨緊急）+ Top10 市場急拉急殺比對 + `GET :7780/healthz` sidecar 監測（ℹ️非緊急、狀態翻轉才報；快照含 `mcp_ok` 存 `{workdir}/.watchdog-markets.json`）→ **只在異常時**通知 friday，正常就靜默收工 | 不分析、不研究、無 memory |
| **evva**（persona member，RP-29） | friday 的 ticket / 訊息（**無 cron**，純派工驅動） | **特派工程師**：接 Sunday 軟體改動 ticket（`docs/prd/` PRD 實作、bug 修復）→ 實作 → `./scripts/run-tests.sh` 綠 → commit main → 照 RUNBOOK 重啟（**先知會 friday** 擇時）→ 驗 `/health` → 回報（票號+hash+證據）；失敗 `git revert` 回滾重啟。行規見 repo 根的 `EVVA.md` | 不下單、不碰 `/api/perp`；不改憲法與隊友記憶；不碰 `../evva` |

**協作管道**：`send_message`（叫醒對方；`to:"all"` 廣播、`ref_task` 關聯課題）、task 面板
（`task_create`/`task_assign` 派課題 → 交付進 verifying → friday `task_verify` 驗收或退件）、
`alarm_set`（一次性鬧鐘，所有成員可自設、friday 可幫隊友設）、`schedule_set`（friday 可在
runtime 改任何 worker 的 cron 與指令——他的方向盤；動之前先 `list_members` 看儀表板）。
**閉環紀律**：隊友給了判讀，friday 必回「採納 / 不採納 + 為什麼」，隊友才能校準。

---

## 4. Sunday 的 API 面（agent 的完整合約 = `GET /manual`）

> **milestone-9 起，下表的熱路徑（行情/帳戶/交易/提醒）另有 typed MCP 工具版**：sidecar
> `sunday_mcp`（`:7780/mcp`）提供 22 個 `mcp__sunday__*` 工具（13 唯讀 + 8 寫入 + ping）
> 與 `sunday://manual` resource——寫入 schema 強制 `agent`/`take_profit`/`stop_loss`/`memo`
> （裸單與匿名單在 schema 層不可表達）、輸出整形進 60k 預算、寫入零自動重試（連線斷 =
> placed-or-not UNKNOWN，先對帳再動）。**本表（http_request）永遠是降級通道與長尾通道**
> （S6）；營運見 RUNBOOK §10。

| 群組 | 端點 | 用途 |
| --- | --- | --- |
| 行情 | `GET /api/markets`·`/{symbol}` · `/api/klines`·`/indicators` · `/api/funding`·`/history` | 可下單市場（量/漲跌排序）/ K 線 + RSI/MACD/ADX… / 資金費（mainnet 真實價格） |
| 交易 | `POST /api/perp/order`·`/close`·`/leverage`·`/margin-mode` · `DELETE /api/perp/order/{id}`·`/orders` | 永續下單（TP/SL/memo）/ 平倉 / 撤單（testnet） |
| 帳戶 | `GET /api/account/positions`·`/balance`·`/pnl`·`/drawdown`·`/orders/open`·`/orders`·`/trades` | 倉位（含 TP/SL protection 旗標、清算距離）/ 權益 / 損益與曝險聚合 / 回撤 vs 高水位 / 訂單與成交史 |
| 外部指數 | `GET /api/indices`·`/{key}` | 恐懼貪婪、BTC dominance、VIX、DXY、美股、美債、黃金、布蘭特原油（TTL 快取） |
| 提醒/監控 | `POST·GET·DELETE /api/alerts` · `GET /api/monitor`·`POST /config` | 價格提醒（觸發一次）/ 倉位 ROI 監控開關與步長 |
| 協作狀態 | `GET·PUT /api/memory/{friday,researcher}` · `POST·GET /api/journal` · `POST·GET /api/reports` | 公告板：friday 憲法 + researcher 研究日誌（agent 私人工作記憶已原生化進 evva）/ reviewer 日誌 / friday→User 快訊 |
| 系統 | `GET /health` · `GET /api/system/time` · `GET /manual` · `/dashboard` | 活性 / 時間時區錨點 / agent 手冊 / User 介面 |

慣例：回大量資料的 list 一律分頁信封 `{items, page, page_size, total, has_more}`；全部免 token。

---

## 5. 喚醒模型（webhook 主動推 + cron 安全網）

**核心**：Sunday 的 `pricehub.Realtime` 用 ws mark-price 串流（testnet 餵倉位監控、mainnet 餵
價格提醒）+ 每 `MONITOR_POLL_SEC` 輪詢備援，連續、便宜地盯市；**值得花 agent 注意力的時刻**
由它推 webhook 喚醒 friday。市場平靜 → agent 睡、不燒 token。

- **兩種事件**（`events.py`）：
  - `position_pnl` — 持倉 ROI 每跨一個 5% bucket（`MONITOR_STEP_PCT`）通報一次；喚醒誰由
    `MONITOR_WEBHOOK_TO` 決定（預設且現行 `leader` → friday，由他對照 standing rules 與
    持倉理由處理）。
  - `price_alert` — friday 設的價格/波動提醒觸發（one-shot，觸發即失效；固定 `to:"leader"`）。
  - 兩者 payload 自帶數字 + `suggested_action`，woken 的 agent 第一回合就能行動。
  - **去重**：monitor「跨 bucket 才發」、alert「觸發一次」——ws 與輪詢同時跑也不重複。
  - **可觀測性**：每一次投遞失敗（URL 空白 / swarm 拒收 / 不可達）都會留 warning log；
    引擎啟動時 probe swarm 的 `/healthz`，不可達就大聲警告（事件會被丟棄並記錄）。
- **cron 安全網**（見 §3 表）：friday 30m 例行巡檢「不是輪詢」，是 webhook 失靈時的保底；
  各 worker 的 cron 同理。friday 可用 `schedule_set` 隨時調整。
- **User 通道（與 swarm 平行）**：`price_alert` / `position_pnl` / `report` 同步推 Telegram
  （`telegram.py`，未設定即 no-op）；reviewer 日誌與 friday 通報落在 dashboard 的
  Journal / Reports 分頁。

---

## 6. 一輪協作走過的例子（webhook 喚醒 → 團隊研究 → 決策 → 親自執行 → 復盤）

1. **開倉**：前一輪 friday 決定做多 SOLUSDT：5×、isolated、TP 180 / SL 140、理由、
   standing rule「+10% ROI → SL 上移到成本」；pre-flight 過（共識/限額/餘額）
   → `POST /api/perp/order`（memo 寫理由）→ 驗證成交與保護腿 → 持倉理由與 standing rule
   落盤憲法，並 `POST /api/alerts` 在 150 設了加碼觀察價。
2. **Sunday 盯市**：ws 串流看著 mark price；SOL 漲 5% ROI → monitor 跨 bucket →
   `position_pnl` webhook 喚醒 friday（預設；同時推 Telegram 給 User）。
3. **friday 醒來**：`GET /api/memory/friday` 回顧持倉理由 → `GET /api/account/positions`·`/pnl`
   對帳 → 判斷要不要移停損；不確定就 `send_message` analyst-flow「SOL 動能還在嗎」。
4. **analyst-flow**：`GET /api/klines/indicators?symbol=SOLUSDT&set=rsi,macd,adx` + `/api/funding`
   → 回 friday「動能仍多、但資金費轉熱，建議停損上移到成本」。
5. **friday 裁決並動手**：採納 → `POST /api/perp/protection`（SOL SL 上移到成本，引擎先掛
   新腿後撤舊腿）→ 驗 `protection` → 回 analyst-flow「採納，理由 X」→ `PUT /api/memory/friday`
   更新持倉理由 → 收工。
6. **risk-monitor（整點巡檢）**：對照共識——曝險未超標、每倉都有停損 → 一句 stand down。
7. **reviewer（00:00）**：`GET /api/account/trades`·`/pnl`（repl 算數字）歸因當日、決策與執行
   分開評 → `POST /api/journal`（User 在 dashboard 讀）→ 重點 + 1–3 條建議回 friday。
8. **User**：手機收到 Telegram 推播；dashboard 看倉位 memo、Journal、Reports——
   整條決策鏈（ticket / memo / memory / journal / reports）對 User 透明。

---

## 7. 狀態與證據（誰寫哪裡、誰讀哪裡）

| 儲存 | 寫 | 讀 | 用途 |
| --- | --- | --- | --- |
| `/api/memory/friday`（憲法公告板） | friday | friday（每次醒來 + 下單 pre-flight）、risk-monitor（對照共識）、analyst（watchlist）、User | 風控共識、watchlist、持倉理由、standing rules |
| `/api/memory/researcher`（研究日誌） | researcher | friday、User | 標日期的線索與 idea、已交付記錄 |
| `agents/{main,sub}/<name>/memory/`（evva 原生，RP-25） | 各成員（只能寫自己的） | 自己（醒來自動帶索引）+ 隊友可 `read` | 私人工作記憶：教訓、校準、對照副本、在途事項 |
| `/api/journal` | reviewer（每日） | User（dashboard） | 復盤日誌 |
| `/api/reports` | friday（事件驅動）、risk-monitor（升級） | User（dashboard + Telegram） | 大賺 / 大賠 / 系統錯誤快訊 |
| 訂單 `memo` | friday（隨單，寫決策理由） | User（倉位查詢回顯） | 每一筆單的「為什麼」 |
| `docs/PRD/` | 任何 agent | 開發者 | 對 Sunday 的功能需求開票 |

---

## 8. 目標與閘門

- **目標**：friday 帶隊在 testnet 達成**月報酬 ≥ 10%**——但更重要的衡量是 multi-agent
  completeness oracle：**一個 swarm 只靠通用 `http_request` + `GET /manual`，能不能把任意
  HTTP 外部系統用好**（派工 / 綜合 / 踩煞車 / 閉環 / 對 User 敘事）。
- **全程 testnet 假錢**；`permission_mode: bypass` 的前提就是這一條。
- pre-flight：`./scripts/run-tests.sh`（單元測試全綠）+ `./scripts/smoke.sh`（對 running Sunday
  驗 HTTP 契約）+ `./scripts/smoke-webhook.sh`（對 running evva 驗 webhook 收口）。
