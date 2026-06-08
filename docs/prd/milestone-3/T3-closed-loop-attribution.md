# T3 — 閉迴路：切換結果歸因（`GET /strategy/outcomes`）

> milestone-3 任務 **3/6** ｜ 契約見 [`README.md`](README.md) §2 ｜ **依賴**：M1.0-T3 帳本有資料

## 做什麼

把監督從**開迴路**（拉桿、祈禱）變**閉迴路**（學會哪種 regime 下哪種切換有用）。**這是上層 §2.1「alpha 不在單一策略、而在 agent 的切換政策」的資料地基。** 做法 = **lens over 既有 modeling-grade 帳本，零/極少新 capture**（M3-D3）。

## 交付

- **`GET /strategy/outcomes?symbol=&since=`（新；唯讀）**：把每筆 `strategy_state`（一次切換）連到其生效窗 `[set_at, 下次同標的切換)` 內、該標的、該策略的已實現結果：

```jsonc
{ "episodes": [
  { "symbol": "BTCUSDT", "strategy": "mean_reversion", "set_by": "friday",
    "set_at": "2026-06-08T14:30:00Z", "ended_at": "2026-06-08T18:00:00Z",
    "reason": "analyst 判轉震盪",
    "realized_pnl": 124.5, "trades": 3, "win_rate": 0.67, "return_pct": 0.41 } ] }
```

  - 純查詢：`strategy_state` join `positions`/`fills`（皆已 tag `strategy` + 時間戳，見 `0001_init.sql`）。**先確認 0001 schema 夠用**；若 episode 邊界查詢需要，補一支 `migrations/0002_*.sql`（**僅索引 / view，非新事實表**——守 M3-D3）。
- **消費端**：reviewer 的 skill recipe + 每日復盤範本引用它；leader 切策略前可參考「上次這樣切的結果」。

## Done

- `curl -s :7777/strategy/outcomes` 回每次切換的結果歸因（PnL / 筆數 / 勝率 / 報酬率）。
- **A4**（README §4）：reviewer 能對「某次切換」給出 +X% / N 筆 / 勝率。

## 不在本任務

- ML / 切換政策最佳化、回測（Gate-2）。
- dashboard 視覺化（Gate-2 / milestone-2）。
