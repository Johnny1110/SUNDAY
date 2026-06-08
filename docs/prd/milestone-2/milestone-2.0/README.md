# Milestone 2.0 — Sunday 自服 execution dashboard（overview + 任務索引）

> **狀態：✅ 完成（2026-06-07）** — T1–T4 全 commit；live testnet e2e 跑通：commentary → 切策略（附 reason，開 momentum long 0.008）→ 開倉時段權益快照 → `/positions` 帶 strategy/entry_reason/stop → halt flat（realized -0.0248 捕捉）→ `/performance` 歸因。驗收 B1–B7 全達。dashboard 在 `http://127.0.0.1:7777/dashboard`。
>
> 上層：[`../../sunday-project-prd.md`](../../sunday-project-prd.md)（**D14** User-facing 系統 of record、§7.7 schema、§7.11、§10 Gate-2、§12.10/§12.11）｜ milestone-2 index：[`../README.md`](../README.md)
> 繼承上層全部不變量（[`../../../../CLAUDE.md`](../../../../CLAUDE.md) 10 條）。**全程 testnet、純讀 + 一個無害寫入、零真錢。**

## 1. 目標（DoD）

在 **Binance USDⓈ-M testnet** 上，**Sunday 自服**一頁 web dashboard（`GET http://127.0.0.1:7777/dashboard`），讓 User 在**一個地方**看到 D14 要的全部：

> **權益（資產）折線圖 + 30 日 PnL + 當前倉位 + per-strategy 績效歸因 + 策略切換理由疊圖 + analyst 市場動態 commentary feed。**

證明「**監督迴路對 User 透明**」：績效曲線（Sunday 寫）＋ 切換理由（leader 寫，§7.11）＋ 市場脈絡（analyst 寫，§7.11）三者 co-located，User 一眼判斷「策略 work 不 work」。**這是 §9 V2 的書面證據面**。

> **守 D12 的關鍵**：dashboard 完全由 **Sunday（:7777）自服**——evva 內**零新增** Sunday-specific code。資料捕捉本就該從 Gate-1 開始（補不回過去的市場脈絡），本里程碑把 1.0 留下的捕捉缺口補齊，再加視覺化。

## 2. 範圍

**In**：① 補資料捕捉（`pnl_snapshots` 寫入、closed position 的 `realized_pnl`、`commentary` 表/端點、`/positions` 補 `strategy`/`entry_reason`/`stop`）；② dashboard 讀 API（擴充 `/pnl` 真 equity_curve、`/performance` 歸因、`/strategy_history` 疊圖、`/commentary` feed）；③ Sunday 自服 `/dashboard` 單頁 UI；④ analyst `POST /commentary` 的 skill recipe + e2e。

**Out（→2.1/2.2/2.3）**：telegram 對外播報、analyst 外部訊號源（fear&greed / on-chain / 新聞）、回測引擎、ML 建模、多策略、真錢 / mainnet、webhook + command token 硬化、dashboard auth（2.0 維持 **loopback-only**，與 `:7777` 既有讀端點同信任域）。

## 3. 共用契約：Sunday HTTP API（2.0 新增 / 修改；`/manual` 要文件化）

base `http://127.0.0.1:7777`。GET = 唯讀 = allow-rule 自動放行；`POST /commentary` = **無害寫入、非交易 lever** → 也 auto-allow（§7.11，analyst 用）。**交易 lever（/strategy /halt …）不在本里程碑、維持 ask。**

| 方法 | 端點 | 入 | 出（重點） | 任務 |
| --- | --- | --- | --- | --- |
| GET | `/dashboard` | — | 自含 HTML 頁面 | T3 |
| GET | `/pnl` | `?since` | **（擴充）** `{realized, unrealized, equity, equity_curve:[[ts_ms,equity]...], window_days}`——取代 1.0 的 `equity_curve:[]` stub、`realized:null` | T2 |
| GET | `/performance` | `?since` | per-strategy 歸因 `[{strategy, realized_pnl, n_trades, win_rate, avg_pnl, open_qty}]` | T2 |
| GET | `/strategy_history` | `?since` | 切換時間軸 `[{set_at_ms, symbol, strategy, reason, set_by}]`（疊圖用） | T2 |
| GET | `/commentary` | `?since&limit` | analyst 貼文 feed `[{ts, author, title, body}]`（時間倒序） | T1 |
| POST | `/commentary` | `{author,title,body}` | `{ok,id}`——analyst 推市場動態給 User（**無害寫入、auto-allow**） | T1 |
| GET | `/positions` | — | **（修）** 補真的 `strategy`/`entry_reason`/`stop`（join DB `positions` 表；1.0 是 `None`） | T1 |

## 4. 共用契約：postgres（2.0 = 開始**寫入**既有表 + 一張新表）

