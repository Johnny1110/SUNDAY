# T5 — 自給自足的 webhook payload

> milestone-3 任務 **5/6** ｜ 契約見 [`README.md`](README.md) §2 ｜ **依賴**：M1.0-T4 `notify()`（impl 折入一起做）

## 做什麼

agent 醒來看到的第一句話，決定它行動好不好。讓 Sunday 寄的「信」**自給自足**：被喚醒的 agent **首輪不必 curl** 就能定位狀況。也順帶緩解「payload 是當時、決策要看現在」的時差——agent 仍照 §7.10 重抓，但 payload 先把脈絡給足，少打幾趟 API、少燒 token。Sunday 是 swarm 的「非 LLM 隊員」，它寄的信品質 = worker→leader 回報的品質。

## 交付

- **`notify()` 的 `data` 自帶三樣**：
  - `status`：當下 `/status` 快照（省一趟 round-trip）。
  - `rationale`：為何判定 regime 改變 / 破封套 + **觸發指標**（如「波動率破 3σ」「drawdown 4.8% 逼近上限 5%」）。
  - `suggested_action`：如「考慮由 momentum 切 mean_reversion；先 `curl /signals` 複核再決定」。
- 對齊上層 §7.9：**每個事件都帶觸發依據**（硬需求）。
- `webhook_log` 既有欄位不動；擴充內容寫進 `data`，`/manual` 補事件 payload schema。

## Done

- **A5**（README §4）：注入假 `regime_shift` → 被喚醒 agent **首輪不 curl** 即可說出狀況與下一步。

## 不在本任務

- 事件**收件人路由**改動（上層 §12.3，待決：全進 leader vs 直送 analyst/risk-monitor）。
- 其餘事件型別（`risk_breach`/`safe_mode_entered`…，milestone-1.1）。
