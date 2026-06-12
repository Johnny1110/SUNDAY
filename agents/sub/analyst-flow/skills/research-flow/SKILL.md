# research-flow 技術面 + 永續微結構 + 世界指數 → 給 friday 方向 + 關鍵價位

**通道（混合制）**：優先用 `mcp__sunday__*` 唯讀工具——`indices {key?}` · `klines {symbol,interval,limit}` · `indicators {symbol,interval,set}` · `funding {symbol,history?}` · `market_get {symbol}` · `markets_list {sort,search?}`（typed 參數、輸出已整形好直接讀）；工具不可用（tool error / server 不在）才用 **`http_request`** 打下方端點（降級通道），並在回報註明。**你只讀、不下單。**

## 世界指數（例行先掃）

MCP：`indices {}`（全部）或 `indices {key:"fear-greed"}`。降級：

```jsonc
{ "method":"GET", "url":"http://127.0.0.1:7777/api/indices" }                 // 全部：F&G / 主導率 / VIX / DXY / SPX / NDX / US10Y / Gold / Brent Oil
{ "method":"GET", "url":"http://127.0.0.1:7777/api/indices/fear-greed" }
```

- risk-on / risk-off 轉變？VIX 飆、DXY 強、美股弱 → 加密通常承壓；F&G 極端貪婪 → 過熱、極端恐懼 → 可能超賣；布蘭特油價急漲 → 通膨與地緣風險升溫，偏 risk-off。

## 標的技術面（friday 指定）

MCP：`indicators {symbol:"BTCUSDT", interval:"4h", set:"rsi,ema,macd,bollinger,adx,atr"}`（直接給最新面板值）· `klines {symbol,interval,limit≤500}` · `funding {symbol}` · `market_get {symbol}`。輸出第一行 `⚠ stale` = 上游卡頓供的 last-good 值，判讀可用但註明。降級：

```jsonc
{ "method":"GET", "url":"http://127.0.0.1:7777/api/klines", "query":{ "symbol":"BTCUSDT", "interval":"1h", "limit":"200" } }
{ "method":"GET", "url":"http://127.0.0.1:7777/api/klines/indicators", "query":{ "symbol":"BTCUSDT", "interval":"4h", "set":"rsi,ema,macd,bollinger,adx,atr" } }
{ "method":"GET", "url":"http://127.0.0.1:7777/api/funding", "query":{ "symbol":"BTCUSDT" } }
{ "method":"GET", "url":"http://127.0.0.1:7777/api/markets/BTCUSDT" }
```

（要超過 500 根的原始 K 線序列走 http_request，上限 1500。）

## 判讀框架

- **趨勢 / 動能**：EMA 排列、MACD、ADX（>25 才算有趨勢）；多時間框對照（1h vs 4h）。
- **超買超賣 + 波動**：RSI、Bollinger；**ATR 決定停損該放多寬**（給 friday 的停損區要參考 ATR）。
- **資金費**：年化 |值| 高 → 該邊付高成本、擁擠；極端常隨清算反轉。問：擁擠在哪一邊？
- **量能 / 漲跌**：`/api/markets` 的 quoteVolume、percentage。

## 回報 friday（send_message）

**方向（偏多 / 偏空 / 觀望）+ 訊號強度 + 關鍵價位（支撐 / 壓力 / 建議停損）+ 失效條件 + 一句理由 + 數據出處。** 給得出「在哪進、在哪停」最有用。細節 `GET /manual`。
