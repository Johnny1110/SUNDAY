# Sunday — Claude Code 開發指引

> 本檔每次 session 載入。**動工前先讀權威 PRD：[docs/prd/sunday-project-prd.md](docs/prd/sunday-project-prd.md)**（§0 決策 D1–D14、§4 lever 契約、§7 Sunday 規格、§9 驗證準則、§10 里程碑、§12 待決）。
> **milestone-4 起轉向（2026-06-08）：產品方向 = AI 事件驅動永續台。** 先讀 [docs/prd/milestone-4/README.md](docs/prd/milestone-4/README.md) + [product-plan.md](docs/prd/milestone-4/product-plan.md)。下一個 gate = 第一次**一個月 testnet running test**。

## 我們在蓋什麼

一個 **Binance USDⓈ-M 永續交易系統**：**Sunday**（Python 引擎）負責**確定性執行 + 風控 + 資訊 ingest**，**evva agent swarm**（friday + workers）在上面**經營交易決策**。真正目的是**驗證 evva swarm 的能力邊界**（Gate-1，testnet），獲利是 Gate-2 的獨立目標。Sunday 是 Veronica（evva 的 swarm 子系統）Phase 2 的具體化。

**milestone-4 起的產品方向：AI 事件驅動永續台。** 不讓 AI 做它會輸的事（預測 K 線、拚統計 alpha），讓它做唯一能贏的事——**7×24、跨多標的、把 funding / 鏈上 / 新聞 / 事件等非結構化資訊整合成「方向 + 信念 + 風險姿態」**。一個沒有人類能輪班、沒有 quant 能輕易系統化的夾縫。**Sunday = 執行/風險/資訊基板；swarm = 研究台（research desk），alpha 在資訊整合、不在策略。** 詳見 [docs/prd/milestone-4/](docs/prd/milestone-4/)。

## 不可違反的不變量（load-bearing invariants）

開發時這些是硬規則，違反 = 設計錯誤：

1. **兩段閘門。** Gate-1 = 在 testnet 驗證 swarm，成敗 = swarm 機制正確、**與獲利無關**；Gate-2 = 追真錢獲利（獨立決策）。**獲利永遠不是 Gate-1 的 gate。** 別把「策略賺不賺」混進「swarm 對不對」。
2. **Sunday = 執行/風險/資訊基板（Python）；swarm 驅動 positioning。** Sunday 擁有**所有確定性執行 + 下單/平倉 + 風險熔斷 + 資訊 ingest**；agent 不下單、不在快路徑。〔milestone-4〕swarm 從「只監督」升為「經營決策」：透過結構化 **thesis** 表達方向/信念，由 Sunday 的 `directed` 模式**確定性**執行（LLM 設 WHAT、Python 做 HOW）。
3. **agent 的牙齒 = leader-only meta-lever**：切策略 / 設風險封套 / kill·重啟 ＋〔milestone-4〕**thesis**（`POST /thesis`：結構化方向/信念/失效條件，驅動 `directed` 執行）。**不做逐單核准**（LLM 不在毫秒迴路）。諮詢角色只建議，**只有 leader 拉 lever**。
4. **evva 內零 Sunday-specific code（最重要）。** agent 用**通用** `http_request` 工具（RP-A 已 ship；亦可 bash+curl）+ per-role skill + Sunday 服務端 `/manual` 操作 Sunday。**永遠不要為 Sunday 在 evva 寫 custom Go tool。** 這正是本實驗的能力邊界主張：swarm 只靠通用工具 + 文件就能驅動任意 HTTP 外部系統。
5. **只有兩條 HTTP 邊界**：swarm→Sunday（`http_request`/curl）、Sunday→swarm（RP-9 webhook `POST /api/swarm/sunday/event`）。**agent 永不直接讀 Sunday 的 postgres；Sunday 永不碰 evva 的 `.vero`；交易所是持倉最終真相。**
6. **喚醒 = event-gated；timer 只當安全網**（dead-man liveness + 週期性人類產出），**不做市場輪詢**。由 Sunday 決定何時喚醒 agent〔milestone-4：由 `/desk` 的 notable score 把關〕。
7. **確定性風險熔斷在 Python/交易所層，永不在 LLM。** 硬限額（單筆/曝險/槓桿/回撤）+ 交易所原生 stop。LLM 永不在快路徑上。**thesis 再激進，封套與熔斷仍是最終防線（誰下令都擋）。**
8. **swarm 掛掉 → Sunday 進 safe-mode**（凍新倉、守舊 stop）。**雙向 dead-man**：leader heartbeat Sunday；Sunday 收不到 heartbeat 就 safe-mode。
9. **Sunday = 系統 of record + legible。** 存執行結果（modeling-grade）+ leader 的 `reason` + analyst 的 `commentary`〔milestone-4：**+ 資訊層（funding/OI/清算/basis/事件/新聞）+ thesis/outcome 帳本**〕。對 agent legible（`/status`·`/advisor`·`/desk`·事件帶 rationale）、對 User legible（決策理由 + 市場脈絡）。dashboard 由 **Sunday 自服**（不塞進 evva）。
10. **Gate-1 全程 testnet**（含 milestone-4 的一個月 running test）。lever 走 permission 審批；Sunday command token 是 Gate-2 的硬化。
11. **〔milestone-4〕ablation 是生死線。** 任何「資訊層 / agent 綜合有加值」的宣稱，都必須對照基準（buy-hold / funding-carry / 確定性 baseline）**＋ 資訊層 OFF 的同一 swarm**。沒 ablation 證據，不准宣稱 edge、不准轉真錢。這是降低「LLM 劇場」風險的硬紀律。

