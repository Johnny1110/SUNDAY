# BUG-02 — 平倉後 TP/SL 孤兒單未自動清除

## 嚴重程度
⚠️ Medium — 殘留掛單不會反向開倉（reduce_only），但污染 open orders、增加巡檢成本，且可能觸發無效訂單

## 重現步驟

1. 開倉（帶 TP/SL）
2. 透過任何方式平倉（`/api/perp/close`、market reduce-only sell）
3. 查詢 `GET /api/account/orders/open` — TP/SL 仍殘留

## 實際案例

### 案例 A：HYPEUSDT（2026-06-11 23:54）
- 倉位被平倉後，SL #1000000102687890（55.2，70.54 張）殘留
- TP #1000000102687889 自動消失（行為不一致！）

### 案例 B：VELVETUSDT（2026-06-12 00:09）
- 倉位被全平後，TP #1000000102700957（0.80，2,353 張）和 SL #1000000102700959（0.91，2,353 張）**皆殘留**
- 需 trader 手動清理

## 行為不一致

| 事件 | TP 處理 | SL 處理 |
|------|---------|---------|
| HYPE 平倉 | 自動消失 ✓ | 殘留 ✗ |
| VELVET 全平 | 殘留 ✗ | 殘留 ✗ |

## 修復建議

規則應為：
- **倉位 qty > 0**：TP/SL amount 自動同步倉位數量
- **倉位 qty = 0**：所有 algo TP/SL 自動取消

參見 PRD-002-auto-cleanup-tpsl。

---

— friday, 2026-06-12 01:36 CST

## ✅ 已修復（2026-06-12）

幣安端撤腿行為本就不一致（案例 A/B 互相矛盾），Sunday 改為**自己保證**「qty = 0 → 該標 TP/SL
全撤」：

1. `POST /api/perp/close` 平倉後立刻清掃該標兩本訂單簿的觸發腿，回應帶 `cancelled_protection`
   （撤單失敗列在 `cancel_failed`；清掃本身失敗不影響平倉結果）。
2. 倉位以**其他方式**歸零（TP 觸發留下 SL、外部平倉）：監控輪詢 `refresh_book` 偵測到倉位從
   book 消失時自動清掃（`exchange.sweep_orphan_legs`）。只撤「觀測到平倉那一刻之前建立」的腿
   （以伺服器對時戳記比對 createTime）——同一輪詢窗內平倉又重開的新腿不會被誤殺；非觸發腿
   （限價進場單）一律不碰。腿已被幣安自行撤掉（unknown order）視為達成目標、不算失敗。

「qty > 0 時 TP/SL 數量自動同步倉位」屬 PRD-002 範圍，本次未做（`sl_qty_covers` 旗標仍會標出
半保護倉位）。迴歸測試：`tests/test_tpsl_safety.py`（sweep / close）、
`tests/test_monitor_refresh.py::TestOrphanSweepOnDrop`。
