# query-sunday 用 bash+curl 讀 Sunday 交易引擎（唯讀，給 friday 建議）

Sunday 在 `http://127.0.0.1:7777`。你**只讀、不下單**（lever 是 friday 的事）。完整 API：`curl -s http://127.0.0.1:7777/manual`。

## 讀（免審批）

```bash
curl -s http://127.0.0.1:7777/status                                     # 當值策略 + 理由 + 倉位
curl -s "http://127.0.0.1:7777/market?symbol=BTCUSDT&tf=1h&limit=100"     # OHLCV
curl -s http://127.0.0.1:7777/positions ; curl -s http://127.0.0.1:7777/pnl
```

判斷方向後，用 `send_message` 把「**方向 + 建議策略（momentum / flat）+ 理由**」回報給 **friday**。
