# PRD-001 結案：不是時鐘偏差，是「無時區標籤」——時間都是對的，標籤是錯的

> **狀態：已修復（evva 端已改碼，重啟 swarm 後生效；Sunday 端新增 `/api/system/time`）**
> 結論日期：2026-06-10

## 裁定

PRD-001 報告「系統時鐘比 UTC 快 8 小時」。實測**時鐘沒有偏差**：

- 運行環境 `date -u` 與 Google/Cloudflare 的 HTTP `Date`（GMT）誤差 **3 秒**。
- 運行環境的本地時區是 **UTC+8**（HKT）。`currenttime: 2026-06-10 09:00:00` 是**正確的
  本地牆鐘時間**——它剛好等於 User 的手錶，這正是時鐘沒偏的證明。
- 真正的問題：harness 注入的每一個時間字串（`currenttime`、webhook `time=`、alarm 回覆、
  `list_members` 的 ⏰）都**不帶時區標籤**，而整個系統沒有任何地方告訴 agent「local = UTC+8」。
  Agent 只能猜，猜了 UTC，於是觀察到「快 8 小時」。
- 因此 PRD-001 建議的修復方向 1–3（查 `date -u`、host NTP、啟用自動同步）**全部不適用**；
  方向 4（time endpoint）已採納，見下。

## 對 PRD-001 四項影響的重新評定

1. **friday `*/30` 巡檢——不受影響。** 常數偏移不改變間隔長度；間隔型 cron 照常每 30 分鐘跑。
2. **CPI alarm——風險為真，但機制是時區解析。** `alarm_set` 的裸字串按**本地時區**解析：
   `2026-06-10 12:25:00` 會 armed 在 12:25 **+08:00**（= 04:25 UTC），比意圖中的 12:25 UTC
   早 8 小時觸發。**修法（現有版本就支援）**：用帶時區的 RFC3339 —— `2026-06-10T12:25:00Z`，
   或直接寫本地時間 `2026-06-10 20:25:00`。
3. **trades/journal 時間戳——Sunday 自身是準的。** engine 一律 `datetime.now(timezone.utc)` +
   Binance serverTime 偏移校正。只有 agent 自己寫進 journal/memory 正文裡的時間需要注意標 offset。
4. **researcher `0 0,8,16 * * *`——功能沒壞。** cron 按本地牆鐘比對，仍是嚴格每 8 小時，
   相位在本地 0/8/16 點（= UTC 16/0/8）。reviewer 的 `0 0 * * *` 是**本地午夜**復盤，對
   UTC+8 的 User 而言正是合理語義。

## 已落地的修復

### evva（需重新編譯 + 重啟 swarm 生效）

所有餵給 agent 的牆鐘字串統一帶明確 UTC offset（`2026-06-10 12:25:00 +08:00`）：

- 排程喚醒 `currenttime`、**信件投遞**（新增 currenttime 標頭 + 每封信 `[sent …]` 戳）、
  webhook `external-event` 的 `time=`、`schedule_wakeup` 結果。
- `alarm_set` 確認回覆同時給出 **UTC 對照**：`set for 2026-06-10 20:25:00 +08:00
  (= 2026-06-10 12:25 UTC)`——下完 alarm **看一眼回覆**，意圖是 UTC 卻對不上就立刻改。
- `list_members` 標頭標明時區；`schedule_set` 工具說明寫明 cron 按本地牆鐘比對。
- 系統提示詞 Environment 新增一行靜態時區契約（zone 整個運行期不變，不破 prompt cache）。

### Sunday（新端點）

`GET /api/system/time` —— 對時錨點：

```json
{"epoch_ms":1781123100000,"utc":"2026-06-10T04:25:00+00:00","local":"2026-06-10T12:25:00+08:00",
 "tz":"HKT","utc_offset":"+08:00","binance_clock":{"offset_ms":-3,"synced":true}}
```

## 全隊時間慣例（從現在起）

1. **跨系統對時用 `epoch_ms`**（無時區、不會被誤讀）；懷疑自己的時間感就打 `/api/system/time`。
2. **沒帶 offset 的牆鐘字串一律是本地時間（UTC+8）**；要表達 UTC 就寫 RFC3339 帶 `Z`。
3. **自己寫時間（journal、memory、報告）請帶 offset**：`2026-06-10 20:25 +08:00`。
4. 排 alarm 對應外部事件（CPI/FOMC 等 UTC 公布時刻）時，**用 RFC3339 `…Z` 格式**，
   並核對確認回覆中的 UTC 對照行。
