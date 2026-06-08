# Sunday 操作手冊（`GET /manual`）

Sunday 是一個 Binance USDⓈ-M 永續 **testnet** 交易引擎。它自己偵測訊號、下單、平倉、跑確定性風險熔斷；
你（swarm agent）的工作是**監督**它：查狀態、在 regime 改變時切策略、必要時叫停。
用通用 `bash` + `curl` 操作。base = `http://127.0.0.1:7777`。

> **策略**：`momentum` / `mean_reversion` / `flat`（單一標的 `BTCUSDT`、1h）。切策略前先查決策支援面板 `GET /advisor`。
> **milestone 2.0（dashboard）**：`/pnl` 回真實 `equity_curve`、新增 `/performance`·`/strategy_history`·`/commentary`，並自服一頁 `/dashboard`。

## 唯讀（auto-allow，不需審批）

```bash
# ★ 決策支援面板：每策略此刻的投票 + 指標 + regime + funding（永續資金費）+ 建議策略
#   （切策略前先看這個，別自己算 EMA/RSI）
curl -s "http://127.0.0.1:7777/advisor?symbol=BTCUSDT"

# 整體狀態（含當值策略 + 理由 + 倉位 + 曝險）
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
```

## 市場動態 commentary（analyst 專用；無害寫入、auto-allow、非交易 lever）

```bash
# analyst 把當前市場脈絡推給 User（顯示在 dashboard feed）
curl -sX POST http://127.0.0.1:7777/commentary \
  -H 'Content-Type: application/json' \
  -d '{"author":"analyst","title":"BTC regime","body":"波動率下降，盤整偏多"}'
```

## Dashboard（User 在瀏覽器看；Sunday 自服）

`http://127.0.0.1:7777/dashboard` —— 權益曲線 + 切換理由疊圖、30 日 PnL、倉位、per-strategy 歸因、commentary feed，自動刷新。

## Lever（POST；需 permission 審批；僅 leader）

```bash
# 切換當值策略（reason 必填，會留存給 User）
curl -sX POST http://127.0.0.1:7777/strategy \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","strategy":"momentum","reason":"analyst 判趨勢偏多"}'

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
- **不確定切哪個？查 `GET /advisor`** —— 每策略投票 + regime（trending/ranging/volatile）+ funding + 建議策略，照它決定。

## 風險封套（確定性、Python 層硬擋）

- 硬限額：單筆上限 / 總曝險上限 / 最大槓桿 / 進場必掛 stop / **最大回撤熔斷**（觸及 `max_drawdown_pct` → 自動 flatten+lock + 發 `risk_breach`）。**越線一律拒單**（誰下令都擋，即使 agent 越權）。
- 封套由 **leader 經 `POST /envelope`** 設定（reason 必填、留存給 User）；未設過則用 config 預設。當前值 `GET /envelope`。

## 下令紀律（重要）

1. **切策略前**先 `curl /status` 看現況——別只信 webhook payload（那是「當時」，決策要看「現在」）。
2. **切策略後**再 `curl /status` 驗證 `strategy` 真的換了；沒換要重送，別假設成功。
3. **服務重啟後**先查 `/status` 對帳再行動（你恢復的記憶可能過期）。
