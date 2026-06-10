# PRD-003：用 evva per-member permissions.json 技術強制「只有 friday 下單」

> 狀態：**提案（含 trade-off，需 operator 拍板）**（開票日期 2026-06-10，來自 evva 控制流程稽核）

## 卡在哪 / 想解決的問題

「只有 friday 下單」目前是 **prompt 紀律，不是技術強制**（workflow.md §2 已誠實標注）：
Sunday API 免 token、每個 worker 都有 `http_request`、`permission_mode: bypass` 下全部放行。
analyst-news / researcher 會瀏覽外部網頁，是 prompt-injection 的入口——惡意內容理論上可以
誘導 worker 直接 `POST /api/perp/order` 或 `DELETE /api/perp/orders`。

## evva 已有的機制（已驗證）

每個成員可放一份 `<agentDir>/permissions.json`（Claude Code 相容的
`{permissions:{allow,deny,ask}}`），**只**載入該成員的 permission store（RP-11，
`agentdef/member.go:101-105`——註解甚至直接拿 risk-monitor 當例子）。

## 關鍵限制（這張票存在的原因）

**`permission_mode: bypass` 完全跳過規則查詢——deny 也不生效**（`pkg/permission/decision.go:42`：
「ModeBypass → allow (no rule lookup; bypass means bypass)」）。所以技術強制必須**離開 bypass**，
而這是一個 trade-off：

| | 現狀（bypass + prompt 紀律） | 提案（default mode + per-member 規則） |
| --- | --- | --- |
| worker 下單 | prompt 說不行，技術上可以 | **deny 規則硬擋** |
| 無人值守 | 全部放行，不會卡 | 漏列一條 allow → 半夜卡在等審批 |
| 設定成本 | 零 | 每個 worker 一份完整 allow 清單，要維護 |

## 期望的做法（若採納）

1. `evva-swarm.yml` 的 `permission_mode` 改回預設（非 bypass）。
2. 每個 **worker** 的 `agents/sub/<name>/permissions.json`：
   ```jsonc
   { "permissions": {
       "deny":  ["http_request(POST http://127.0.0.1:7777/api/perp/*)",
                 "http_request(DELETE http://127.0.0.1:7777/api/perp/*)"],
       "allow": ["http_request", "read", "write", "edit", "bash", "web_search", "web_fetch", "skill"]
   } }
   ```
   （deny 比 allow 優先；allow 要覆蓋該 worker 全部正常操作，否則會 ask 卡住。
   實際 rule 的匹配語法需先讀 `pkg/permission` 確認 http_request 參數匹配怎麼寫。）
3. **friday** 的 permissions.json 給全量 allow（他是唯一需要交易端點的人）。
4. swarm 注入的協調工具（send_message / task_* / alarm_set…）本來就在 auto-allow
   清單（`swarm/tools/set.go` init），不受影響。

## 驗收

- worker 嘗試 `POST /api/perp/order` → 被 deny，事件留痕。
- 全 swarm 跑 24h 無一次 ask 卡住（allow 清單完備）。
- friday 下單行為完全不變。

## 不採納的條件

若認為 testnet 假錢階段不值得這個維護成本，明確記錄「接受 prompt 紀律邊界直到轉真錢前」，
並把本票列為 **Gate-2（真錢）前的必辦項**。