## 專案結構

```
sunday/
├── docs/prd/sunday-project-prd.md   # 權威 PRD（先讀）
├── docs/prd/milestone-4/            # 〔現行〕研究台轉向：README + product-plan + milestone-4.0/
├── evva-swarm.yml                   # swarm manifest（root = swarm workdir）
├── agents/                          # friday(leader) + sub workers（milestone-4/T5 演進為研究台）
├── engine/                          # Sunday Python 引擎（+ engine/sunday/web/ = Vue dashboard）
└── .vero/                           # evva swarm 自建（gitignored）
```

- swarm 成員用 **`http_request`** 工具操作 Sunday（RP-A 已 ship，不再需 bash+curl）；analyst 另加 `web_fetch`/`web_search`。
- skill：leader = `operate-sunday`（讀 + lever recipe + §7.10 下令紀律）；諮詢角色 = `query-sunday`。**〔milestone-4/T5〕** 演進為研究台：`operate-desk`（thesis recipe + research-round 紀律）+ 各專責 analyst 的 `research-*` skill。
- **〔milestone-4/T5〕roster 演進**：friday→desk lead；analyst 拆 `analyst-flow`/`analyst-news`(/`analyst-onchain`)；risk-monitor→對抗式踢館（+ 窄 halt lever，RP-11）；reviewer→post-mortem + playbook。
- `evva-swarm.yml` 與 `agents/` 格式以 `../evva` 現有 swarm（`docs/roadmap/veronica/example-swarm/`、`vero-tech-swarm/`）為準。

## 技術棧

- **Sunday 引擎（`engine/`）**：Python。Binance USDⓈ-M testnet（ccxt）；pandas/numpy（指標）；FastAPI（HTTP API + `/manual` + 自服 dashboard）；redis（熱狀態）；PostgreSQL（帳本 + 資訊層 + thesis/outcome 帳本，modeling-grade）。
- **dashboard（`engine/sunday/web/`）**：Vue 3（vendored 全域版，零 build）+ lightweight-charts，Sunday 自服於 `/dashboard`。
- **swarm**：evva（Go，**不在此 repo**）。我們**不寫 evva**，只**配置**它（`evva-swarm.yml` + `agents/`）。

## 與 evva 的關係（重要）

- evva 是 swarm runtime，**獨立 Go 專案在 `../evva`**（`/Users/johnny/lab/evva`）。
- 本專案是 evva swarm 的**使用者**：跑 `evva service start` + `evva swarm .`，靠 `:8888` API + RP-9 webhook 驅動。
- **不從這裡改 evva。** swarm 缺能力 → 回 `../evva` 開 refine-plan（RP），不在本 repo 改 `internal/swarm`。Sunday 只消費 evva 公開介面（這是 multi-agent completeness oracle 的重點）。
- 相關 evva 文件（`../evva/docs/roadmap/veronica/refine-plan/`）：`RP-9`（事件 webhook，已實作）、`RP-7`（timer 喚醒）、`RP-10`（skill 注入）、**`RP-11`（事件路由 + 窄 lever）、`RP-12`（advice-loop 閉合）= milestone-4 回填**。`http_request`（舊 RP-A）已 ship（`pkg/tools/web/http.go`），roster 已用。milestone-4 另請 evva 優先 `docs/roadmap/PRD/structured-output-tool.md` + `memory-typed-directory.md`（thesis / playbook 的天然載體）。

## 現況 / 節奏

- **已完成**：milestone-1（引擎 + 最小監督迴路 + 確定性風控 + 雙向 dead-man）、milestone-2（Sunday 自服 dashboard；後擴為 Vue 多頁 User UI）、milestone-3（agent legibility：`/advisor` 決策面板 + 閉迴路歸因 + 防禦式 skill）、milestone-4（研究台轉向：資訊層 `/desk`、thesis 驅動 `directed`、ablation 生死線、5-agent roster）。evva 側 RP-11（per-member 窄 lever）、RP-12（advice-loop 閉合）已 ship。
- **現況 = milestone-5（盤點/整鏈/固本）**：不開發新功能，把 m1→m4 的鏈路盤點乾淨、重構去重、修斷鏈，並優化 agent 協同認知（friday = 協調者）+ crontab（對齊不變量 6：timer 只當安全網）。讀 [docs/prd/milestone-5/](docs/prd/milestone-5/)。**128 單元測試綠。**
- **里程碑**：M1 → M2 → M3 →（轉向）M4 → **M5（固本，現行）** → **M4.1 一個月 testnet running test**（= 下一個 gate）→ Gate-2（真錢，gated on ablation 結果）。
- **待決**：product-plan §10 的數字（conviction→size 曲線、notable score 權重、test 封套）已先給 milestone-4 預設值，留待 running-test 期間按實況調；批 2 feed 來源未定。

## 慣例

- testnet API key / 密鑰放 `.env`，**永不 commit**（已 gitignore）。
- commit 訊息用 conventional prefix（`feat`/`fix`/`chore`/`docs`/`refactor`/`test`）。
- 寫任何 code 前，先確認沒違反上面 11 條不變量。
- **edge 主張一律附 ablation 證據**（不變量 11）——別把「看起來在忙」當成「有加值」。
