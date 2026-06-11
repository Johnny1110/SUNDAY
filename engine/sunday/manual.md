# Sunday — Agent Manual (`GET /manual`)

Sunday 是一個**為 agent 設計的 Binance USDⓈ-M 永續交易所代理**。你（agent）用通用 HTTP 工具
（`http_request` 或 `curl`）操作它，就像人類用幣安一樣：查市場、看 K 線/指標/資金費、下永續單
（槓桿 / 逐倉全倉 / 止盈止損）、查倉位損益與訂單、看外部總經指數、設定價格與倉位提醒。

- **行情資料 = 主網（mainnet，真實價格，免金鑰）；下單交易 = 測試網（testnet，假錢，安全）。**
- **所有 API 免 token。** Sunday 自己持有幣安金鑰，你只需要 HTTP。
- base = `http://127.0.0.1:7777`。回傳大量資料的 list 端點一律**分頁**。

## 分頁慣例（所有 list）

`?page=1&page_size=50` → `{ items, page, page_size, total, has_more }`。歷史類（orders/trades/funding）
另接受 `?start=<ms>&limit=`；`limit` 上限 **1000**（klines 為 **1500**），超過會**靜默截斷**到上限、不報錯。

## 0 · 可下單市場 `/api/markets`

```bash
# 分頁 + 篩選 + 排序（sort=volume|change|symbol|last, order=desc|asc）
curl -s "http://127.0.0.1:7777/api/markets?sort=volume&order=desc&page=1&page_size=20"
curl -s "http://127.0.0.1:7777/api/markets?symbol=BTC"          # 子字串篩選
curl -s "http://127.0.0.1:7777/api/markets/BTCUSDT"             # 單一市場：ticker + 精度/限額/最大槓桿
```

## 2 · K 線 + 技術指標 + 資金費

```bash
# OHLCV，interval 切換時間框（1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M）
curl -s "http://127.0.0.1:7777/api/klines?symbol=BTCUSDT&interval=1h&limit=200"
# 技術指標（set=rsi,ema,sma,macd,bollinger,adx,atr）。K 線短暫快取（隨 interval 調整、
# 最長 5 分鐘）；上游故障時回最後一次成功值並帶 `stale: true` + `stale_age_s`——拿到 stale
# 表示行情源暫時不穩，數值仍可用於判讀，但別當即時價；`as_of` 永遠是資料的真實 bar 時間。
curl -s "http://127.0.0.1:7777/api/klines/indicators?symbol=BTCUSDT&interval=1h&set=rsi,macd,adx"
# 資金費（現值 + mark/index + 下次結算）/ 歷史（分頁）
curl -s "http://127.0.0.1:7777/api/funding?symbol=BTCUSDT"
curl -s "http://127.0.0.1:7777/api/funding/history?symbol=BTCUSDT&page=1"
```

## 1 · 永續下單 `/api/perp`（測試網）

```bash
# 市價買進 / 開多，用 USD 名目金額、5× 槓桿、逐倉，附止盈止損（reduce-only trigger legs）
# memo = 你下這一單的理由（≤300 字），會記入帳本並在 /api/account/positions 回顯給 User。
curl -sX POST http://127.0.0.1:7777/api/perp/order -H 'Content-Type: application/json' -d '{
  "symbol":"BTCUSDT","side":"buy","type":"market","notional_usd":200,
  "leverage":5,"margin_mode":"isolated","take_profit":75000,"stop_loss":60000,
  "memo":"4h 突破壓力 + 資金費轉負，順勢做多" }'

# 限價單（用 qty 指定張數）：
#   {"symbol":"ETHUSDT","side":"sell","type":"limit","qty":0.1,"price":4200}
curl -sX POST http://127.0.0.1:7777/api/perp/leverage     -d '{"symbol":"BTCUSDT","leverage":10}'
curl -sX POST http://127.0.0.1:7777/api/perp/margin-mode  -d '{"symbol":"BTCUSDT","mode":"cross"}'
curl -sX POST http://127.0.0.1:7777/api/perp/close         -d '{"symbol":"BTCUSDT"}'   # reduce-only 平倉
curl -sX DELETE "http://127.0.0.1:7777/api/perp/order/<id>?symbol=BTCUSDT"
curl -sX DELETE "http://127.0.0.1:7777/api/perp/orders?symbol=BTCUSDT"                  # 撤該標所有掛單（含 TP/SL 腿）
```

