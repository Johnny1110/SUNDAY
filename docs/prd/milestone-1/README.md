# Milestone 1 — Gate-1（驗證 evva swarm 能力邊界）

> 上層 PRD：[`../sunday-project-prd.md`](../sunday-project-prd.md)。本資料夾把 **Gate-1** 拆成可逐步交付的 sub-PRD。
> **Milestone 1 = Gate-1**：在 Binance **testnet** 上證明 swarm 能正確監督 Sunday（成敗 = swarm 機制正確，**與獲利無關**）。最終驗證準則 = 上層 §9 的 **V1–V9**。

## 拆解（三個 sub-PRD = 上層 §10 的 S0/S1/S2）

| 版本 | 範圍 | Gate | 狀態 | 文件 |
| --- | --- | --- | --- | --- |
| **1.0** | **最小端到端監督迴路**：Sunday skeleton（testnet + momentum/flat + size/exposure 熔斷 + legible `/status` + pg ledger + notify + `/manual`）+ **friday + analyst** 兩角 + webhook + 切策略 + halt | 最小「`regime_shift` → friday → analyst 評估 → 切策略 → Sunday 反映 → halt」迴路在 `:8888` Web 看得到（testnet） | 🚧 **已拆 T1–T6、待開工** | [milestone-1.0/](milestone-1.0/)（overview + T1–T6，一個 session 一個 T） |
| **1.1** | 全 roster + 護欄 + 雙向 dead-man：risk-monitor / reporter / reviewer + `mean_reversion` + `/envelope` + drawdown breaker + safe/flat + 完整雙向 heartbeat + `/commentary` + 下令紀律 | V2 + V3 + V6 + V7 | 🚧 **大部分已實作**（roster + `/envelope`·`/commentary`·`/restart`·`/trades` + `daily_rollup_ready`；mean_reversion/drawdown/safe-flat 早在 M3 波就落地）；待 live 驗證 | （隨 `feature/milestone-3` 分支） |
| **1.2** | 耐久壓測 + 評估報告：多日 testnet run + 多標的籃子 + 量 V1/V4/V5/V8/V9 + 「swarm 能力邊界評估報告」 | V1–V9 全達 + 報告 | ⬜ 待開 | （待建） |

> **Gate-2**（真錢 + Sunday 自服 dashboard + 四個 extras）是 **milestone-2**，通過 milestone-1 後才開。

## 每個 sub-PRD 都繼承的不變量

見 [`../../../CLAUDE.md`](../../../CLAUDE.md) 的 10 條 load-bearing invariants。最常踩到的三條：
1. **evva 內零 Sunday-specific code**——agent 只用通用 `bash`+curl + skill + Sunday `/manual`。
2. **Gate-1 成敗與獲利無關**——1.0/1.1/1.2 的 gate 都不看 P&L。
3. **確定性風控在 Python/交易所層，永不在 LLM。**
