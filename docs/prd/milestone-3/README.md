# Milestone 3 — 讓 agent 從「瞎子監督者」變「看得見、能閉迴路的監督者」

> 狀態：**草案 / Draft（方向已定，待開工）** ｜ 日期：2026-06-08
> 上層 PRD：[`../prd/sunday-project-prd.md`](../sunday-project-prd.md)（§7.9 legibility、§7.10 下令紀律、§2.1 alpha 在切換政策）
> 同層：[`../milestone-1/`](../milestone-1/)（Gate-1）、milestone-2（Gate-2，待建）
> 觸發：2026-06-08 帶入 agent 視角實跑一輪 analyst（被 `regime_shift` 喚醒 → 照 skill `curl` 行情 → **手算 EMA20/EMA50** 才知道方向）所暴露的體驗缺口。

---

## 0. 一句話定位

milestone-1.0 證明「架構通」（agent 能用通用 `bash`+curl 監督 Sunday）。但走過一輪才發現：**agent 是個隔著 HTTP 監督 Python 黑箱的瞎子——Sunday 內部算好的指標/regime/訊號不吐出來，逼 agent 用最易錯的 `curl→python` 重算一遍**。milestone-3 把「讓 swarm 真的監督得好」系統化，三條主線：

1. **Legibility（看得見）** — 從「理由」升級到「決策支援」：Sunday 把它已經算好的東西吐給 agent。
2. **Closed-loop（學得會）** — 每次拉 lever 的*結果*對 agent 可讀。這是 §2.1「alpha 在切換政策」的前置地基。
3. **Defensive ergonomics（不出錯）** — 用 API/skill 設計替「慢監督者 ↔ 快引擎」的時差防呆，而非只靠 prompt 紀律。

> 主題句：**把 agent 的眼睛打開、把回饋迴路關起來、把易錯的體力活拿掉。**

---

## 0.1 與 Gate / milestone 結構的關係（先講清楚，守紀律）

| 既有 | 對應 |
| --- | --- |
| milestone-1 = **Gate-1**（1.0/1.1/1.2） | testnet 驗證 swarm；成敗 = swarm 對不對，**與獲利無關**（D1） |
| milestone-2 = **Gate-2** | 真錢 + dashboard + 四個 extras；成敗 = 真實 P&L |

**milestone-3 不是新 gate，是一條橫切的「agent 監督體驗升級」。** 它讓 Gate-1 的監督迴路*真的好用*、並順手把 Gate-2 的學習地基（切換結果歸因）趁早鋪好。三件事先說明白：

- **守不變量 #4（evva 內零 Sunday-specific code）**：本 milestone 的 T1–T5 全是 **Sunday 端 Python + skill markdown + permission 設定**；唯一碰 evva 的 T6 是**回 `../evva` 開 refine-plan（RP）**，不在本 repo 改 `internal/swarm`。
- **守 D1（獲利不是 Gate-1 的 gate）**：milestone-3 **不追 P&L**。Closed-loop（T3）蓋的是「讓切換政策*可被學習*」的資料與透鏡，不是去調策略賺錢。真正用它煉 alpha 是 Gate-2。
- **誠實的依賴**：多數 engine 端項目是 milestone-1.0（T2–T4）端點的*增強*，需引擎先存在。**現在就能開工**的是 T1 契約、T2 skills、permission 設定、T6 的 RP 文件；**T3/T4/T5 的引擎實作折入 milestone-1.0 完成後**（見 §7 落地序）。

> **給 architect 的選項（M3-D1）**：若你偏好把這些**併進 milestone-1.1/1.2** 而非獨立 milestone-3，純粹是文件搬家、內容不變——說一聲即可。暫立為獨立 milestone-3 是因為它有一個清楚、自成一體的主題（agent legibility/閉迴路），且跨越 1.0 增強 + evva RP，放哪個既有 sub-PRD 都只裝得下一半。

---

## 1. 決策紀錄（M3-D1..D5）