下單參數：`side` buy|sell · `type` market|limit · 大小用 `qty`（張數）或 `notional_usd`（USD，自動換算）·
`leverage` / `margin_mode`(isolated 逐倉 | cross 全倉) 在進場前套用 · `take_profit`/`stop_loss` 為觸發價 ·
`memo`（≤300 字）= 下單理由，記入帳本、在倉位查詢的 `memo` / `order` 欄回顯給 User。

TP/SL 觸發腿是幣安的**條件單（algo）**，與一般掛單分屬兩本訂單簿：回應與訂單列表會帶 `algo: true`
（其 id 為 algoId）。你不用管哪本——`orders/open` 兩本合併回傳，`DELETE /api/perp/order/<id>` 兩種 id
都能撤。

### 1a · 既有倉位的 TP/SL 管理 `/api/perp/protection`

保護腿脫落（部分平倉、調倉、誤刪）時**補掛/改掛，不必重開倉**；也用來巡檢單一標的保護狀態：

```bash
# 查保護腿：主 TP/SL 腿（id / trigger_price / status）+ 階梯數 + SL 數量蓋不蓋得住倉位
curl -s "http://127.0.0.1:7777/api/perp/protection?symbol=BTCUSDT"
# → { "symbol":"BTCUSDT", "position":{"side":"long","qty":0.02,...} | null,
#     "take_profit":{...}|null, "stop_loss":{...}|null, "tp_legs":1, "sl_legs":1, "sl_qty_covers":true }
#   position 為 null 卻列得出腿 = 孤兒腿，撤掉它。

# 為現有倉位補/改 TP/SL（null = 該腿不動）；新腿按目前倉量先掛上、舊同類腿才撤——換腿過程不裸奔
curl -sX POST http://127.0.0.1:7777/api/perp/protection -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","take_profit":75000,"stop_loss":60000}'
# → { "ok":true, "take_profit":{"id":"...","trigger_price":75000,...},
#     "stop_loss":{...}, "replaced":["<舊腿id>", ...] }
```

無倉位時 POST 回 404（先用 `/api/perp/order` 開倉）；觸發價在錯誤一側（會立即觸發）回 400 並說明方向。

## 3 · 帳戶：倉位 / 損益 / 訂單 `/api/account`（測試網）

```bash
curl -s http://127.0.0.1:7777/api/account/positions        # 開倉 + 每倉 ROI%（含 protection / liq_distance_pct）
curl -s http://127.0.0.1:7777/api/account/balance          # equity / free / used
curl -s http://127.0.0.1:7777/api/account/pnl              # equity + 總未實現 + 曝險聚合 + 每倉拆解
curl -s http://127.0.0.1:7777/api/account/drawdown         # 權益 vs 高水位（回撤 %，引擎自動快照）
curl -s "http://127.0.0.1:7777/api/account/orders/open?page=1"          # 掛單（可加 &symbol=；含未觸發 TP/SL 腿）
curl -s "http://127.0.0.1:7777/api/account/orders?symbol=BTCUSDT"       # 歷史訂單（需 symbol，分頁；含條件單歷史）
curl -s "http://127.0.0.1:7777/api/account/trades?symbol=BTCUSDT"       # 成交（含 realized PnL，分頁）
```

風控視角欄位（給巡檢用，引擎算好、不用自己 join / 心算）：

