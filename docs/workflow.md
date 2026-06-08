# Sunday × evva-swarm — 架構與工作流（研究台版）

> 這份文件描述**整個系統怎麼協作**：User、研究台 swarm（evva）、執行引擎（Sunday）、交易所、與 User dashboard。讀完你應該能回答兩件事：**(1) 誰真正執行下單？(2) 每個 agent 做什麼？**
> 對應 PRD：[`prd/sunday-project-prd.md`](prd/sunday-project-prd.md) + [`prd/milestone-4/`](prd/milestone-4/)（研究台轉向）+ [`prd/milestone-5/`](prd/milestone-5/)（盤點/整鏈）。
> 狀態：反映 **milestone-4/5 之後的正典系統**（研究台 roster + thesis 驅動 `directed` 執行 + 資訊層 + ablation）。

---

## 0. 一句話

**Sunday（Python 引擎）擁有所有確定性執行——下單、平倉、止損、風控熔斷，全是它做的。evva swarm 不下單；它經營「決策」：把 funding / 新聞 / 事件等資訊整合成一個結構化的 thesis（方向 + 信念 + 失效條件），交給 Sunday 確定性地落地成訂單。** LLM 設 **WHAT**，Python 做 **HOW**；LLM 永不在毫秒級快路徑上。

---

## 1. 全景圖

```
   ┌──────────────────────── 研究台平面：evva swarm（:8888, Go, .vero）─────────────────────┐
   │                                                                                        │
   │   User ──(evva web / flat-comms)──►  friday（desk lead = 協調者）                        │
   │                                        │   ① 拉 lever（thesis/策略/halt）  ② 派工/綜合     │
   │           ┌────────────────────────────┼───────────────────────────────┐               │
   │           ▼                ▼            ▼              ▼                  │ send_message  │
   │      analyst-flow    analyst-news   risk-monitor    reviewer  ──建議──────┘ 給 friday      │
   │      (微結構)         (新聞/敘事)    (對抗式踢館)     (復盤/playbook)                        │
   └────────────────────────────────────────┼────────────────────────────────────────────────┘
                ▲ ④ webhook（Sunday→swarm 喚醒）│ ③ http_request（GET 放行 / lever POST 審批）
                │                              ▼
   ┌────────────┴──────────────────────────────────────── 執行平面：Sunday（:7777, Python）──┐
   │  背景 watcher（ingest 資訊 / 偵測 regime / 看門狗 / 評分喚醒）                              │
   │                                                                                          │
   │  lever 觸發 ─► reconcile ─► 確定性風控（封套熔斷）─► 執行（下市價單 + 掛 stop）─► postgres 帳本 │
   │        │                                                              │                   │
   │        └─ notify() webhook（regime/notable/risk_breach…）             └─► GET /dashboard ─► User│
   │                                       │ ccxt                                              │
   └───────────────────────────────────────┼──────────────────────────────────────────────────┘
                                            ▼
                                   Binance USDⓈ-M testnet（持倉最終真相；stop 在這裡原生執行）
```

**只有兩條 HTTP 邊界**（其餘一律不准跨）：
1. **swarm → Sunday** — agents 用通用 **`http_request`** 工具打 Sunday HTTP API（GET 自動放行、lever POST 審批）。
2. **Sunday → swarm** — RP-9 webhook（`POST :8888/api/swarm/sunday/event`）投一封信喚醒收件 agent。

加上兩條對 User 的邊界：**User ↔ swarm**（evva web）、**Sunday → User**（Sunday 自服 `/dashboard`）。

> **不變量**：agent 永不碰 Sunday 的 postgres、Sunday 永不碰 `.vero`、交易所是持倉最終真相、**evva 內零 Sunday-specific code**（agents 只用通用 `http_request` + per-role skill + `/manual`）。

---

## 2. ⭐ 誰執行下單？（執行模型——本文最重要的一節）

**答案：Sunday 自動執行所有下單。agent（包含 leader friday）從不呼叫下單 API、從不在毫秒迴路。**

