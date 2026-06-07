# T2 — USDⓈ-M testnet adapter（行情 / 下單 / 平倉 / stop）

> 1.0 任務 **2/6** ｜ 共用契約見 [`README.md`](README.md) ｜ **依賴：T1**

## 做什麼
接 Binance USDⓈ-M **testnet**，把「讀行情 + 下單 + 平倉 + 掛 stop」這層做出來，並開 3 個唯讀端點。此任務**不含策略決策**（那是 T3）——只提供 T3 會呼叫的執行原語。

## 交付
- `engine/sunday/exchange.py`（ccxt，`binanceusdm` + testnet）：
  - `fetch_ohlcv(symbol, tf, limit)`、`fetch_ticker` / `fetch_positions` / `fetch_balance`。
  - `place_market(symbol, side, qty)`、`close_position(symbol)`、`set_stop(symbol, side, qty, stop_price)`。
  - 設定 testnet endpoint + key（從 config）；設 `BTCUSDT` 槓桿。
- 在 `app.py` 接：`GET /market`、`GET /positions`、`GET /pnl`（讀交易所 + redis cache；落 `ohlcv`/`positions`/`pnl_snapshots` 的部分可先簡單）。

## 端點
- `GET /market?symbol&tf&limit` → OHLCV。
- `GET /positions` → 當前 testnet 持倉（`strategy`/`entry_reason` 欄位 T3 才填，先留空/null）。
- `GET /pnl?since` → realized/unrealized + equity 點。

## Done
- 能用 adapter **手動**在 testnet 開一筆 + 掛 stop + 平掉。
- `/market`、`/positions`、`/pnl` 回真實 testnet 資料。

## 不在本任務
- 何時下單 / 多空判斷（T3 策略）、風控熔斷（T3）、regime/notify（T4）。
