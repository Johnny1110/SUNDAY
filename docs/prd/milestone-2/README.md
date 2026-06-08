# Milestone 2 — Gate-2（真錢 + dashboard + 四個 extras）

> 上層 PRD：[`../sunday-project-prd.md`](../sunday-project-prd.md)（§10 Gate-2、決策 **D11/D14**、§7.7 schema、§7.11 User-facing 系統 of record）。本資料夾把 **Gate-2** 拆成可逐步交付的 sub-PRD，與 [milestone-1](../milestone-1/) 同構：**一個 sub-PRD 一個資料夾、一個 session 一個 T**。
> **Milestone 2 = Gate-2**：在 Gate-1 證明 swarm 機制正確後，追求「**賺不賺 + User 可觀測 + 情報/研究/真錢硬化**」。**Gate-2 真正成敗 = 真實長期 P&L**（§2.1：生產等級 ≠ 賺錢；alpha 是研究問題、工程換不到）。

## ⚠️ 閘門紀律（動工前先讀）

- **真錢是 2.3、而且硬 gated。** invariant #1/#10：Gate-1 全程 testnet。**小額 mainnet（2.3）只有在 Gate-1 的 V1–V9 全達（即 milestone-1.0 / 1.1 / 1.2 全綠）後，才是一個獨立的 go-live 決策。** 2.0–2.2 **全程 testnet，不碰真錢**。
- **2.0（dashboard）可與 1.1 / 1.2 平行、甚至先做。** 它是**純讀 + 一個無害寫入（commentary）、零真錢**，不違反兩段閘門；而且它正好讓你**在 1.1 / 1.2 的耐久 run 期間看得到** PnL / 權益 / 決策理由——是觀測 Gate-1 後段的工具，越早有越好。
- **守 D12。** dashboard 由 **Sunday 自服**（FastAPI 服 `:7777`），**不塞進 evva swarm UI**——evva 內仍是零 Sunday-specific code（這正是 D12 的能力邊界主張）。

## 拆解（四個 sub-PRD = 上層 §10 Gate-2 的四塊）

| 版本 | 範圍 | 環境 | Gate | 狀態 | 文件 |
| --- | --- | --- | --- | --- | --- |
| **2.0** | **Sunday 自服 execution dashboard**：補齊資料捕捉（權益曲線快照 / realized 歸因 / commentary）+ Sunday 服一頁 web UI（權益曲線 / 30 日 PnL / 倉位 / per-strategy 歸因 / 切換理由疊圖 / commentary feed） | testnet | User 在一頁 Sunday-served dashboard 看到 D14 的全部（testnet 資料） | ✅ **完成（2026-06-07）— B1–B7 全達** | [milestone-2.0/](milestone-2.0/)（overview + T1–T4） |
| **2.1** | **情報 extras**：analyst 外部輸入（fear & greed / on-chain / 新聞 web）+ telegram 對外播報 | testnet | analyst commentary 有真實外部訊號來源；team 狀態經 telegram 對外播報 | ⬜ 待開 | （待建） |
| **2.2** | **研究 extras**：回測引擎（postgres 歷史回放）+ Sunday 內 ML 建模 + 多策略 / 策略精進 | testnet | 能在捕捉的歷史上回測 + 訓練模型；多策略並行 | ⬜ 待開 | （待建） |
| **2.3** | **Go-live 硬化 + 小額 mainnet**：webhook 窄權限 token + Sunday command 端點 token + 小額 mainnet（獨立 go-live 決策） | **mainnet** | 真實長期 P&L 為正（Gate-2 真正成敗）；**硬 gated：須 Gate-1 V1–V9 全達 + 2.0–2.2 就緒** | 🔒 鎖（待前置） | （待建） |

> **排序理由**（D11：四 extras 全做、sequencing 到 Gate-2）：**先讓系統對 User 透明（2.0）→ 餵真實情報（2.1）→ 能研究/精進策略（2.2）→ 最後才上真錢（2.3）。** 真錢放最後，因為前三塊都不需要它，而它最不可逆。

## 每個 sub-PRD 都繼承的不變量

見 [`../../../CLAUDE.md`](../../../CLAUDE.md) 的 10 條 load-bearing invariants。Gate-2 最常踩到的三條：
1. **D12 零 Sunday-specific evva code** —— dashboard / telegram / 一切都由 **Sunday 自服**或 Sunday 端做，evva 只用通用 `bash`+curl / `http_request` + skill + `/manual`。
2. **真錢硬 gated** —— 2.3 之前一律 testnet；上真錢是 Gate-1 通過後的獨立決策，不是某個 task 的副作用。
3. **確定性風控永遠在 Python / 交易所層** —— 上真錢（2.3）只是把封套數字改小 + 換 mainnet key，**風控架構不動**；LLM 永不在快路徑。
