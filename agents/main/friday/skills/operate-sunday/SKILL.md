# operate-sunday 操作 Sunday 交易引擎：查狀態、切策略、叫停、心跳（leader 專用）

Sunday 是我們的交易引擎（Binance USDⓈ-M 永續 **testnet**），跑在 `http://127.0.0.1:7777`。
**它自己交易，你監督它。** 你用通用 `bash`+`curl` 操作；完整 API 隨時 `curl -s :7777/manual`，
下面是你最常用的可複製 recipe。**你是唯一能拉 lever（切策略 / 設封套 / halt）的成員。**

> 端點可用性（隨引擎進度開通；未開通的先用 fallback）：
> `/status`·`/market`·`/positions`·`/pnl`·`/strategy`·`/halt`·`/heartbeat` = milestone-1.0；
> `/signals`·`/status` 增強 = M3-T1；`/strategy/outcomes` = M3-T3；POST 回 state + `expected_current` = M3-T4。
> `/signals` 還沒開通時，fallback：`curl /market` 取 OHLCV 自行判讀（盡量避免——這正是 M3 要消除的體力活）。

---

## 監督節奏（每次被喚醒都照這個走）

1. **重抓現況** — 別只信喚醒你的 webhook payload（那是「當時」，你要看「現在」）。先 `curl /status`（+ 需要時 `/signals`）。
2. **判斷** — regime 真的變了嗎？值得切策略嗎？平靜無事就**回報並 stand down**，別硬找事做。
3. **行動** — 要切策略/叫停才拉 lever（見下，**附 `reason`**）。
4. **驗證** — 從 lever 的**回應本身**確認狀態真的變了；沒變要重送（見「下令紀律」）。
5. **stand down** — 做完就結束這一輪，省 token。

---

## 唯讀（auto-allow，不跳審批）

```bash
# 整體狀態：當值策略 + 理由 + 倉位 + 曝險 + as_of_ts + last_lever
curl -s http://127.0.0.1:7777/status | jq

# 決策面板（M3-T1）：每個候選策略此刻的投票 + 指標 + regime 讀數——用這個決定要不要切，別自己算
curl -s 'http://127.0.0.1:7777/signals?symbol=BTCUSDT' | jq '.regime, .votes'

# 某次切換的結果（M3-T3）：上次這樣切賺賠多少，幫你決定這次要不要重複
curl -s 'http://127.0.0.1:7777/strategy/outcomes?symbol=BTCUSDT' | jq '.episodes[-3:]'

# 倉位 / 損益
curl -s http://127.0.0.1:7777/positions | jq
curl -s 'http://127.0.0.1:7777/pnl?since=2026-06-01' | jq '{realized,unrealized,equity:.equity_curve[-1]}'
```

---

## Lever：切換策略（**僅你**；POST 會跳 permission 審批）

**標準防禦式 recipe**——重抓 → 帶 `expected_current` 下令 → 從回應驗證：

```bash
# 1) 重抓當值策略（決策要看現在）
cur=$(curl -s http://127.0.0.1:7777/status | jq -r '.strategy')

# 2) 下令，帶 expected_current（防呆：若狀態已被別人/引擎改掉，引擎回 409 不誤套）
resp=$(curl -sX POST http://127.0.0.1:7777/strategy \
  -H 'Content-Type: application/json' \
  -d "{\"symbol\":\"BTCUSDT\",\"strategy\":\"mean_reversion\",\"reason\":\"analyst 判轉震盪，ADX 跌破 20\",\"expected_current\":\"$cur\"}")

# 3) 驗證：從回應本身確認，不必再 curl 一次
echo "$resp" | jq '.resulting_status.strategy'   # 應為 "mean_reversion"
```

- **`reason` 必填**——它會留存給 User（「14:30 切 mean_reversion，因為…」），也是監督迴路的書面證據。
- **若回 `409 {error:"stale", current_status}`**：你的視圖過期了。讀回應裡的 `current_status`，重新判斷後再送一次（別盲目重送同一個）。
- 策略值：`momentum`（順勢）/ `mean_reversion`（逆勢震盪）/ `flat`（空手）。

## Lever：叫停（緊急）

```bash
# mode=flat 全平、mode=safe 凍新倉（既有倉留交易所 stop）
curl -sX POST http://127.0.0.1:7777/halt \
  -H 'Content-Type: application/json' \
  -d '{"reason":"risk_breach 後人工複核，先凍倉","mode":"safe"}' | jq '.resulting_status.mode'
```

## 心跳（你的 dead-man ping；timer 每 30m 會叫你做）

```bash
curl -sX POST http://127.0.0.1:7777/heartbeat -d '{}' | jq '.watchdog_reset_at'
```

> Sunday 連續 ~90m 收不到 heartbeat → 自動進 safe-mode（凍新倉）。所以**別漏心跳**——漏了等於告訴引擎「監督端腦死」。

---

## 下令紀律（§7.10，違反會誤動作）

1. **切策略前先重抓 `/status`**——webhook payload 是「當時」，決策要看「現在」。
2. **切策略後從回應驗證**——確認 `resulting_status` 真的換了；沒換（或 409 stale）要重判重送，**別假設成功**（靜默失敗會讓你以為切了其實沒切）。
3. **服務重啟後先全量 re-sync**——你恢復的記憶可能過期。resume 第一件事是 `curl /status`（+`/positions` 對帳）再行動，prompt 也會提醒你。

---

## 邊界（硬規則）

- **你不下單**——下單/平倉是 Sunday 的事。你只拉 meta lever（切策略 / 設封套 / halt·restart）。
- **硬風控擋不過**——即使你下了越線指令，Sunday 的 Python/交易所層仍會拒單。lever 是「方向盤」，不是「油門」。
- **諮詢角色（analyst/risk/reporter/reviewer）不能拉 lever**——他們用 `send_message` 給你建議，**只有你**行使。採納或不採納，**回信告訴他們**（否則他們無法改進）。
- 細節、錯誤碼、封套語意：`curl -s http://127.0.0.1:7777/manual`。