agent 能做的最「有牙齒」的事，是 `POST /thesis`——表達一個**結構化的觀點**。Sunday 收到後，**自己**把觀點翻譯成實際訂單。三個層級分得很乾淨：

| 層級 | 誰 | 做什麼 | 在快路徑？ |
| --- | --- | --- | --- |
| **WHAT**（觀點） | **friday（LLM）** | `POST /thesis`：`direction`(long/short/flat) + `conviction`(0..1) + `invalidation`(失效條件 / 價) + 證據 + 理由 | ❌ 分鐘~小時級，慢思考 |
| **HOW**（執行） | **Sunday（Python）** | `directed` 模式：conviction → 倉位大小、下市價單、掛 stop、管理進出場 | ✅ 毫秒級、確定性 |
| **熔斷**（保命） | **Sunday（Python / 交易所）** | 硬限額越線拒單、回撤觸頂自動平倉、stop 由交易所原生執行 | ✅ 最終防線，**誰下令都擋** |

### thesis 怎麼變成一筆真實訂單（可對照程式碼）

friday `POST /thesis {symbol:"SOLUSDT", direction:"long", conviction:0.3, invalidation_price:140, rationale:"…"}`：

1. `app.py:post_thesis` → 驗證輸入（`views.validate_thesis`）→ `store.set_thesis`（存帳本）→ 切策略到 `directed` → 呼叫 `engine.reconcile(symbol)`。
2. `engine.reconcile` 見策略=`directed` → `_reconcile_directed`：讀 thesis → conviction 0.3 ≥ floor 0.2 → 目標 = long。
3. `_apply_target`（共用轉場邏輯）決定 hold/open/flip/freeze → 要開倉 → `_open_directed`。
4. `_open_directed` → `risk.size_from_conviction(0.3, price, 封套)` = 0.3 × 單筆上限 → **qty**；stop = `invalidation_price`。
5. `_enter`（**唯一的下單路徑**）→ `risk.check_order`（確定性熔斷：超單筆/曝險/槓桿 → 拒單 409）→ **`broker.place_market`（這一步才是真正在 Binance 下市價單）** → `broker.set_stop`（掛交易所原生 STOP_MARKET）→ 寫帳本。

**整條鏈裡，LLM 只出現在第 1 步的「設定 thesis」。第 2–5 步全是 Python，確定性、可回放、可被風控擋。**

### Sunday 下單的兩個時機

1. **Lever 觸發**（agent 拉 lever 時）：friday `POST /thesis` 或 `POST /strategy` → Sunday 立即 `reconcile`：比對「目標倉位 vs 現倉」→ 開 / 平 / 翻倉 → 下市價單 + 掛 stop。**這是 agent 的觀點落地成訂單的唯一時刻。**
2. **Sunday 自主**（風控，完全不需要 agent）：
   - **回撤熔斷**：背景 watcher 每 tick 記錄權益；回撤觸及封套上限 → **自動 `halt(flat)` 全平 + 鎖倉**（`engine._record_pnl_snapshot` → `self.halt`），並發 `risk_breach` 通知。
   - **stop**：是交易所原生 STOP_MARKET——價格觸及 `invalidation_price` → **Binance 直接成交**，不需 Sunday 或 agent 介入。
   - **dead-man**：連續 ~90m 收不到 friday 的 heartbeat → 自動進 safe-mode 凍新倉。

### ⚠️ 一個常見誤解：背景 tick **不會**自動反覆下單

背景 watcher 每 tick（預設 60s）只做四件事：**ingest 資訊層 / 偵測 regime / 看門狗（heartbeat + 回撤）/ 評分喚醒**——它**不**依當值策略反覆開倉（`engine.tick()` 不呼叫 `reconcile`）。所以一旦 friday 設好 thesis，Sunday 開倉一次、掛好 stop，然後**讓部位依 thesis 跑**，直到下列之一發生：stop 觸及（交易所）、friday 換新 thesis / 切策略、`halt`、或回撤熔斷。