- 每倉 `protection`：`{take_profit, stop_loss, sl_qty_covers}`——TP/SL 觸發單還掛著嗎、SL 數量
  蓋不蓋得住倉位（`false` = 裸倉或半裸倉）；讀不到掛單時為 `null`（未知，不是沒有）。
- 每倉 `liq_distance_pct`：現價離清算價的距離 %（cross 倉無逐倉清算價 → `null`）。
- `/pnl` 的 `total_notional` / `exposure_pct`：全帳戶名目曝險與占權益比。
- `/drawdown`：`{equity, high_water, high_water_ts, drawdown_pct, samples}`——引擎每
  `EQUITY_SNAP_SEC`（預設 300s）自動快照權益，`samples` 是已累積的快照數（剛開機時少、參考性低）。

## 4 · 外部指數 `/api/indices`

```bash
curl -s http://127.0.0.1:7777/api/indices                  # 全部快照
curl -s http://127.0.0.1:7777/api/indices/fear-greed       # 加密恐懼貪婪
# 其他 key：btc-dominance | vix | dxy | spx | ndx | us10y | gold
```

加密情緒（Fear&Greed、BTC 主導率）+ 總經（VIX 波動率、DXY 美元、標普 / 那斯達克、美十年期殖利率、黃金）。
皆有 TTL 快取，feed 失效時回傳最後值（`stale: true`）而非報錯。

## 6 · 價格提醒 `/api/alerts`

```bash
# kind: price_above / price_below（threshold=價格）；pct_move（threshold=百分比，建立時鎖定參考價）
curl -sX POST http://127.0.0.1:7777/api/alerts -d '{"symbol":"BTCUSDT","kind":"price_above","threshold":75000,"note":"突破"}'
curl -s  "http://127.0.0.1:7777/api/alerts?status=active&page=1"
curl -sX DELETE http://127.0.0.1:7777/api/alerts/<id>
```

提醒**觸發一次**即送 webhook 給 evva swarm，並標記 `triggered`。

## 5 · 倉位監控 `/api/monitor`

```bash
curl -s http://127.0.0.1:7777/api/monitor                  # 目前監控中的倉位 + ROI% + step + 設定
curl -sX POST http://127.0.0.1:7777/api/monitor/config -d '{"step_pct":5,"enabled":true}'
```

Sunday 自動監控**每一個**開倉倉位（websocket 即時報價 + 輪詢備援），當某倉位的 ROI%
每跨越一個 `step_pct`（預設 5%）就送一次 webhook 給 swarm。**無需手動訂閱。**

## 工作日誌 `/api/journal`（reviewer 寫日報 → User 在 UI 看）

```bash
# 寫一篇日誌（body 為 markdown；date 省略則預設今天 UTC；author 預設 reviewer）
curl -sX POST http://127.0.0.1:7777/api/journal -H 'Content-Type: application/json' -d '{
  "author":"reviewer","date":"2026-06-09","title":"2026-06-09 當日復盤",
  "body":"## 當日操作\n- ...\n\n## 盈虧歸因\n- ...\n\n## 改進建議\n- ..." }'
curl -s  "http://127.0.0.1:7777/api/journal?page=1"        # 列出（新到舊，分頁；可加 &author=reviewer）
curl -s  "http://127.0.0.1:7777/api/journal/12"            # 單篇全文
```

reviewer 每日把復盤 POST 到這裡存進 DB，User 在 dashboard 的 **Journal** 分頁讀；`body` 用 markdown、
不限長度；`title` 最多 **200 字**（超過會被拒收 422）。

## 公告板 `/api/memory`（發布給全隊與 User 的兩份文件）

每個 agent 的**私人工作記憶**現在原生存在 evva（各自的 `agents/…/<name>/memory/` 目錄，
醒來自動帶索引）；這裡只放**刻意發布**的跨 agent 合約，User 在 dashboard **Memory** 分頁讀：