| # | 決策 | 為什麼 |
| --- | --- | --- |
| **M3-D1** | 暫立**獨立 milestone-3**（橫切主題），但接受併入 1.1/1.2 | 主題自成一體且跨 1.0 增強 + evva RP；放哪個 sub-PRD 都只裝一半。待 architect 拍板。 |
| **M3-D2** | 新增 **`GET /signals`（live 決策面板端點）**，與既有 **`signals`（audit 表）** 命名上明確區分 | 端點服務「跨策略*此刻*的投票 + 指標 + regime 讀數」供 agent 決定要不要切；表是「每次決策的歷史特徵」供建模。兩者語意不同，別混。 |
| **M3-D3** | 切換結果歸因 = **lens（view/endpoint）over 既有 modeling-grade schema**，幾乎**零新 capture** | T1 的 `0001_init.sql` 前瞻性已足：`positions/fills/orders` 都 tag `strategy`、`strategy_state` 記每次切換的 who/when/why。「某次切換賺賠多少」是**可推導的查詢**，不是新表。schema delta 僅在確有必要時加（見 T3）。 |
| **M3-D4** | 防呆優先用 **API 契約**承擔（POST 回傳套用後完整 state + idempotent + `expected_current` 樂觀並發 → 可糾正錯誤），而非只靠 prompt 紀律 | 順 agent 既有直覺：evva 的 task 轉移 / `send_message` 收件人驗證都是「工具回可糾正錯誤 → agent 重試」。把 §7.10 的三條紀律從「文字叮嚀」變「機制保證」。 |
| **M3-D5** | evva 端缺口（`http_request`、漏斗緩解、agent↔agent 閉迴路）一律**回 `../evva` 開 RP**，不在本 repo 改 evva | 守不變量 #4 + 維持 swarm 的 multi-agent completeness oracle 性質。Sunday 只當 swarm 的*使用者*。 |

---

## 2. 契約 delta（= 要實作 + 要寫進 `/manual` + 要進 skill）

base `http://127.0.0.1:7777`。**新增/增強**相對 milestone-1.0 契約（[`../milestone-1/milestone-1.0/README.md`](../milestone-1/milestone-1.0/README.md) §3）：

| 變更 | 端點 / 形狀 | 服哪條主線 | 依賴 | ticket |
| --- | --- | --- | --- | --- |
| **新** | `GET /signals` → 每個候選策略此刻的投票 + 計算用指標 + regime 讀數（derived，非 raw OHLCV） | Legibility | T3 strategy.py | **T1** |
| **增強** | `GET /status` 加 `as_of_ts`、`last_lever{by,what,at}`、各策略 vote 摘要 | Legibility + 防呆 | T3 | **T1** |
| **增強** | 所有 **POST lever 回傳套用後的完整 state**（切完即見新 state，免第二趟 curl） | 防呆 | T3 | **T4** |
| **增強** | `POST /strategy` 收選填 `expected_current` → 狀態過期回**可糾正錯誤 + 當前 state** | 防呆 | T3 | **T4** |
| **新** | `GET /strategy/outcomes`（或 `/pnl?group=strategy_episode`）→ 每次切換的結果歸因（lens over 既有帳本） | Closed-loop | T3 帳本 | **T3** |
| **增強** | webhook payload 自帶 `status` 快照 + `rationale` + `suggested_action` | Legibility + 協作 | T4 notify() | **T5** |
| **新** | 兩個防禦式 skill：`operate-sunday`（leader）/ `query-sunday`（諮詢角色），含可複製 recipe + §7.10 紀律 | 三線皆服 | 無引擎依賴 | **T2** |
| **設定** | permission allow-rules：唯讀 curl（含新 `/signals`、`/strategy/outcomes`）放行；POST lever 維持 ask | 安全 | 無 | **T2** |
| **evva RP** | `http_request` 工具升第一順位、`risk_breach`→risk-monitor 窄 halt lever、leader→諮詢角色「採納與否」回信 | 協作/拓樸 | 回 `../evva` | **T6** |

> **legibility 是硬需求（沿用上層 §7.9）**：agent 監督品質直接被它決定。沒有 `/signals`，agent 不是在監督、是在對數字按讚。

---

## 3. Ticket 索引（一個 session 一個 T）

