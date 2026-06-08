# Sunday 操作手冊（`GET /manual`）

Sunday 是一個 Binance USDⓈ-M 永續 **testnet** 交易引擎。它自己偵測訊號、下單、平倉、跑確定性風險熔斷；
你（swarm agent）的工作是**監督**它：查狀態、在 regime 改變時切策略、必要時叫停。
agent 用 `http_request` 工具操作（傳 `{method, url, query?, body?}`；**GET/HEAD 自動放行、lever POST 自動跳審批**）。
base = `http://127.0.0.1:7777`。下面的 `curl` 範例僅作 API wire 參考——agent 把它對應成 `http_request` 的欄位即可。

> 單一標的 `BTCUSDT`、1h K 線、策略 `momentum` / `mean_reversion` / `flat`。
> **人與 agent 讀同一份手冊**——它永遠跟引擎版本一致。

---

## 唯讀（auto-allow，不需審批）

```bash
# 整體狀態：當值策略 + 理由 + 倉位 + 曝險 + as_of_ts + last_lever + 各策略投票摘要
curl -s http://127.0.0.1:7777/status | jq

# ★ 決策面板：每個候選策略「此刻」的投票 + 指標 + regime 讀數（derived，別自己算）
curl -s 'http://127.0.0.1:7777/signals?symbol=BTCUSDT' | jq '.regime, .votes'

# ★ 切換結果歸因：每次切策略後賺賠多少（PnL / 筆數 / 勝率 / 報酬率）
curl -s 'http://127.0.0.1:7777/strategy/outcomes?symbol=BTCUSDT' | jq '.episodes[-3:]'

# 行情 / 倉位 / 損益
curl -s 'http://127.0.0.1:7777/market?symbol=BTCUSDT&tf=1h&limit=100' | jq '.ohlcv[-3:]'
curl -s http://127.0.0.1:7777/positions | jq
curl -s 'http://127.0.0.1:7777/pnl?since=2026-06-01' | jq '{unrealized, equity}'
```

`/status` 的關鍵欄位：`as_of_ts`（這份快照的時間）、`last_lever`（最近一次拉桿：誰/什麼/何時 →
判斷你的視圖是否過期）、`strategy_rationale`（當值策略為何當值）、`votes`（各策略一行投票摘要）。

---

## Lever（POST；需 permission 審批；僅 leader）

### 切換策略 — 防禦式三步：重抓 → 帶 `expected_current` → 從回應驗證

```bash
cur=$(curl -s http://127.0.0.1:7777/status | jq -r '.strategy')   # 1) 重抓現況
curl -sX POST http://127.0.0.1:7777/strategy \
  -H 'Content-Type: application/json' \
  -d "{\"symbol\":\"BTCUSDT\",\"strategy\":\"mean_reversion\",\"reason\":\"analyst 判轉震盪\",\"expected_current\":\"$cur\"}" \
  | jq '.resulting_status.strategy'                               # 3) 從回應驗證（免再 curl）
```

- **`reason` 必填**——留存給 User（決策理由）。漏了會回 `400 reason_required`。
- 回應 `200 {ok, applied, resulting_status}`：`resulting_status.strategy` 就是切換後狀態，**不必再 curl 一次**。
- 若回 `409 {error:"stale", current_status}`：你的視圖過期（引擎/別人已改）。讀 `current_status` 重新判斷再送。
- 設成跟當前相同策略 = `200 applied:false`（**idempotent，無害**）。
- 策略值：`momentum`（順勢）/ `mean_reversion`（逆勢震盪）/ `flat`（空手；會立即平倉）。

### 叫停

```bash
# mode=flat 全平 + 停；mode=safe 凍新倉（既有倉留交易所 stop）
curl -sX POST http://127.0.0.1:7777/halt -H 'Content-Type: application/json' \
  -d '{"reason":"risk_breach 後人工複核","mode":"safe"}' | jq '.resulting_status.mode'
```

## liveness（leader 的 dead-man ping；timer 每 30m 做）

```bash
curl -sX POST http://127.0.0.1:7777/heartbeat -d '{}' | jq '.watchdog_reset_at'
```

Sunday 連續 90m 收不到 heartbeat → 自動進 safe-mode（凍新倉，既有倉留 stop）。**別漏心跳。**

---

## Sunday 會主動寄信給你（webhook → leader 信箱）

事件**自給自足**：`data` 帶 `status`（當下快照）、`rationale`（觸發指標）、`suggested_action`（建議下一步）。
被喚醒時**首輪不必馬上再查**（payload 已自給自足），但下 lever 前仍要照「下令紀律」重抓 `/status`。

| 事件 | 何時 |
| --- | --- |
| `regime_shift` | 盤性改變（trending/ranging/volatile 切換） |
| `risk_breach` | 回撤逼近/越界（確定性熔斷可能已動作，仍須複盤） |
| `engine_degraded` | Sunday 出錯/交易所斷線 → 需注意或 `POST /restart` |
| `safe_mode_entered` | heartbeat 逾時，已進 safe-mode |

## 策略（Gate-1 故意簡單）

- `momentum`：EMA20 × EMA50（1h）。EMA20>EMA50 → 偏多、< → 偏空。
- `mean_reversion`：布林帶 z + RSI14。超賣（z≤-1 且 RSI≤35）→ 偏多；超買（z≥1 且 RSI≥65）→ 偏空。
- `flat`：空手（既有倉平掉）。
- regime 讀數：ADX≥25 → trending（宜 momentum）；ADX<25 → ranging（宜 mean_reversion）；高波動 → volatile（宜 flat）。

## 風險封套（確定性、Python 層硬擋；agent 不能改）

單筆 ≤ $2000、總曝險 ≤ $4000、槓桿 ≤ 3x、回撤 5% 熔斷、進場必掛 2% stop。**越線一律拒單（誰下令都擋）。**

## 下令紀律（重要）

1. **切策略前**先 `curl /status`（或 `/signals`）看現況——別只信 webhook payload（那是「當時」）。
2. **切策略後**從回應的 `resulting_status` 驗證；`409 stale` 就重抓重送，別假設成功。
3. **服務重啟後**先查 `/status` 對帳再行動（你恢復的記憶可能過期）。
