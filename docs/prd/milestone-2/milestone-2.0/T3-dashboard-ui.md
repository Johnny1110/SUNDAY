# T3 — Sunday 自服 dashboard 頁面

> 2.0 任務 **3/4** ｜ 共用契約見 [`README.md`](README.md) ｜ **依賴：T2** ｜ **守 D12：Sunday serve，不塞 evva**

## 做什麼
Sunday 在 `GET /dashboard` serve **一頁自含 HTML**，polling T1/T2 的 JSON 端點，畫出 D14 的全部。**零 build step、零 node、零框架**：vanilla JS + 一個 CDN chart lib（uPlot 或 Chart.js）。**只讀既有 JSON 端點，不新增任何資料邏輯。**

### 版面（單頁，由上而下）
1. **頁首狀態列**：`/status` 的 `mode` / `strategy` / `equity` / `swarm_heartbeat_ok`（綠/紅燈）。
2. **權益曲線**（主圖，折線）：`/pnl.equity_curve`；在 X 軸標 `/strategy_history` 的切換點（垂直線 + hover tooltip 顯示 `strategy` + `reason`）= **D14「切換理由疊圖」**。
3. **30 日 PnL**（數字卡）：realized + unrealized + equity（`/pnl`，30 日窗）。
4. **當前倉位**（表）：`/positions` — side / qty / entry / mark / upnl **/ strategy / entry_reason / stop**。
5. **per-strategy 歸因**（表）：`/performance` — 每策略 realized_pnl / 筆數 / 勝率。
6. **commentary feed**（時間倒序清單）：`/commentary` — analyst 的市場動態（title + body + 時間）。
- **自動刷新**：每 15–30s re-fetch 一次（單頁、無 WS 也行）。

### 實作
- `app.py` 加 `GET /dashboard` 回 `HTMLResponse`（讀 `sunday/dashboard.html`）。
- `dashboard.html` 自含；chart lib 走 CDN `<script>`（離線時優雅退化成只顯示數字/表，不白屏）。
- **不新增資料端點**——全靠 T1/T2 既有 JSON。

## 驗收
- [ ] 瀏覽器開 `http://127.0.0.1:7777/dashboard`，一頁看到 6 個區塊。
- [ ] 權益曲線畫得出 + 切換點標記 + hover 顯示 `reason`。
- [ ] commentary feed 顯示 analyst 貼文。
- [ ] **無 build step**（直接開頁就動）；**evva 內零新增程式碼**（全在 `engine/`）。

## 不在本任務
- analyst 寫 commentary 的 skill recipe / e2e 串接（T4）。
- telegram / 外部訊號源（2.1）。
