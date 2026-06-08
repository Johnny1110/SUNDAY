# query-sunday 用 bash+curl 讀 Sunday 交易引擎（唯讀 + 推 commentary，給 friday 建議）

Sunday 在 `http://127.0.0.1:7777`。你**只讀、不下單**（lever 是 friday 的事）。完整 API：`curl -s http://127.0.0.1:7777/manual`。

## 讀（免審批）

```bash
curl -s http://127.0.0.1:7777/status                                     # 當值策略 + 理由 + 倉位
curl -s "http://127.0.0.1:7777/market?symbol=BTCUSDT&tf=1h&limit=100"     # OHLCV
curl -s http://127.0.0.1:7777/positions ; curl -s http://127.0.0.1:7777/pnl   # 倉位 / 損益 + 權益曲線
curl -s http://127.0.0.1:7777/performance                                # per-strategy 績效歸因
```

## 推市場動態給 User（commentary；無害寫入、免審批、非交易 lever）

評估完 regime 後，除了 `send_message` 給 friday，把**給 User 看的市場脈絡**貼到 commentary feed（顯示在 `:7777/dashboard`）：

```bash
curl -sX POST http://127.0.0.1:7777/commentary -H 'Content-Type: application/json' \
  -d '{"author":"analyst","title":"<一句摘要>","body":"<當前市場動態：regime / 波動 / 風險>"}'
```

判斷方向後，用 `send_message` 把「**方向 + 建議策略（momentum / flat）+ 理由**」回報給 **friday**（只有 friday 能拉 lever）。
