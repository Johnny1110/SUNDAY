# milestone-5 — 盤點、整鏈、固本（consolidation）

> 狀態：**進行中（2026-06-09 起）** ｜ 前置：milestone-4（研究台轉向）已完成、RP-11/12 已在 evva ship。
> 性質：**不開發新功能**。把 m1→m4 累積的鏈路盤點乾淨、重構去重、修斷鏈、優化 agent 協同認知與 crontab。
> 目標：**把地基搭好**，為下一個 gate（M4.1 一個月 testnet running test）與 Gate-2 鋪路。

## 為什麼需要這個階段

m1→m4 是一路加功能堆上來的（引擎 → dashboard → legibility → 研究台轉向）。轉向時 `/signals` 併進 `/advisor`、`/status` 從單標的擴成籃子、`/strategy` 旁邊長出 `/thesis`——**舊鏈沒有同步收尾**，留下三類債：

1. **斷鏈**：功能寫了、測了，卻沒接回端點（最危險——看起來有、其實沒作用）。
2. **死碼**：milestone-3 的 view builders 被 milestone-4 繞過，只剩測試引用。
3. **重複**：`advisor.py` 與 `strategy.py` 兩份投票邏輯；`engine` 的 baseline/directed 兩條幾乎一樣的執行路徑。

agent 側則是：friday 偏「執行者」而非「協調者」，workers 各自為政沒有共享心智模型，crontab 有一條（analyst-news 30m 掃新聞）與不變量 6（event-gated、timer 只當安全網）相牴觸。

## 盤點結果（findings）

### A. Sunday engine

| # | 類型 | 位置 | 問題 | 處置 |
|---|---|---|---|---|
| A1 | 斷鏈 | `app.py` POST `/strategy` | 繞過 `views.apply_strategy`，inline 驗證且**不要求 reason**（違反 §7.11 lever 留證） | 回接 `apply_strategy`，刪 inline 重複 |
| A2 | 斷鏈/錯誤 | `app.py` `/status` | exposure 只累加主標的，籃子 ETH/SOL 曝險被忽略 → 籃子曝險數字錯 | 籃子感知：聚合全籃子曝險 + per-symbol `basket` 陣列；保留頂層欄位向後相容 |
| A3 | 斷鏈 | `events.py` `engine_degraded` | suggested_action 叫 leader `POST /restart`，無此端點 | 改 hint（服務重啟非 HTTP；查 /status，異常通報 User） |
| A4 | 死碼 | `views.py` | `signals_view`/`status_view`/`votes_summary` 無 production caller（無 `/signals`；`/status` 不走它） | 刪（含測試） |
| A5 | 死碼 | `risk.py`/`strategy.py` | `max_allowed_qty`、`target_side` 無 production caller | 刪（含測試），修過期 docstring |
| A6 | 重複 | `advisor.py` ↔ `strategy.py` | 兩份 momentum/MR 投票邏輯 + 兩份門檻常數 | advisor 消費 strategy 的 `Vote`（單一真相） |
| A7 | 重複 | `engine.py` | `reconcile`/`_reconcile_directed`、`_open`/`_open_directed` 各重複一遍 | 抽 `_apply_transition` + `_enter`；directed/baseline 只差 sizing/stop/thesis_id |
| A8 | 過期文件 | `app.py` header、`manual.md` | header 是 m1 語言；manual 說 dashboard「五區」實際 7 區 | 更新 |
| A9 | 封裝 | `app.py` `/status`·`/positions` | 直接用私有 `exchange._sym` | 提供公開比對 helper |

### B. Agent（協同認知）

| # | 位置 | 問題 | 處置 |
|---|---|---|---|
| B1 | `friday/system_prompt` | 偏「執行 thesis」，協調者職責（派工、裁決衝突、回信閉環、對 User 敘事）不夠突出 | 重寫為 desk lead：**協調 > 下單** |
| B2 | 全 roster | 各角色只知自己那塊，沒有共享心智模型 | 每個 prompt 加共用「研究台如何運作 / 隊友是誰 / 一輪節奏 / 你的位置」段 |
| B3 | 斷鏈 | `query-sunday` skill（risk-monitor+reviewer 共用）缺 `/risk`·`/desk`·`/theses`·`/ablation`，但兩者 prompt 都用 | 依角色裁剪各自的 skill 端點清單 |
| B4 | `risk-monitor` | RP-11 窄 lever 已 ship，prompt 還寫「在那之前一律建議」 | 更新語言（部署可給 `permissions.json` safe-halt；給了即可直接 POST /halt safe） |

### C. crontab + schedule prompts

| 角色 | 現況 | 調優後 | 理由 |
|---|---|---|---|
| friday (leader) | every 30m | **保留 30m** | dead-man heartbeat 命脈：timeout 90m → 30m 給 3× 餘裕 |
| analyst-flow | event-driven only | 保留 | 由 funding/oi/basis 事件喚醒，正確 |
| analyst-news | **every 30m 掃新聞** | **every 6h 安全網巡檢** | 30m web 掃 = 市場輪詢 + 燒 token，違反不變量 6；重大事件由 `catalyst` 事件即時喚醒 |
| risk-monitor | every 30m | **every 1h** | 確定性熔斷已在 Python 毫秒級；策略級巡檢 1h 足夠，省一半 token |
| reviewer | cron 17:00 daily | 保留 | 每日復盤 |
| 全部 | 各自 prompt | 統一收尾「無事一句 stand down」+「這是安全網不是輪詢」 | 避免 timer 喚醒就長篇燒 token |

## 不碰什麼（守住範圍）

- **不改 evva**（RP-11/12 已 ship；缺能力才回 evva，本階段不缺）。
- **不加新端點 / 新策略 / 新資訊源**（批 2 feed、conviction 曲線等留給 running-test 期間按實況調）。
- **不動 migrations / DB schema**（系統真相穩定）。
- **不破 11 條不變量**（每次改動先對照）。

## 驗收

- 143 測試（調整死碼後的等量）全綠；無 production 行為退化。
- 斷鏈全修：`/strategy` 要 reason、`/status` 籃子曝險正確、prompt↔skill 端點一致、文件與實作一致。
- 重複消除：投票邏輯單一真相、engine 單一風控進場路徑。
- agent prompt：friday 協調者定位明確、全員有共享心智模型、crontab 對齊不變量 6。
- CLAUDE.md「現況/節奏」更新為 milestone-5。