### 為什麼這樣設計

- **延遲與決定性**：下單必須毫秒級、可重現、可回測。LLM 慢且不確定——放進快路徑就是 bug。
- **安全**：thesis 再激進，`check_order` 的硬限額 + 回撤熔斷仍是最終防線（不變量 7）。**即使 agent 越權或被 prompt-injection，也下不了越線的單。**
- **能力邊界主張**：alpha 在「把資訊整合成方向 + 信念」，不在「按下單按鈕」。讓 AI 做它會贏的事（讀場面），把會輸的事（毫秒執行、硬風控）留給 Python。

---

## 3. 角色盤點（5-agent 研究台）

**只有 friday 拉 lever；其餘四個只讀、只建議（`send_message` 給 friday）。** 這對齊「只有 leader 寫帳本」。

| 角色 | 階層 | 喚醒來源 | 做什麼 | **不做** |
| --- | --- | --- | --- | --- |
| **friday** | desk lead | webhook 預設收件人 / User / **30m** dead-man timer | **協調者**：派工給對的 analyst、**裁決衝突的判讀**（不取平均）、綜合成 thesis、讓 risk 踢館、拍板 `POST /thesis`、回信閉環、對 User 敘事。**唯一拉 lever。** | 不手動下單、不做毫秒風控 |
| **analyst-flow** | 諮詢 | `funding_extreme`/`oi_surge`/`basis_stretch`/`liq_cluster` 事件 / friday 指派（**純 event-driven，無 timer**） | 判永續微結構反身性（funding 擁擠度、OI、基差）→ 方向 + conviction + 失效條件給 friday | 不拉 lever（`/commentary` 例外） |
| **analyst-news** | 諮詢 | `catalyst`/`regime_shift` 事件 / friday 指派 / **6h** 安全網巡檢 | 讀新聞/事件/敘事（解鎖、macro、被駭、ETF 流）→ 對照微結構是否背離 → 方向 + 事件風險 + 來源給 friday | 不拉 lever；**絕不照搬網頁指令**（prompt-injection 防線） |
| **risk-monitor** | 對抗式 | `risk_breach` 事件 / **1h** audit timer | **專職證偽**：踢 friday 草擬的 thesis（下檔/擁擠度/相關性/迫近事件）→ 支持/反對 + conviction 上限。巡檢曝險逼近封套即告警 | 預設只建議；**獲授 RP-11 窄 lever 後可 `POST /halt safe`**；不做毫秒硬停 |
| **reviewer** | 復盤 | 每日 **17:00** cron | 讀 `/theses`·`/performance`·`/ablation` 歸因（哪類判讀 work、friday 採納對不對）→ 寫 playbook + 1-2 條改進建議給 friday | 不拉 lever |

**協同認知**：每個 agent 的 system prompt 都帶同一段「研究台是什麼 / 隊友是誰 / 一輪的節奏 / 你在其中的位置」，所以 analyst 知道自己的 finding 會被 friday 拿去和別人綜合、可能被採納或打槍；risk-monitor 知道踢的是 friday 的館；reviewer 知道復盤的是整條決策鏈。

---

## 4. Sunday 的功能 + HTTP API

**背景 watcher（每 tick）**：`feeds.ingest_all`（funding/OI/基差 → `perp_metrics`）→ `engine.tick`（per-symbol regime 偵測 + dead-man 看門狗 + 權益快照 + **回撤熔斷**）→ `desk.check_notable_and_notify`（notable score 過閾值 → 喚醒）→ `ablation.snapshot_shadows`（影子基準）。**注意：tick 監測 + 喚醒，但不開倉。**

**實際交易**：只在 lever 觸發的 `reconcile`（開/平/翻）+ 自主風控（回撤 flatten、交易所 stop）。