- `friday` —— **團隊憲法**：風控共識、watchlist、持倉理由、standing rules。trader 下單前
  pre-flight 對照、risk-monitor 巡檢對照、analyst 對齊 watchlist。
- `researcher` —— **研究日誌**：標日期的線索與 idea，friday 與 User 回看。

```bash
curl -s  http://127.0.0.1:7777/api/memory/friday                 # 讀全文（無內容回空文件，不報錯）
curl -sX PUT http://127.0.0.1:7777/api/memory/friday \
     -H 'Content-Type: application/json' -d '{"content":"# friday 憲法\n## 風控共識\n- ..."}'  # 整份覆寫
curl -s  http://127.0.0.1:7777/api/memory                        # 索引：每份文件的 updated_at + size
```

文件是**覆寫式**：`GET` 讀回 → 就地增刪 → `PUT` 整份寫回（保持精簡，過期的刪掉）。
讀對所有 agent 開放；只有 `friday` `researcher` 兩個名字可寫（其他名字 404）。

## 通報 `/api/reports`（friday → User 的重要通報）

當發生**重要的事**（大量盈利 / 大量虧損 / 系統錯誤）就 POST 一則通報，User 在 dashboard 的
**Reports** 分頁由近到遠讀。內容用 markdown、**不限字數，表達清楚最重要**。

```bash
curl -sX POST http://127.0.0.1:7777/api/reports -H 'Content-Type: application/json' -d '{
  "kind":"profit", "title":"BTC 多單止盈 +18%",
  "body":"## 發生什麼\n4h 突破後順勢做多，TP 觸發…\n## 影響\n權益 +18%…\n## 下一步\n…" }'
curl -s  "http://127.0.0.1:7777/api/reports?page=1"              # 列出（新到舊，分頁；可加 &kind=）
curl -s  "http://127.0.0.1:7777/api/reports/12"                  # 單則全文
```

`kind`：`profit` | `loss` | `system` | `info`（其他值存為 info，用於 UI 上色）。`title` 必填。
這和 `/api/journal`（reviewer 每日排程復盤）不同——通報是**事件驅動**「你現在該知道」的快訊。

## 系統時間 `/api/system/time`

時間/時區的對時錨點（緣起 PRD-001：只看無時區的牆鐘字串，會把本地時間誤讀成 UTC）。

```bash
curl -s http://127.0.0.1:7777/api/system/time
# {"epoch_ms":1781123100000,"utc":"2026-06-10T04:25:00+00:00","local":"2026-06-10T12:25:00+08:00",
#  "tz":"HKT","utc_offset":"+08:00","binance_clock":{"offset_ms":-3,"synced":true}}
```

慣例：跨系統對時一律用 `epoch_ms`（無時區的絕對時間）；任何**沒帶 offset 的牆鐘字串都是本地時間**
（offset 見 `utc_offset`），需要 UTC 自行換算。`binance_clock` 是 Sunday 簽單用的 Binance↔本地偏移
（ms；`synced=false` 表示尚未對時過，offset 視為 0）。

## Webhook（Sunday → evva swarm）

`position_pnl`（倉位每 5% ROI）與 `price_alert`（提醒觸發）兩種事件，POST 到 `EVVA_WEBHOOK_URL`
（預設 `…/api/swarm/sunday/event`），payload = `{title, body, data, to}`，`data` 內含結構化欄位
＋ `suggested_action`，woken 的 agent 第一回合即可行動。`position_pnl` 喚醒誰由
`MONITOR_WEBHOOK_TO` 決定（預設 `leader` → friday；設 `trader` 直達執行台）；`price_alert`
固定喚醒 leader。

## Dashboard（人類在瀏覽器看）

`http://127.0.0.1:7777/dashboard` —— TS + Vue 3 量化終端：Markets / Chart / Trade / Account /
Indices / Alerts / Reports / Journal / Memory / Manual。人能做的，agent 用上面同一組端點也能做。