| T | 檔 | 做什麼 | 依賴 | 現在可開工？ |
| --- | --- | --- | --- | --- |
| **T1** | [`T1-legibility-decision-support.md`](T1-legibility-decision-support.md) | `GET /signals` live 決策面板 + `GET /status` 增強（as_of_ts/last_lever/votes）；契約寫進 `/manual` | strategy.py（M1.0-T3）impl；契約可先定 | 契約✅ / impl 待 T3 |
| **T2** | [`T2-defensive-skills.md`](T2-defensive-skills.md) | `operate-sunday` / `query-sunday` 兩 skill（防禦式 recipe + 下令紀律）+ permission allow-rules | 無引擎依賴 | ✅ **現在就做**（最 on-theme） |
| **T3** | [`T3-closed-loop-attribution.md`](T3-closed-loop-attribution.md) | `GET /strategy/outcomes` 切換結果歸因 lens（query over 既有帳本）；reviewer/leader 消費 | M1.0-T3 帳本有資料 | lens 設計✅ / impl 待帳本 |
| **T4** | [`T4-defensive-api.md`](T4-defensive-api.md) | POST 回 resulting state + idempotent + `expected_current` + `/status` staleness | M1.0-T3 lever 端點 | 契約✅ / impl 折入 T3 |
| **T5** | [`T5-self-sufficient-webhooks.md`](T5-self-sufficient-webhooks.md) | webhook payload 自帶 status 快照 + rationale + suggested_action | M1.0-T4 notify() | 契約✅ / impl 折入 T4 |
| **T6** | [`T6-evva-refine-plans.md`](T6-evva-refine-plans.md) | 回 `../evva` 開 3 份 RP：`http_request` promote、漏斗緩解、agent↔agent 閉迴路 | 回 evva | ✅ 文件即可開（不改 sunday code） |

> **平行性**：T2（skills）、T6（evva RP）不依賴 Sunday 引擎，**現在就能交付**。T1/T3/T4/T5 的*契約*現在可定稿（並寫進 `/manual` 與 skill），*引擎實作*折入 milestone-1.0 的 T3/T4 一起做（避免改兩次）。

---

## 4. 驗收（DoD）— 「agent 監督得好」可被證明

對齊上層 §9 的精神（主動製造例外、看 swarm 是否確定性正確反應），milestone-3 額外要證明**體驗**升級：

- **A1 — 不再手算**：transcript 顯示 agent 讀 `GET /signals` 就能做出「切/不切」決策，**不再 `curl→python` 自算指標**。
- **A2 — 切完即見**：`POST /strategy` 後 agent 從**回應本身**確認新 state，無需第二趟 `curl /status`。
- **A3 — 過期被擋**：對過期狀態下 `/strategy`（帶舊 `expected_current`）→ 收到**可糾正錯誤 + 當前 state**，agent 重抓後重送成功。
- **A4 — 閉迴路**：reviewer/leader 能對「14:30 那次切到 mean_reversion」給出**結果歸因**（`GET /strategy/outcomes`：+X%、N 筆、勝率）。
- **A5 — 自給自足喚醒**：被 webhook 喚醒的 agent **首輪不必 curl** 即可定位狀況（payload 自帶 status 快照 + rationale + suggested_action）。
- **A6 — 零整合碼仍成立**：以上全程 evva 內**無任何 Sunday-specific code**（沿用 §9 V9）；T6 的能力升級走 evva RP，不破不變量 #4。

> milestone-3 **不**驗獲利（守 D1）。它驗的是「agent 看得見、學得會、不出錯」。

---

## 5. 不在範圍（milestone-3）

- **真錢 / dashboard 視覺化** — Gate-2 / milestone-2。本 milestone 只做*資料 + 透鏡 + 端點*，不做 UI。
- **改 evva internal** — T6 一律走 RP，不在本 repo 改 `internal/swarm`（不變量 #4）。
- **第 4 根 lever（策略參數微調）** — 上層 §12.9，Gate-2 候選，本 milestone 不做。
- **新策略 / 多標的籃子 / 耐久 run** — milestone-1.1/1.2。

---

## 6. 落地序（建議）

```
現在就能做（無引擎依賴）
  ├─ T2  兩個防禦式 skill + permission rules        ← 最 on-theme，先做
  ├─ T1/T3/T4/T5 的「契約」定稿 + 寫進 /manual       ← 把眼睛/迴路/防呆的規格釘死
  └─ T6  回 ../evva 開 3 份 RP                       ← 文件，獨立

待 milestone-1.0 引擎（T2–T4）up 後
  └─ T1/T3/T4/T5 的「引擎實作」折入 M1.0 的 T3/T4    ← 一次做完，不改兩次
```

> **與「轉向營利」的對齊**：你要的「營利」在專案詞彙裡 = Gate-2。milestone-3 不直接賺錢，但它把 Gate-2 唯一有指望的 alpha 來源（**切換政策**，§2.1）變得*可觀測、可學習*——這是現在（Gate-1）最該、也最便宜鋪的營利地基。先把迴路關起來、把資料攢起來，Gate-2 才有東西可煉。