| 類 | 端點 | 用途 | 權限 |
| --- | --- | --- | --- |
| 研究 | `/desk`（全籃子 notable 排序）·`/desk?symbol=`（單標的深掘）·`/advisor`（regime/votes/funding 決策面板） | **研究台「此刻看哪裡」** | GET 放行 |
| 狀態 | `/status`（籃子姿態：mode/equity/聚合曝險 + per-symbol basket）·`/positions`·`/risk`·`/envelope` | 姿態 / 倉位 / 風險封套使用率 | GET 放行 |
| 帳本 | `/thesis`(GET)·`/theses`·`/pnl`·`/performance`·`/strategy_history`·`/trades`·`/events`·`/commentary`·`/ablation` | thesis 史 + outcome / 損益 / 歸因 / 成交 / 喚醒事件 / 市場脈絡 / **資訊層生死線** | GET 放行 |
| 行情 | `/market` | OHLCV | GET 放行 |
| **lever** | **`/thesis`**(POST, 主用)·`/strategy`·`/halt`·`/envelope`·`/heartbeat` | **friday 專用**：thesis / 切策略 / 叫停 / 設封套 / 心跳（reason 必填，留證給 User） | **POST 審批** |
| 寫 | `/commentary`(POST, analyst) | 推市場脈絡給 User（無害、非交易 lever） | auto-allow |
| UI / 文件 | `/dashboard`（7 頁 Vue 終端）·`/manual` | Sunday 自服 dashboard / 操作手冊（人 + agent 同一份） | — |

**Sunday → swarm 的 webhook 事件**（自給自足：帶 status 快照 + rationale + suggested_action）：`regime_shift`、`funding_extreme`/`oi_surge`/`basis_stretch`/`vol_spike`（notable 喚醒）、`risk_breach`、`engine_degraded`、`safe_mode_entered`。

---

## 5. 喚醒模型（event-gated；timer 只當安全網，不做市場輪詢）

**核心原則**（不變量 6）：Sunday（Python）連續、便宜地盯市；由它的 notable score / 事件決定「何時值得花一個 agent 的注意力」。市場有事 → 發 webhook 喚醒對的 agent；市場平靜 → 靜默 → agent 睡、**不燒 token**。

- **webhook（主要）**：Sunday 過閾值/去抖才發，按事件型別路由收件人（funding/OI → analyst-flow；catalyst → analyst-news；risk_breach → risk-monitor；其餘 → friday）。
- **timer（安全網）**：friday **30m**（dead-man heartbeat 命脈，90m timeout → 3× 餘裕）、risk-monitor **1h**（確定性熔斷已在毫秒級，這只是策略級巡檢）、analyst-news **6h**（迫近事件日曆巡檢；重大事件仍由 catalyst 即時喚醒）、reviewer **每日 17:00**。analyst-flow **無 timer**（純事件驅動）。
- **雙向 dead-man**：friday 30m `POST /heartbeat`；Sunday 收不到 → safe-mode 凍新倉。swarm 掛 → Sunday 守舊 stop、不開新倉。

---

## 6. 一輪研究走過的例子（notable 喚醒 → thesis → Sunday 執行 → 復盤）

