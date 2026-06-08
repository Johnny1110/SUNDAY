# Sunday 操作手冊（`GET /manual`）

Sunday 是一個 Binance USDⓈ-M 永續 **testnet** 執行/風險/資訊基板。它自己下單/平倉、跑確定性風險熔斷、ingest 資訊層；
你（swarm 研究台）的工作是**經營決策**：看 `/desk` 哪裡有事 → 研究 → 下 **thesis**（方向+信念）驅動 `directed` 執行，必要時叫停。
用 **`http_request`** 工具操作（GET 免審批；lever POST 跳審批，僅 leader）。base = `http://127.0.0.1:7777`。籃子 = `BTCUSDT, ETHUSDT, SOLUSDT`（1h）。

> **策略**：`directed`（thesis 驅動，milestone-4 主用）/ `momentum` / `mean_reversion` / `flat`。研究先看 `GET /desk` 與 `GET /advisor`。
> **〔milestone-4〕** 資訊層 `/desk`（funding/OI/基差 + notable score）、thesis 帳本 `/thesis`·`/theses`、ablation `/ablation`；dashboard 自服於 `/dashboard`。

## 唯讀（auto-allow，不需審批）

```bash
# ★★ 研究台「此刻看哪裡」：全籃子 notable 排序（funding/OI/基差 + notable score）
curl -s http://127.0.0.1:7777/desk
# 單標的深掘（+ advisor：regime / votes / funding context）
curl -s "http://127.0.0.1:7777/desk?symbol=BTCUSDT"

# 當前 active thesis（驅動 directed 執行）/ thesis 史 + 結果（outcome 歸因）
curl -s "http://127.0.0.1:7777/thesis?symbol=BTCUSDT"
curl -s "http://127.0.0.1:7777/theses?limit=20"

# ★ 決策支援面板：每策略此刻的投票 + 指標 + regime + funding（永續資金費）+ 建議策略
curl -s "http://127.0.0.1:7777/advisor?symbol=BTCUSDT"

# 整體狀態：account-level（mode / equity / 全籃子聚合曝險 / heartbeat）+ 每標的 basket[]（strategy / thesis / position）
curl -s http://127.0.0.1:7777/status

# 行情 OHLCV
curl -s "http://127.0.0.1:7777/market?symbol=BTCUSDT&tf=1h&limit=100"

# 持倉（含 strategy / entry_reason / stop）
curl -s http://127.0.0.1:7777/positions

# 損益 + 權益曲線（realized 來自 DB、equity/unrealized 即時；equity_curve=[[ts_ms,equity]...]；預設 30 日窗）
curl -s "http://127.0.0.1:7777/pnl?since=2026-05-09"

# per-strategy 績效歸因（realized_pnl / n_trades / win_rate / avg_pnl / open_qty）
curl -s http://127.0.0.1:7777/performance

# 策略切換時間軸（含每次切換的 reason，給 dashboard 疊圖）
curl -s http://127.0.0.1:7777/strategy_history

# 市場動態 commentary feed（analyst 貼文，時間倒序）
curl -s "http://127.0.0.1:7777/commentary?limit=20"

# 當前風險封套（active caps；未設過則回 config 預設）
curl -s http://127.0.0.1:7777/envelope

# 風險面板：封套 vs 即時讀數 + 各上限使用率 + 當前違規 + 近期確定性熔斷事件
curl -s http://127.0.0.1:7777/risk

# 成交帳本 / blotter（orders，時間倒序）
curl -s "http://127.0.0.1:7777/trades?limit=50"

# Sunday 對 swarm 發過的喚醒事件（webhook_log：regime_shift / risk_breach / …）
curl -s "http://127.0.0.1:7777/events?limit=50"
```

## 市場動態 commentary（analyst 專用；無害寫入、auto-allow、非交易 lever）

```bash
# analyst 把當前市場脈絡推給 User（顯示在 dashboard feed）
curl -sX POST http://127.0.0.1:7777/commentary \
  -H 'Content-Type: application/json' \
  -d '{"author":"analyst","title":"BTC regime","body":"波動率下降，盤整偏多"}'
```

## Dashboard（User 在瀏覽器看；Sunday 自服）

