# BUG-01 — `/api/perp/protection` 更新 SL 時觸發意外 market close

## 嚴重程度
🚨 Critical — 導致倉位意外平倉，造成實際資金損失

## 重現步驟

1. 開倉（例如 BTCUSDT long，帶 TP/SL）
2. 調用 `POST /api/perp/protection` 更新 stop_loss 觸發價
3. API 回應 200 OK，顯示新 SL 已建立
4. **同一秒內**產生了 reduce-only market sell order，平倉全部部位

## 實際案例

**時間**：2026-06-12 01:33:12 CST

**操作**：
```json
POST /api/perp/protection
{"symbol":"BTCUSDT","take_profit":64000,"stop_loss":62615}
```

**API 回應**（200 OK）：
```json
{
  "ok": true,
  "stop_loss": {
    "id": "1000000102773934",
    "trigger_price": 62615.0,
    "status": "open"
  },
  "replaced": ["1000000102764267"]
}
```

**實際結果**：
- 新 SL #1000000102773934 建立（未被觸發，mark ~63,400 >> 62,615）
- 但同時產生了 order #14843715560：market sell 0.0318 BTC @62,430，平倉全部部位
- 倉位從 +$25 浮盈變成 -$5.80 已實現虧損

## 根因推測

`/api/perp/protection` 在「取消舊 SL 腿 → 建立新 SL 腿」的流程中：
- 取消舊腿時可能誤走了「market sell」路徑而非「cancel order」
- 或新舊腿替換的競態窗口觸發了 Binance 的 reduce-only close

## 影響範圍

- **所有調用 `/api/perp/protection` 的操作都有風險**
- Standing rule「+10% ROI 時 SL 上移保本」無法安全執行
- 任何事後調整 TP/SL 的操作都可能觸發此 bug

## 關聯事件

- **HYPEUSDT 不明平倉**（2026-06-11 23:54）：也可能是同一 bug。HYPE 倉位在無任何顯式平倉指令下被 market sell @56.555。若當時有人（如 trader 巡檢）調用了 protection API，可能觸發了相同行為。但因 Sunday 無 audit log，無法確認。

## 暫時規避方案

1. **禁止調用 `/api/perp/protection`** 進行任何 TP/SL 修改
2. 開倉時直接在 `/api/perp/order` 中帶入 `take_profit` 和 `stop_loss`
3. 若需調整 TP/SL：先手動平倉 → 再以新參數重新開倉（繞過 protection API）
4. trader 巡檢僅驗證 protection 狀態，不執行修改

## 修復建議

1. 審查 `/api/perp/protection` 的「取消舊腿」邏輯，確保走 cancel order 而非 market sell
2. 加入防禦性檢查：若 position qty > 0，禁止對該 symbol 發起 reduce-only market order（除非是明確的 close 請求）
3. 加入 audit log（PRD-001），記錄每次 API 調用的操作者和結果
4. 修復平倉後 TP/SL 自動清理（PRD-002），減少孤兒單

---

— friday, 2026-06-12 01:36 CST

## ✅ 已修復（2026-06-12，與 BUG-04 同一 root cause）

**根因**：不是「取消舊腿誤走 market sell」——是**新 SL 腿一掛上去就立即觸發**。`place_stop()`
未指定 `workingType`，幣安預設 `CONTRACT_PRICE` = 用**測試網最新成交價**判定觸發；測試網成交價
（薄訂單簿）與 agent 決策依據的主網價格脫鉤。本案例：主網 mark ~63,400，但測試網成交價 ~62,430
< 觸發價 62,615 → 新腿落地即在觸發區。且 Algo Service 遷移後幣安**不再回 -2021 拒單**，而是直接
成交 → reduce-only market sell 全平（即觀察到的 order #14843715560）。

**修復**（`fix/bug-report-sl-trigger` branch）：
1. `exchange.place_stop()` 改掛 `workingType=MARK_PRICE`——測試網 mark 由指數推導、貼近主網真實價，
   觸發行為回到 agent 預期。
2. `/api/perp/order` 與 `/api/perp/protection` 在**任何寫入前**先以測試網 mark 驗證觸發價：已在
   觸發區回 400 並說明方向（`protection.immediate_trigger`，純邏輯、有單元測試），補回 Algo Service
   不再提供的 -2021 防線。protection 的兩個觸發價一起預檢——不會 TP 先掛上、SL 才報錯的半套狀態。

迴歸測試：`tests/test_tpsl_safety.py`、`tests/test_protection.py::TestImmediateTrigger`。
audit log 見 BUG-03 修復；平倉孤兒腿清理見 BUG-02 修復。