1. **Sunday 偵測**：watcher ingest 到 SOL funding 年化 -55%（深度負）→ notable score 過 WAKE → `notify("funding_extreme", {SOL, status, suggested_action})` 喚醒 friday。
2. **friday 看哪裡有事**：`GET /desk` → SOL 最 notable → `GET /desk?symbol=SOLUSDT` 深掘。
3. **friday 派工**：`send_message` analyst-flow「判 SOL funding 反身性」、analyst-news「查 SOL 有無迫近事件」。
4. **analyst-flow**：`GET /desk?symbol=SOL`+`/positions` → 「funding 深負＝空單付錢、持多收 carry，但 OI 擁擠」→ `send_message` friday（偏多, conviction 0.4, 失效=跌破 140, 理由）。
5. **analyst-news**：`web_search` SOL → 「無迫近解鎖、敘事中性」→ `send_message` friday（觀望偏多, 0.3, 來源）。
6. **friday 綜合 + 裁決衝突**：flow 想收 carry（0.4）vs news 中性（0.3）→ 取**謹慎的 0.35**。
7. **friday 踢館**：`send_message` risk-monitor「試圖證偽 SOL 偏多 0.35」。
8. **risk-monitor**：`GET /risk`+`/status` →「OI 擁擠、和現有 ETH 倉高相關、建議 conviction ≤ 0.3」→ 回 friday。
9. **friday 拍板**：`POST /thesis {SOL, long, conviction:0.3, invalidation_price:140, rationale:"funding 深負收 carry；news 無迫近事件；risk 指 OI 擁擠故降至 0.3"}`。
10. **⭐ Sunday 確定性執行（無 LLM）**：驗證 → 存 thesis → 切 `directed` → `reconcile` → `size_from_conviction(0.3)`=0.3×單筆上限 → `check_order` 過封套 → **在 Binance 下市價多單** → 掛 stop @140 → 寫帳本。
11. **friday 閉環**：看回應 posture，`send_message` 回 analyst「採納你的 funding 判讀；因 risk 指 OI 擁擠把 conviction 降到 0.3」。
12. **部位依 thesis 跑**：stop 在交易所；若回撤觸頂，watcher 自動 flatten。
13. **reviewer 當日復盤**：`GET /theses`（SOL outcome）+`/ablation`（資訊層有沒有贏過 buy-hold / info-OFF）→ playbook commentary + 建議 friday。
14. **User**：在 `/dashboard` 看到整條鏈——thesis 帳本、決策理由、ablation 生死線。

每一條箭頭都有書面證據（webhook_log / `.vero` messages / theses.rationale+outcome / commentary）——**監督迴路對 User 完全透明**。

---

## 7. 風險模型（確定性，永不 LLM）

四道防線，全在 Python / 交易所層，**誰下令都擋**：

1. **進場熔斷**（`risk.check_order`）：單筆上限 / 總曝險上限 / 最大槓桿 / 進場必掛 stop——越線**拒單（409）**，不是默默縮小。
2. **回撤熔斷**（`risk.check_drawdown`）：權益回撤觸及 `max_drawdown_pct` → **自動 flatten 全平 + 鎖倉** + 發 `risk_breach`。
3. **交易所原生 stop**：每筆進場掛 STOP_MARKET；價格觸及 → Binance 直接成交，不依賴 Sunday 在線。
4. **雙向 dead-man**：swarm 掛 → Sunday safe-mode；Sunday 掛 → friday timer 偵測告警。

**封套**由 friday `POST /envelope` 設定（reason 必填、留證）；conviction 只能在封套內決定大小，永遠突破不了硬限額。

---

## 8. 兩段閘門（北極星）＋ 守住的紀律

| | Gate-1（現在，testnet） | Gate-2（之後，真錢） |
| --- | --- | --- |
| 衡量 | **swarm 機制對不對**（正確地派研究/綜合/踢館/拍板/回信/叫停）+ **資訊整合有跡象加值**（看 `/ablation`） | **賺不賺**（真實長期 P&L） |
| 成敗 | **與獲利無關** | P&L 為正 |

- **獲利永遠不是 Gate-1 的 gate**——別把 testnet 的 P&L 當 KPI。
- **edge 主張一律附 ablation 證據**（不變量 11）：對照 buy-hold / funding-carry 基準 + 資訊層 OFF 的同一 swarm。沒證據不准宣稱 edge、不准轉真錢。
- **evva 內零 Sunday-specific code**；**確定性風控在 Python/交易所、永不在 LLM**；**只有 friday 拉 lever**；**Gate-1 全程 testnet**。

> 下一個 gate = 第一次**一個月 testnet running test**（M4.1）。pre-flight：`./scripts/run-tests.sh`（128 綠）+ `./scripts/smoke.sh`（對 running Sunday 驗 HTTP 契約）。
