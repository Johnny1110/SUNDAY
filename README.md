# Sunday

> 一個 **Binance USDⓈ-M 永續合約交易系統**：Python 交易引擎（Sunday）出手交易，evva agent swarm 在上面監督。
> 真正目的是**驗證 evva swarm 的能力邊界**（Gate-1，testnet）；獲利是後續的獨立目標（Gate-2，真錢）。

## 這是什麼

- **Sunday（Python 引擎）**：串 Binance USDⓈ-M 永續 testnet，跑策略引擎（momentum / mean-reversion / flat），自主下單/平倉，內建確定性風險熔斷，資料落 PostgreSQL，並在「值得注意時」用 webhook 喚醒 swarm。它也是 **User-facing 系統 of record**（執行結果 + 決策理由 + 市場動態）。
- **swarm（evva，5 個 agent）**：不下單，**監督** Sunday。手握三個 meta-level 槓桿（切策略 / 設風險封套 / kill·重啟），透過通用 `bash`+curl 操作 Sunday 的 HTTP API。

> 完整設計見 **[docs/prd/sunday-project-prd.md](docs/prd/sunday-project-prd.md)**。

## 兩段閘門

| | Gate-1（現在） | Gate-2（後續） |
| --- | --- | --- |
| 目的 | 驗證 swarm 機制正確 | 追求真實獲利 |
| 環境 | Binance testnet / paper | 小額 mainnet |
| 成敗 | swarm 對不對（**與賺賠無關**） | 長期 P&L 為正 |

## 架構一覽

兩個平面、兩條 HTTP 邊界：

```
swarm（evva :8888 · Go · .vero SQLite）            Sunday（Python · :7777 · postgres + redis）
  friday(leader) + analyst/risk/reporter/reviewer
        │  bash + curl ──────────────────────────►  HTTP API（/status /strategy /halt /manual …）
        ◄──────────── RP-9 webhook ───────────────  notify()（regime_shift / risk_breach …）
                                                     └── Binance USDⓈ-M testnet（持倉真相）
```

- agent 永不碰 Sunday 的 postgres；Sunday 永不碰 swarm 的 `.vero`；**交易所是持倉最終真相**。
- **evva 內零 Sunday-specific code**——agent 只用通用工具 + 文件（per-role skill + Sunday `/manual`）操作 Sunday。

## 專案結構

```
sunday/
├── README.md
├── CLAUDE.md              # Claude Code 開發指引（每次 session 載入）
├── docs/
│   └── prd/
│       └── sunday-project-prd.md   # 權威 PRD
├── evva-swarm.yml         # swarm manifest（待建）
├── agents/                # swarm agents（待建）
│   ├── main/friday/
│   └── sub/{analyst,risk-monitor,reporter,reviewer}/
└── engine/                # Sunday Python 交易引擎（待建）
```

（`.vero/` 由 `evva swarm .` 自動建立，已 gitignore。）

## 現況 / 下一步

- ✅ PRD 完成（`docs/prd/`）。
- ⬜ **S0** — Sunday skeleton（testnet adapter + momentum/flat + 風險熔斷 + `/status`·`/strategy`·`/halt`·`/heartbeat`·`/manual`）+ friday/analyst 兩角 + 最小監督迴路。
- ⬜ **S1** — 全 roster + 護欄 + 雙向 dead-man。
- ⬜ **S2** — 耐久壓測 + 評估報告。

詳見 PRD §10。

## 跑起來需要（待補）

- **evva**（swarm runtime，在 [`../evva`](../evva)）：`evva service start` + `evva swarm .`。
- **Binance USDⓈ-M testnet** API key（放 `.env`，**勿 commit**）。
- **PostgreSQL** + **Redis**（本機）。
- **Python 3.x**（`engine/`）。

## 與 evva 的關係

evva 是 swarm 的 runtime（Go，獨立 repo 在 [`../evva`](../evva)）。本專案是 evva swarm 的**使用者**：提供 `evva-swarm.yml` + `agents/`，靠 `:8888` 服務 + RP-9 webhook 驅動。**我們不從這裡改 evva**——swarm 缺能力就回 evva 開 refine-plan。這是本實驗的重點：swarm 只靠公開介面就能被外部系統驅動。

## 安全

Gate-1 **全程 testnet、零真錢**。交易 lever 走 permission 審批；硬限額在 Python/交易所層（非 LLM）。真錢是 Gate-2 通過後的獨立決策。
