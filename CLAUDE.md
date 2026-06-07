# Sunday — Claude Code 開發指引

> 本檔每次 session 載入。**動工前先讀權威 PRD：[docs/prd/sunday-project-prd.md](docs/prd/sunday-project-prd.md)**（尤其 §0 決策紀錄 D1–D14、§4 lever 契約、§7 Sunday 規格、§9 驗證準則、§10 里程碑、§12 待決）。

## 我們在蓋什麼

一個 **Binance USDⓈ-M 永續交易系統**：**Sunday**（Python 交易引擎）出手交易，**evva agent swarm**（friday + 4 worker）在上面監督。真正目的是**驗證 evva swarm 的能力邊界**（Gate-1，testnet），獲利是 Gate-2 的獨立目標。Sunday 是 Veronica（evva 的 swarm 子系統）Phase 2 的具體化。

## 不可違反的不變量（load-bearing invariants）

開發時這些是硬規則，違反 = 設計錯誤：

1. **兩段閘門。** Gate-1 = 在 testnet 驗證 swarm，成敗 = swarm 機制正確、**與獲利無關**；Gate-2 = 追真錢獲利（獨立決策）。**獲利永遠不是 Gate-1 的 gate。** 別把「策略賺不賺」混進「swarm 對不對」。
2. **Sunday = 完整策略引擎（Python）；swarm 只監督。** Sunday 偵測訊號、下單/平倉、跑風險熔斷。agent 不下單。
3. **agent 的牙齒 = 三個 leader-only meta-lever**：切策略 / 設風險封套 / kill·重啟。**不做逐單核准**（會用 LLM 延遲卡死引擎）。諮詢角色（analyst/risk/reporter/reviewer）只建議，**只有 leader 拉 lever**。
4. **evva 內零 Sunday-specific code（最重要）。** agent 用**通用** `bash`+curl（或通用 `http_request`）+ per-role skill + Sunday 服務端 `/manual` 操作 Sunday。**永遠不要為 Sunday 在 evva 寫 custom Go tool。** 這正是本實驗的能力邊界主張：swarm 只靠通用工具 + 文件就能驅動任意 HTTP 外部系統。
5. **只有兩條 HTTP 邊界**：swarm→Sunday（bash/curl）、Sunday→swarm（RP-9 webhook `POST /api/swarm/sunday/event`）。**agent 永不直接讀 Sunday 的 postgres；Sunday 永不碰 evva 的 `.vero`；交易所是持倉最終真相。**
6. **喚醒 = event-gated；timer 只當安全網**（dead-man liveness + 週期性人類產出），**不做市場輪詢**。由 Sunday 決定何時喚醒 agent。
7. **確定性風險熔斷在 Python/交易所層，永不在 LLM。** 硬限額（單筆/曝險/槓桿/回撤）+ 交易所原生 stop。LLM 永不在快路徑上。
8. **swarm 掛掉 → Sunday 進 safe-mode**（凍新倉、守舊 stop）。**雙向 dead-man**：leader heartbeat Sunday；Sunday 收不到 heartbeat 就 safe-mode。
9. **Sunday = 系統 of record + legible。** 存執行結果（modeling-grade）+ leader 切策略的 `reason` + analyst 的 `commentary`。對 agent legible（`/status` 與事件帶 rationale）、對 User legible（決策理由 + 市場脈絡）。**捕捉從 Gate-1 開始；視覺化 dashboard 是 Gate-2，由 Sunday 自服（不塞進 evva）。**
10. **Gate-1 全程 testnet。** lever 走 permission 審批；Sunday command token 是 Gate-2 的硬化。

## 專案結構

```
sunday/
├── docs/prd/sunday-project-prd.md   # 權威 PRD（先讀）
├── evva-swarm.yml                   # swarm manifest（root = swarm workdir）
├── agents/
│   ├── main/friday/                 # leader：profile.yml + system_prompt.md + tools/active.yml + skills/operate-sunday/
│   └── sub/{analyst,risk-monitor,reporter,reviewer}/   # + skills/query-sunday/
├── engine/                          # Sunday Python 引擎
└── .vero/                           # evva swarm 自建（gitignored）
```

- swarm 成員的 `tools/active.yml` **要含 `bash`**（成員預設沒有）；analyst 另加 `web_fetch`/`web_search`。
- skill：leader = `operate-sunday`（讀 + 三 lever recipe + §7.10 下令紀律）；諮詢角色 = `query-sunday`（讀 + analyst 多一條 `/commentary` 無害寫入）。
- `evva-swarm.yml` 與 `agents/` 的格式以 `../evva` 現有 swarm（`docs/veronica/example-swarm/`、`vero-tech-swarm/`）為準。

## 技術棧

- **Sunday 引擎（`engine/`）**：Python。Binance USDⓈ-M testnet（ccxt 或 python-binance）；pandas/numpy（指標）；FastAPI/Flask（HTTP API + `/manual`）；redis（熱狀態）；PostgreSQL（帳本 + modeling-grade 資料）。
- **swarm**：evva（Go，**不在此 repo**）。我們**不寫 evva**，只**配置**它（`evva-swarm.yml` + `agents/`）。

## 與 evva 的關係（重要）

- evva 是 swarm runtime，**獨立 Go 專案在 `../evva`**（`/Users/johnny/lab/evva`）。
- 本專案是 evva swarm 的**使用者**：跑 `evva service start` + `evva swarm .`，靠 `:8888` API + RP-9 webhook 驅動。
- **不從這裡改 evva。** swarm 缺能力 → 回 `../evva` 開 refine-plan（RP），不在本 repo 改 `internal/swarm`。Sunday 只消費 evva 公開介面（這是 multi-agent completeness oracle 的重點）。
- 相關 evva 文件：`../evva/docs/veronica/`（Veronica 設計）、`refine-plan/RP-9`（外部事件 webhook，已實作）、`RP-7`（timer 喚醒）、`RP-10`（agent skill 注入）。

## 現況 / 節奏

- **現況**：scaffolding（README + CLAUDE + PRD 就緒）。
- **下一步 = S0**（PRD §10）：Sunday skeleton + 最小監督迴路。
- **里程碑**：S0 → S1 → S2（Gate-1）→ Gate-2。
- **待決**：PRD §12 還有 11 條 open decisions（風險數字、cadence、事件收件人、標的清單、engine 結構、lever 強度、dashboard 落點…），開工前視需要拍定。

## 慣例

- testnet API key / 密鑰放 `.env`，**永不 commit**（已 gitignore）。
- commit 訊息用 conventional prefix（`feat`/`fix`/`chore`/`docs`/`refactor`/`test`）。
- 寫任何 code 前，先確認沒違反上面 10 條不變量。
