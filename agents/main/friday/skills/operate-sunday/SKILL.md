# operate-sunday 用 bash+curl 操作 Sunday 交易引擎（讀狀態 + 行使 lever）

Sunday 是交易引擎，在 `http://127.0.0.1:7777`。完整 API：`curl -s http://127.0.0.1:7777/manual`。

## 讀（免審批）

```bash
curl -s http://127.0.0.1:7777/status                                          # 當值策略 + 理由 + 倉位 + 曝險
curl -s "http://127.0.0.1:7777/market?symbol=BTCUSDT&tf=1h&limit=100"          # OHLCV
curl -s http://127.0.0.1:7777/positions ; curl -s http://127.0.0.1:7777/pnl    # 倉位 / 損益 + 權益曲線
curl -s http://127.0.0.1:7777/performance                                      # per-strategy 績效歸因（哪個策略在賺/賠）
```

> User 在 `http://127.0.0.1:7777/dashboard` 看權益曲線 / 倉位 / 歸因 / 你的切換理由 / analyst commentary。

## Lever（會跳審批；只有你能用）

```bash
# 切換策略（reason 必填）。strategy ∈ momentum | flat（mean_reversion 1.1 才有）
# ⚠ reason 會直接顯示在 User dashboard 的切換時間軸上——寫成人看得懂的決策理由，別用內部代號。
curl -sX POST http://127.0.0.1:7777/strategy -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","strategy":"momentum","reason":"<為什麼切：regime 讀數 + 依據>"}'

# 叫停：flat=全平、safe=凍新倉
curl -sX POST http://127.0.0.1:7777/halt -H 'Content-Type: application/json' \
  -d '{"reason":"<為什麼>","mode":"flat"}'

# 心跳（dead-man）
curl -sX POST http://127.0.0.1:7777/heartbeat -d '{}'
```

## 下令紀律（重要）

1. 切策略**前**先 `curl /status` 看現況——別只信 webhook payload（那是「當時」，決策看「現在」）。
2. 切策略**後**再 `curl /status` 確認 `strategy` 真的換了；沒換要重送，別假設成功。
3. 服務重啟後先查 `/status` 對帳再行動。
