# T4 — swarm wiring + e2e demo + 驗收 B1–B7

> 2.0 任務 **4/4** ｜ 共用契約見 [`README.md`](README.md) ｜ **依賴：T1–T3** ｜ 收尾任務，不新增功能

## 做什麼
把 dashboard 接回 swarm 迴路、照腳本跑一遍、打勾 milestone 級驗收 B1–B7。**只串接 / 跑通 / 驗證 / 補洞。**

### 1. analyst skill：加 commentary recipe
- `agents/sub/analyst/skills/query-sunday/SKILL.md` 加一條 `POST /commentary` recipe（§7.11：analyst 推市場動態給 User；**無害寫入、auto-allow、非交易 lever**）。
- 引導：analyst 評估完 regime、`send_message` 給 friday 之後，也 `POST /commentary` 留一則 **User-facing 市場脈絡**（這是諮詢角色唯一的寫入）。
- skill 經 RP-10 Web skill 管理；更新後成員下一輪即載入新版（agent 不自寫 skill）。

### 2. friday skill/prompt：`reason` 是給人看的
- `agents/main/friday/skills/operate-sunday/SKILL.md` 補一句：`/strategy` 的 `reason` 會**直接顯示在 User dashboard 的切換時間軸上**——寫成人看得懂的決策理由，不是內部代號。

### 3. e2e demo（testnet）
1. engine + swarm 起；瀏覽器開 `/dashboard`（先空著也行）。
2. 觸發一次 regime → friday 指派 analyst → analyst **`POST /commentary`**（feed 立刻出現一則）+ `send_message` 建議 → friday **`POST /strategy`（附 reason）** → dashboard 出現切換點 + reason 疊圖、倉位表更新。
3. 讓引擎跑一段 → 權益曲線累積點；平倉一次 → `/performance` 出現該策略的 realized。
4. 全程確認：dashboard 全由 Sunday（:7777）serve；evva 端零新增程式碼。

## 驗收（B1–B7，逐項打勾）— ✅ 全達（2026-06-07 live testnet e2e）
- [x] **B1 權益曲線**：`/pnl.equity_curve` 9+ 點、隨 tick 累積；dashboard 折線渲染（含開倉時段快照 equity 4998.50）。
- [x] **B2 30 日 PnL**：`/pnl` 回 `realized=-0.0248`、`equity=4998.5`、`window_days=30`；數字卡顯示。
- [x] **B3 倉位**：開倉中 `/positions` 回 `strategy=momentum` + 完整 `entry_reason` + `stop=60929.7`（DB join）。
- [x] **B4 歸因**：`/performance` momentum `n_trades=3`、`realized_pnl=-0.0248`、`win_rate`（realized 由平倉時 unrealizedPnl 捕捉）。
- [x] **B5 切換理由疊圖**：`/strategy_history` 6 筆切換含 `reason`；本輪 18:26 的切換在曲線窗內 → 圖上標記 + tooltip 顯示理由。
- [x] **B6 commentary feed**：analyst `POST /commentary`（id 1、2）顯示於 feed（時間倒序）。
- [x] **B7 D12 + testnet**：dashboard 全由 Sunday（:7777）自服；**evva repo 全程 clean（零新增 Sunday code）**；全程 testnet、無真錢。

## 不在本任務
- 2.1（telegram / 外部訊號源）、2.2（回測 / ML / 多策略）、2.3（真錢 / token 硬化）。
- 觀察記錄：dashboard 對「在耐久 run 期間觀測 1.1/1.2」是否好用 → 寫一句結論，餵後續里程碑。
