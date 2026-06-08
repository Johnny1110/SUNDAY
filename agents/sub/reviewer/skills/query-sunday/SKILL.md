# query-sunday 唯讀查 Sunday 的歷史與績效（reviewer 復盤用）

Sunday 在 `http://127.0.0.1:7777`。用 **`http_request`** 唯讀查（GET 免審批）。你**不拉任何 lever**（`POST /commentary` 寫 playbook 是唯一例外，無害貼文）。

## 復盤主力端點（GET）

```jsonc
{ "method": "GET", "url": "http://127.0.0.1:7777/theses", "query": { "limit": "50" } }      // ★ thesis 史 + outcome（賺/賠、status、invalidation 觸發了嗎）
{ "method": "GET", "url": "http://127.0.0.1:7777/ablation" }                                // ★ 資訊層 vs no-trade 基準 + info-ON/OFF 拆分（edge 生死線，不變量 11）
{ "method": "GET", "url": "http://127.0.0.1:7777/performance" }                             // per-strategy 歸因（realized_pnl / n_trades / win_rate / avg_pnl）
{ "method": "GET", "url": "http://127.0.0.1:7777/strategy_history" }                        // 每次切換的時間 / 標的 / 策略 / reason
{ "method": "GET", "url": "http://127.0.0.1:7777/pnl", "query": { "since": "2026-06-01" } } // realized / unrealized + 權益曲線
```

## 歸因框架

- 哪類事件 / 敘事 work、哪類不 work？**friday 採納 / 打槍 analyst 的判斷，事後看對不對？**
- `invalidation` 及時觸發了嗎？命中率？平均賺賠？
- **誠實看 `/ablation`**：資訊層真的贏過 buy-hold / funding-carry 基準 + info-OFF 標的嗎？沒贏就說沒贏——別把運氣當 edge。

## 寫 playbook + 交 friday

- `POST /commentary`（`author:"reviewer"`）：把可複用教訓留給 User + 下一輪參考。
- `send_message` friday：當期表現 + **1–2 條具體**改進建議（哪類 setup 該加碼 / 該避開），不要只複述績效數字。細節 `GET /manual`。
```

