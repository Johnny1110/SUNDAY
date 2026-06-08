# Sunday — 整體協作工作流（evva swarm → Sunday）

> 這份文件描述**整個系統怎麼協作**：User、監督 swarm（evva）、交易引擎（Sunday）、交易所、與 User dashboard。
> 對應 PRD：[`prd/sunday-project-prd.md`](prd/sunday-project-prd.md)（§3 架構、§4 lever、§5 喚醒、§7 Sunday）。
> 狀態：反映 **main 上的正典系統**（origin 引擎 + http_request agents + 5-agent roster + milestone-2.0 dashboard）。

---

## 0. 一句話

**Sunday（Python 引擎）自己交易;一個 5-agent 的 evva swarm 在上面監督;User 透過 evva web 指揮 leader、透過 Sunday 自服的 dashboard 看績效。** 兩個平面、四條邊界、零 Sunday-specific 的 evva code。

---

## 1. 全景圖

```
   ┌─────────────────────────── 控制平面：evva swarm（:8888, Go, .vero）────────────────────┐
   │                                                                                        │
   │   User ──(evva web / flat-comms)──►  friday (leader)                                    │
   │                                        │  ① 三 lever  ② 派工/諮詢                        │
   │                                        ▼                                                │
   │              analyst · risk-monitor · reporter · reviewer  ──send_message 建議──► friday │
   │                                        │                                                │
   └────────────────────────────────────────┼────────────────────────────────────────────────┘
                  ▲ ④ webhook（Sunday→leader）│ ③ http_request（GET 放行 / lever POST 審批）
                  │                           ▼
   ┌──────────────┴───────────────────────────────────────── 執行平面：Sunday（:7777, Python）─┐
   │   regime watcher ─► strategy 引擎 ─► 確定性風控 ─► 執行（下單+stop）─► postgres 帳本        │
   │        │                                                          │                       │
   │        └─ notify() webhook                                        └─► GET /dashboard ──► User│
   │                                       │ ccxt                                               │
   └───────────────────────────────────────┼───────────────────────────────────────────────────┘
                                            ▼
                                   Binance USDⓈ-M testnet（持倉最終真相）
```

**四條邊界**（其餘一律不准跨）：
1. **User ↔ swarm** — evva web（:8888）。User 對 friday 對話、可 flat-comm 任一成員。
2. **swarm → Sunday** — agents 用 **`http_request`** 工具打 Sunday HTTP API（GET 自動放行、lever POST 審批）。
3. **Sunday → swarm** — RP-9 webhook（`POST :8888/api/swarm/sunday/event`）投一封信進 leader 信箱。
4. **Sunday → User** — Sunday 自服 `GET :7777/dashboard`（D12：**不塞進 evva**，dashboard 由 Sunday serve）。

> **不變量**：agent 永不碰 Sunday 的 postgres、Sunday 永不碰 `.vero`、交易所是持倉最終真相、**evva 內零 Sunday-specific code**（agents 只用通用 http_request + skill + `/manual`）。

---

## 2. 角色盤點（swarm 的 5 個 agent）

| 角色 | 階層 | 喚醒來源 | 職權 | 不做 |
| --- | --- | --- | --- | --- |
| **friday** | leader | webhook（預設收件人）/ user 訊息 / 30m dead-man timer | **唯一拉 lever**（切策略 / halt / heartbeat）;監督、協調、對 User 負責 | 不下單、不做毫秒級風控 |
| **analyst** | 諮詢 | `regime_shift` / friday 指派 | 判 regime/方向 → 建議 friday;**`POST /commentary` 推市場動態給 User** | 不拉 lever |
| **risk-monitor** | 常駐值班 | 30m timer / `risk_breach` | 巡檢曝險/風控 → 違規告警 friday | 不拉 lever、不做硬停 |
| **reporter** | 常駐值班 | 1h timer | 狀態快照 → friday | 不判斷交易 |
| **reviewer** | 常駐值班 | 每日 17:00 timer | 讀 `/performance`·`/strategy_history` 復盤 → 策略建議 friday | 不拉 lever |

**唯一拉 lever 的是 friday**（對齊「只有 leader 寫 task 狀態」）。諮詽角色全部走 `send_message` 給 friday;**friday 採納/不採納都回信告訴他們**（閉迴路，否則諮詢角色無法改進）。

---

## 3. Sunday 的功能盤點

