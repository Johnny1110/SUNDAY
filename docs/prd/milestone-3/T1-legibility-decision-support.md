# T1 — Legibility 決策支援（`GET /signals` + `GET /status` 增強）

> milestone-3 任務 **1/6** ｜ 契約見 [`README.md`](README.md) §2 ｜ **依賴**：契約可先定稿；引擎 impl 依 M1.0-T3 `strategy.py`

## 做什麼

打開 agent 的眼睛。Sunday 為了自己交易，內部本來就算了 EMA / regime / 訊號——**把這些 derived 的東西吐給 agent**，讓它能做「該不該切策略」的 meta 決策，而不必自己 `curl→python` 重算 raw OHLCV（2026-06-08 agent walkthrough 撞到的 #1 痛點）。

## 交付

- **`GET /signals`（新；唯讀 auto-allow）**：對每個標的，回每個候選策略**此刻**的投票 + 計算用指標 + regime 讀數。**derived，不是 raw K 線**——agent 讀完即可決策、零再計算：

```jsonc
{
  "as_of_ts": "2026-06-08T11:00:00Z",
  "symbol": "BTCUSDT",
  "regime":  { "label": "trending", "adx": 27.3, "vol_pct": 1.8 },
  "active":  "momentum",
  "votes": [
    { "strategy": "momentum", "vote": "long", "confidence": 0.62,
      "indicators": { "ema20": 62418.1, "ema50": 61877.2, "spread_pct": 0.87 },
      "rationale": "EMA20>EMA50，spread +0.87% 偏多" },
    { "strategy": "mean_reversion", "vote": "neutral", "confidence": 0.20,
      "indicators": { "rsi14": 52.0, "bb_z": 0.3 },
      "rationale": "RSI 52 中性、未觸帶邊" }
  ]
}
```

  - 實作 = 每個策略一個「**會投票但不下單**」的 `evaluate()`（`strategy.py` 既有訊號邏輯的純函式版，與 `signals` audit 表共用同一份指標計算，DRY）。
- **`GET /status` 增強**：加 `as_of_ts`、`last_lever {by, what, at}`（與防呆 T4 共用）、各策略 vote 一行摘要（`/signals` 的精簡版，讓 leader 一眼掃完）。
- **`/manual` 補這兩段**——人與 agent 讀同一份、永遠跟版本一致。

## Done

- `curl -s ':7777/signals?symbol=BTCUSDT'` 回上面形狀；agent transcript 能**只憑它**說出「momentum 偏多、建議維持」。
- `curl -s :7777/status` 帶 `as_of_ts` + `last_lever` + votes 摘要。
- **A1**（README §4）：demo run 中 agent **不再** `curl→python` 自算指標。

## 不在本任務

- 引擎策略/帳本本體（M1.0-T2/T3）；切換結果歸因（T3）；POST 防呆語意（T4）；webhook payload（T5）。