`http://127.0.0.1:7777/dashboard` —— 專業量化終端風格 Web UI（Vue，Sunday 自服）。七頁：
**Overview**（KPI / 權益曲線 + 切換理由疊圖 / 籃子倉位 / advisor）、**Desk**（全籃子 notable 排序 + 單標的微結構深掘）、
**Strategy**（advisor 決策面板 + 切策略 / 下 thesis lever + 行情圖）、**Risk**（封套編輯 + 使用率 + 風控事件）、
**Ablation**（資訊層 vs no-trade 基準 + info-ON/OFF 拆分——edge 的生死線，不變量 11）、**Reports**（commentary + 決策/事件時間軸）、
**Manual**（本手冊 + API console）。
User 由此能行使與 agent 等價的全部操作（lever 經同一組端點，確定性風控仍是最終防線）。

## Lever（POST；需 permission 審批；僅 leader）

```bash
# 〔milestone-4〕下 thesis（desk lead 的主要手段）：方向 + 信念，驅動 directed 確定性執行。
#   conviction 0..1 → 倉位大小（封套內，<0.2 視為 flat）；invalidation_price → stop；rationale 必填。
curl -sX POST http://127.0.0.1:7777/thesis \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","direction":"long","conviction":0.4,"rationale":"funding 轉負、無迫近事件","invalidation":"跌破 4h 支撐","invalidation_price":60000}'

# 切換當值策略（reason 必填）：directed（thesis 驅動）/ momentum / mean_reversion / flat
curl -sX POST http://127.0.0.1:7777/strategy \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","strategy":"directed","reason":"轉由 thesis 驅動"}'

# 叫停：mode=flat 全平、mode=safe 凍新倉（既有倉留 stop）
curl -sX POST http://127.0.0.1:7777/halt \
  -H 'Content-Type: application/json' \
  -d '{"reason":"demo 結束","mode":"flat"}'

# 設風險封套（lever；reason 必填；立即生效於下一輪 reconcile/tick）
curl -sX POST http://127.0.0.1:7777/envelope \
  -H 'Content-Type: application/json' \
  -d '{"max_position_usd":2000,"max_total_exposure_usd":4000,"max_leverage":3,"max_drawdown_pct":5,"stop_pct":0.02,"reason":"testnet 保守封套"}'
```

## liveness（leader 的 dead-man ping）

```bash
curl -sX POST http://127.0.0.1:7777/heartbeat -d '{}'
```

Sunday 連續一段時間（預設 90m）收不到 heartbeat → 自動停開新倉（safe 地板）。

## 策略

- `momentum`：EMA20 × EMA50 cross（1h）順勢開多/空（趨勢盤）。
- `mean_reversion`：布林帶 z + RSI14。超賣（z≤-1 且 RSI≤35）偏多、超買（z≥1 且 RSI≥65）偏空（震盪盤）。
- `flat`：空手（既有倉平掉）。
- `directed`〔milestone-4〕：由當前 **thesis** 驅動（方向 + conviction）；Sunday 確定性決定倉位大小（conviction × 單筆上限）、依 `invalidation_price` 掛 stop、自動進出。swarm 用 `POST /thesis` 設定，是研究台的主用模式。
- **研究先看 `GET /desk`（此刻哪裡有事）＋ `GET /advisor`（單標的投票/regime/funding）。**

## 風險封套（確定性、Python 層硬擋）

- 硬限額：單筆上限 / 總曝險上限 / 最大槓桿 / 進場必掛 stop / **最大回撤熔斷**（觸及 `max_drawdown_pct` → 自動 flatten+lock + 發 `risk_breach`）。**越線一律拒單**（誰下令都擋，即使 agent 越權）。
- 封套由 **leader 經 `POST /envelope`** 設定（reason 必填、留存給 User）；未設過則用 config 預設。當前值 `GET /envelope`。

## 下令紀律（重要）

1. **切策略前**先 `curl /status` 看現況——別只信 webhook payload（那是「當時」，決策要看「現在」）。
2. **切策略後**再 `curl /status` 驗證 `strategy` 真的換了；沒換要重送，別假設成功。
3. **服務重啟後**先查 `/status` 對帳再行動（你恢復的記憶可能過期）。