**引擎迴路（每 tick，watch loop）**：行情 ingest（ccxt）→ 當值策略算目標倉 → 確定性風控（單筆/曝險/槓桿/回撤硬擋）→ 執行（市價單 + 交易所原生 stop）→ 寫帳本（orders/fills/positions/pnl_snapshots）→ regime 偵測 → 值得注意時 `notify()` webhook。

**HTTP API（agent + dashboard 的唯一介面）**：

| 類 | 端點 | 用途 | 權限 |
| --- | --- | --- | --- |
| 讀 | `/status` `/market` `/positions` `/pnl` `/performance` `/strategy_history` `/commentary` | 狀態 / 行情 / 倉位 / 損益+權益曲線 / per-strategy 歸因 / 切換時間軸 / 市場動態 feed | GET 自動放行 |
| lever | `/strategy`（reason 必填，momentum/flat）·`/halt`（flat/safe） | **friday 專用** 切策略 / 叫停 | POST 審批 |
| liveness | `/heartbeat` | friday dead-man ping | POST |
| 寫 | `/commentary`（analyst） | 推市場動態給 User（無害、非交易 lever） | auto-allow |
| UI | `/dashboard` | Sunday 自服一頁:權益曲線 + 切換理由疊圖 + 30d PnL + 倉位 + per-strategy 歸因 + commentary feed | — |
| 文件 | `/manual` | 操作手冊（人 + agent 讀同一份） | — |

**Sunday → swarm 的 webhook 事件**：`regime_shift`、`risk_breach`、`engine_degraded`、`safe_mode_entered`（自給自足:帶 status 快照 + rationale + suggested_action）。

---

## 4. 喚醒模型（event-gated;timer 只當安全網）

**核心原則**：Sunday（Python）連續、便宜地盯市;由它決定「何時值得花一個 agent 的注意力」。市場劇烈 → 連發 webhook、agent 醒來才有意義;市場平靜 → 靜默 → agent 睡、**不燒 token**。

- **webhook**（主要）：Sunday 過閾值/去抖才發 → 投 leader 信箱 → idle 醒來 / busy 折入。
- **timer**（安全網，不做市場輪詢）：friday 30m dead-man、risk 30m audit、reporter 1h、reviewer 每日。idle 不燒 token。
- **雙向 dead-man**：friday 30m `POST /heartbeat`;Sunday 連 ~90m 沒收到 → 自行進 safe-mode（凍新倉）。

---

## 5. 一個走過的例子（regime_shift → 切策略 → User 看得到）

1. Sunday 偵測 BTCUSDT 盤性轉震盪、過閾值 → `notify("regime_shift", {status, rationale, suggested_action})`。
2. friday 被喚醒，讀事件自帶的 status + rationale。**指派 analyst** 評估。
3. analyst 用 `http_request` 查 `/status`·`/market`·`/performance` → 判方向 → `send_message` 回 friday「偏空、建議 flat、因為 ADX 跌破 20」;順手 `POST /commentary` 推給 User。
4. friday **下令前先 GET `/status`**（決策看現在）→ `POST /strategy {strategy:"flat", reason:"analyst 判轉震盪…"}` → permission 跳審批 → User/headless 放行。
5. friday **下令後再 GET `/status`** 驗證真的換了。
6. Sunday 下一 tick 依新策略平倉;`reason` 落 `strategy_state` → **User 在 `/dashboard` 看到切換理由疊在權益曲線上**。

每一條箭頭都有書面證據（webhook_log / `.vero` messages / strategy_state.reason / commentary）——這就是「監督迴路對 User 透明」。

---

## 6. 兩段閘門（北極星）

| | Gate-1（現在） | Gate-2（之後） |
| --- | --- | --- |
| 衡量 | **swarm 對不對**（監督/反應/叫停確定性正確） | **賺不賺**（真實長期 P&L） |
| 環境 | Binance testnet | 小額 mainnet |
| 成敗 | 與賺賠無關 | P&L 為正 |

**獲利永遠不是 Gate-1 的 gate。** 策略好壞（純 Python）與 swarm 正確性解耦;alpha 很可能不在單一策略,而在 **agent 的切換政策**——那是 Gate-2 該投資處。

---

## 7. 守住的紀律

- **evva 內零 Sunday-specific code** — agents 只用通用 `http_request` + per-role skill + Sunday `/manual`。
- **確定性風控在 Python/交易所層,永不在 LLM** — LLM 不在快路徑;lever 是「方向盤」,硬限額是「保險絲」。
- **只有 friday 拉 lever** — 諮詢角色只建議;權威集中、對齊「leader 寫帳本」。
- **testnet-first** — Gate-1 全程 testnet、零真錢。
