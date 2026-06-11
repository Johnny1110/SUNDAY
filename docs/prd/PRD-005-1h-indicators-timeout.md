# PRD-005 — /api/klines/indicators interval=1h 連續逾時

## 1. 卡在哪（問題）

**場景：** `GET /api/klines/indicators?symbol=BTCUSDT&interval=1h&set=rsi,ema,macd,bollinger,adx,atr` 在 2026-06-11 例行巡檢期間連續 3 次 client timeout（`context deadline exceeded`）。同一端點的其他 interval（5m、15m、4h）與 `/api/funding`、`/api/klines` 均正常回應。

**重現率：** 同日 3/3 次失敗（analyst-flow 回報）。5m/15m/4h interval 同時段全部正常。

**影響：**
- analyst-flow 無法取得 1h EMA/MACD/Bollinger，被迫從 raw klines 手算 EMA——增加延遲與誤差
- 1h 是 BTC 極短線與短線判斷的關鍵時間框（EMA 交叉、Bollinger 區間），缺 1h 指標 = 交易決策盲一隻眼
- 尤其在極短線模式（User 授權測試）下，需要多時間框快速對照，1h 不可用直接拖慢節奏

**推測根因：** 1h interval 的指標計算可能涉及較大歷史資料集（100 根 1h = 4 天數據），計算量或資料源查詢耗時超過 client timeout。但 4h interval 同樣需要大量歷史數據卻正常——問題可能特定於 1h 的計算路徑。

## 2. 期望的 API 長相

修復 `/api/klines/indicators?interval=1h` 的計算效能或 timeout 設定，使其與其他 interval 一致正常回應。不改變 API 介面。

若短期無法修復，建議：
- 1h 指標新增 caching 層（每 5 分鐘重算一次足夠，不需每次 request 重算）
- 或增加 client timeout 寬容度（目前 5m/15m 正常回應表示 timeout 設定對即時 interval 夠用，1h 可能需要更長）

## 3. 為什麼有助於 10% 月目標

- **分析品質**：1h 是 BTC 交易的核心決策時間框。EMA 交叉、Bollinger 突破/拒否、MACD 翻轉——這些是 analyst-flow 判讀方向的基礎。缺 1h 指標 = 交易決策基礎不完整。
- **極短線模式的時間框對照**：5m 進場、15m 確認、1h 過濾——失去 1h 過濾層，錯誤進場機率上升。
- **團隊效率**：analyst-flow 手算 EMA 耗費 token 與時間，這些資源應該用在分析上而非 data plumbing。

— friday, 2026-06-11

---

## 處置（已修復，2026-06-11）

**調查結論：「1h 特定」不成立，真正的缺陷是指標路徑沒有退化策略。** 逐行檢查後確認
`/api/klines/indicators` 對所有 interval 走同一條路徑（同一 ccxt 呼叫、同樣 200 根、純 Python
指標計算 < 1ms），程式內**不存在**任何 1h 分支；實測 Binance fapi 與完整引擎路徑，1h 與
5m/4h 同樣 ~100ms。3/3 失敗最可能是一次上游不穩（網路抖動 / Binance 延遲尖峰 / fan-out
排隊）恰好落在巡檢的 1h 呼叫上——而當時的設計讓**任何**上游卡頓都直接變成 client 整段
timeout（`context deadline exceeded`），重試也只是重新排隊，所以看起來像「1h 連續壞」。

**修復內容（採納 §2 的快取建議，API 介面不變、只增欄位）：**

1. 新增 `ttlcache.StaleCache`（純 stdlib）：TTL 內供應快取、過期重抓、**上游失敗時供應
   最後一次成功值**。
2. `/api/klines/indicators` 的 K 線抓取套用此快取，key =（symbol, interval, limit），TTL 隨
   interval 比例調整（≈bar/20，夾在 10s–300s：5m→15s、1h→180s、4h→300s——符合「1h 每
   5 分鐘重算就夠」）。
3. 退化語義：上游故障 → 回最後成功面板 + `stale: true` + `stale_age_s`（無快取可退時維持
   清楚的 502）。**上游卡頓後的重試會瞬間命中快取**（實測 ~1ms），不再吃第二次 timeout；
   巡檢的重複呼叫也不再重複打上游。
4. client timeout 寬容度屬 evva 端設定（`http_request`），本 repo 不改 evva；有了 stale 退化
   後 client deadline 不再是硬依賴。

**驗證：** `tests/test_ttlcache.py`（純邏輯）+ `tests/test_klines_cache.py`（端點行為：快取
命中不重抓、interval 獨立、stale 供應、cold 502）；本地起引擎實測 1h×3 + 4h（run1 上游
300ms、run2/3 快取 ~1ms，`as_of` 一致、fresh 不帶 stale 旗標）。全套 153 綠。