- **`pnl_snapshots`**（0001 已建，**目前無人寫**）→ 本里程碑由 watch loop 每 tick 寫一筆 `{ts, equity, realized, unrealized, drawdown_pct}`——**權益曲線的唯一資料源**。
- **`positions.realized_pnl`**（0001 欄位已在）→ 平倉時寫回——**per-strategy 歸因的唯一資料源**。
- **`commentary`**（新，`0002_dashboard.sql`）：`{id BIGSERIAL pk, ts TIMESTAMPTZ default now(), author TEXT, title TEXT, body TEXT}`——analyst 市場動態貼文（User 可見）。
- **`strategy_state.reason`**（0001 已建、1.0 已在寫）→ 疊圖直接讀，無需改 schema。

## 5. 檔案樹（2.0 完成後）

```
sunday/
├── engine/
│   ├── migrations/0002_dashboard.sql                    (T1：commentary 表)
│   └── sunday/
│       ├── store.py      (+commentary DAO / pnl_snapshot 寫入 / equity_curve·performance 查詢 / positions join)  (T1,T2)
│       ├── strategy.py   (+watch tick 寫 pnl_snapshot；+平倉寫 realized_pnl)                                     (T1)
│       ├── app.py        (+/commentary GET·POST、/performance、/strategy_history；擴充 /pnl；修 /positions；+/dashboard) (T1,T2,T3)
│       ├── dashboard.html(自含頁面：vanilla JS + CDN chart lib)                                                  (T3)
│       └── manual.md     (+新端點文件)                                                                            (T2,T4)
└── agents/sub/analyst/skills/query-sunday/SKILL.md       (+POST /commentary recipe)                              (T4)
```

## 6. 任務索引（一個 session 一個 T）

| T | 檔 | 做什麼 | 依賴 |
| --- | --- | --- | --- |
| **T1** | [T1-data-capture.md](T1-data-capture.md) | 補資料捕捉：寫 `pnl_snapshots` + 平倉寫 `realized_pnl` + `commentary` 表/端點 + `/positions` 補欄 | engine 1.0（已就緒） |
| **T2** | [T2-dashboard-api.md](T2-dashboard-api.md) | dashboard 讀 API：擴充 `/pnl`（真 equity_curve）+ `/performance`（歸因）+ `/strategy_history`（疊圖） | T1 |
| **T3** | [T3-dashboard-ui.md](T3-dashboard-ui.md) | Sunday 服 `/dashboard` 單頁 web UI（曲線+疊圖 / 30 日 PnL / 倉位 / 歸因 / commentary）；自含、CDN chart、**零 build** | T2 |
| **T4** | [T4-wiring-e2e.md](T4-wiring-e2e.md) | analyst `/commentary` skill recipe + friday「reason 給人看」note + e2e demo + 驗收 B1–B7 | T1–T3 |

## 7. milestone 級驗收（B1–B7，於 T4 驗；對應 D14）

- **B1 權益曲線**：dashboard 顯示 testnet 權益隨時間的折線（資料來自 `pnl_snapshots`，run 期間累積）。
- **B2 30 日 PnL**：顯示近 30 日 realized + unrealized + equity（窗可 `?since` 調）。
- **B3 倉位**：當前倉位表含 side/qty/entry/mark/upnl **+ strategy / entry_reason / stop**。
- **B4 per-strategy 歸因**：每策略 realized PnL / 筆數 / 勝率，看得出「哪個策略在賺/賠」。
- **B5 切換理由疊圖**：策略切換點標在權益曲線上 + 列出 leader 當時的 `reason`（決策留痕對 User 可見）。
- **B6 commentary feed**：analyst `POST /commentary` 的市場動態，在 dashboard feed 顯示。
- **B7 D12 + testnet**：dashboard 全由 **Sunday（:7777）自服**；**evva 內零新增 Sunday-specific code**；全程 testnet、無真錢。

## 8. 2.0 決策（預設，可改）

1. **dashboard 落點 = Sunday 自服 `GET /dashboard`**（守 D12，§12.10 已拍）。
2. **前端 = 單一自含 HTML + CDN chart lib**（uPlot 或 Chart.js）、**零 build step / 零 node / 零框架**（對齊 evva「minimize deps」精神；引擎不引入前端工具鏈）。
3. **權益曲線資料 = `pnl_snapshots`**，watch loop 每 tick 寫一筆（60s/點，夠密，testnet 無妨）。
4. **auth = 無**（loopback-only，與 `:7777` 既有讀端點同信任域）；真正的 token 硬化是 **2.3**。
5. **30 日窗預設**，`?since` 可覆寫。
6. **commentary 作者**：2.0 只有 analyst 寫（§7.11）；欄位留 `author` 以便 2.1+ 擴充（如 reviewer 日結貼文）。
