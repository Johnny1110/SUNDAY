# T6 — 回填 evva 的 3 份 refine-plan（不改 sunday code）

> milestone-3 任務 **6/6** ｜ 契約見 [`README.md`](README.md) §2 ｜ **依賴**：回 `../evva`（守不變量 #4）

## 做什麼

走一輪 agent 體驗暴露的、屬於 **swarm runtime（evva）** 的缺口，一律回 `../evva/docs/veronica/refine-plan/` 開 RP——**不在本 repo 改 evva**。Sunday 只當 swarm 的*使用者 + 需求來源*；這正是 multi-agent completeness oracle 的紀律（不變量 #4 / D5）。

## 交付（三份 RP 草案，提到 `../evva`）

1. **`http_request` 工具升第一順位**（上層 §6.4）：2026-06-08 實跑證實 `curl→python` 是 agent 最常出錯的一段（拼字串、漏 `Content-Type`、解析 raw JSON）。通用、非 Sunday-specific（D12 已認證），把不可靠體力活換成結構化 I/O；能力邊界主張更乾淨（「一個通用 HTTP 工具 + 文件操作任意外部系統」）。
2. **單一漏斗緩解**（上層 §5 警告 / §12.3·12.7）：所有事件→leader、又只有 leader 能拉桿；幣圈高相關，崩起來所有標的同時發事件全擠進 leader 一個 run（drain B 折疊）。提案：`risk_breach` 直送 risk-monitor + 給它一根**窄 halt lever**（只能 halt、不能切策略/設封套）。
3. **agent↔agent 閉迴路**：leader 採納 / 不採納諮詢角色建議後**回信說明**，否則 analyst / risk 在對虛空喊話、無法改進。可能落點 = swarm 協作協議的「回報」紀律強化（`teamprompt.go` 的 worker/leader protocol）。

## Done

- 三份 RP 文件在 `../evva` 開好（含 file:line 證據、acceptance、對齊 RP 既有格式）。
- 本 repo **零 evva code 改動**（沿用 §9 V9 / 不變量 #4）。

## 不在本任務

- 在 evva **實作**這些 RP（那是 evva 的排程）；本 ticket 只負責「把需求以 RP 形式回填」。
