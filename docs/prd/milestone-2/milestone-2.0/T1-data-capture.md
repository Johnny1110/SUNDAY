# T1 — 資料捕捉補齊（pnl 快照 + realized 歸因 + commentary + 倉位補欄）

> 2.0 任務 **1/4** ｜ 共用契約見 [`README.md`](README.md) ｜ **依賴：engine 1.0（已就緒）**

## 做什麼
把 dashboard 要畫的資料「在 run 期間**真的被寫進** postgres」。1.0 的 schema 已 modeling-grade，但三個資料源是空的 / stub：權益曲線無人寫、realized PnL 無歸因資料、commentary 表/端點不存在。本任務只補**資料層 + 寫入端點**，**不做任何 UI**。

### 1. 權益曲線資料源：開始寫 `pnl_snapshots`
- `pnl_snapshots`（0001 已建）**目前無人寫** → equity_curve 永遠空。
- 在 watch loop（`app._watch_loop` → `strategy.tick`）每 tick 記一筆：`{ts, equity, realized, unrealized, drawdown_pct}`。`equity`/`unrealized` 取自 exchange balance/positions；`realized` 累計自 closed positions；`drawdown_pct` 相對歷史權益高點。
- **交易所不可達時跳過該 tick**（別寫 0/null 污染曲線）。
- store.py 加 `record_pnl_snapshot(...)` DAO。

### 2. realized PnL 歸因源：平倉時寫 `positions.realized_pnl`
- 確認 `strategy.halt(flat)` 與一般平倉路徑有把 DB `positions` row 的 `closed_at` + `realized_pnl` 寫回；**缺則補**（1.0 主要驗開倉，平倉的 realized 寫入可能未落地）。
- 歸因 = 「closed positions 的 `realized_pnl` GROUP BY `strategy`」——這筆不寫，T2 的 `/performance` 就沒東西可算。

### 3. commentary：表 + 端點（§7.11，analyst 用）
- migration `0002_dashboard.sql`：`commentary(id BIGSERIAL PK, ts TIMESTAMPTZ NOT NULL DEFAULT now(), author TEXT, title TEXT, body TEXT)`。
- `POST /commentary {author,title,body}` → 寫一筆 → `{ok,id}`。**無害寫入、非交易 lever → allow-rule auto**（不跳審批）。
- `GET /commentary?since=&limit=` → 時間倒序貼文。
- store.py 加 `record_commentary()` / `list_commentary()` DAO。

### 4. `/positions` 補欄
- 1.0 的 `/positions` 只讀 exchange，`strategy`/`entry_reason`/`stop` 全是 `None`。
- 與 DB `positions`（open = `closed_at IS NULL`）join，補上真的 `strategy` / `entry_reason` / `stop_price`（對不上交易所倉位時保留交易所為準、DB 欄位填 null 並不報錯）。

## 驗收
- [ ] run 幾分鐘後 `SELECT count(*) FROM pnl_snapshots` > 0，且 `equity` 隨時間有不同點。
- [ ] 平一筆倉後，該 `positions` row 有 `closed_at` + 非 null `realized_pnl`。
- [ ] `curl -sX POST :7777/commentary -d '{"author":"analyst","title":"…","body":"…"}'` 寫入成功；`GET /commentary` 讀得回；**無害寫入不跳審批**。
- [ ] `GET /positions` 對 open 倉回傳真的 `strategy`/`entry_reason`/`stop`。

## 不在本任務
- 任何 UI / 圖（T3）。
- `/pnl` 擴充、`/performance`、`/strategy_history`（T2）——本任務只負責「把資料寫進去」，讀聚合在 T2。
