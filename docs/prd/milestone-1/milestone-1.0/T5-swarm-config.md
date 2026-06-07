# T5 — swarm 設定（evva-swarm.yml + friday/analyst + permission）

> 1.0 任務 **5/6** ｜ 共用契約見 [`README.md`](README.md) ｜ **依賴：無**（可與 T1–T4 平行；只需 `../evva` 的格式參考）

## 做什麼
把監督端配置出來：兩個 agent（friday + analyst）、它們的 prompt/tools/skill、以及 permission allow-rules。**全是設定與文件，零 Sunday-specific Go code**（不變量 #4）。格式對照 `../evva/docs/veronica/example-swarm/` 與 `vero-tech-swarm/`。

## 交付
- `evva-swarm.yml`：space `sunday`；leader `friday`（`schedule: every 30m`，prompt=dead-man check）；worker `analyst`；`permission_mode: default`。
- `agents/main/friday/`
  - `profile.yml`：sonnet-4-6 / high / `when_to_use` / `advertise_skills: true`。
  - `system_prompt.md`：CEO·風險長人格；「Sunday 是交易引擎不是隊友」；收 `external-event` → 評估 → 必要時指派 analyst → 行使 lever → 平時 stand down；**內化下令紀律**（切策略前查 `/status`、切後驗證、**附 `reason`**）。
  - `tools/active.yml`：`bash`、`web_fetch`、`send_message`、`list_members`、`schedule_set`、`schedule_clear`、`Agent`。
  - `skills/operate-sunday/SKILL.md`：唯讀 recipe + lever recipe（1.0：`/strategy`+reason、`/halt`、`/heartbeat`）+ 下令紀律 + 「細節 `curl :7777/manual`」。
- `agents/sub/analyst/`
  - `profile.yml`：sonnet-4-6 / high / `when_to_use`。
  - `system_prompt.md`：regime/趨勢分析師；被指派或收事件→查 `/market`+`/status`(+web 新聞)→`send_message` 回 friday「方向+建議策略+理由」；**不碰 lever**。
  - `tools/active.yml`：`bash`、`web_fetch`、`web_search`、`send_message`、`my_tasks`、`task_get`。
  - `skills/query-sunday/SKILL.md`：唯讀 recipe（`/status`、`/market`、`/positions`、`/pnl`）。
- permission allow-rules（落點對照 evva settings/config）：唯讀 curl（`/status`·`/market`·`/positions`·`/pnl`·`/manual`·`/heartbeat`）放行；`POST /strategy`·`/halt` **不放行 → 維持 ask**。

## Done
- `evva service start` + `evva swarm .` → space `sunday` 註冊成功；roster 看得到 friday + analyst。
- 兩 agent 的 tools/skill 正確載入；唯讀 curl 不跳審批、lever curl 跳審批。

## 不在本任務
- 引擎本身（T1–T4）；端到端串接與 demo（T6）；其餘三角色（1.1）。
