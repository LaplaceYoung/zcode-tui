# TUI 设计调研与 ztui 采纳清单

调研时间：2026-09-13 · 来源：OpenCode TUI 官方文档（/docs/tui/）与 Claude Code Interactive Mode 官方文档（code.claude.com/docs/en/interactive-mode），见下。

## 调研结论 → 已采纳

| 来源 | 设计点 | ztui 实现 |
|---|---|---|
| OpenCode `/docs/tui/` | `!` 前缀直接跑 shell，输出进入对话上下文 | `_shell_prefix`：本地执行 → Shell 工具块 + 注入 loop 消息（`$ cmd` + `<shell_output>`） |
| Claude Code interactive mode | Queue messages while Claude works（忙时消息排队） | `_queue`：忙时输入入队（UserMsg 标 `queued`），回合结束自动 dispatch；Esc 可丢弃 |
| Claude Code subagent 块内嵌列表 | 子代理工具调用在父块内实时滚动 | task ToolBlock `sub_update`：运行中 ▸ 滚动、完成 ✔（含输出首行）、结束后保留 activity 列表 + 最终报告 |
| OpenCode `/undo` + git 语义 | 撤销最后一轮（消息 + 文件变更） | `loop.record_preimage`（write/edit 工具自动存档 pre-image）+ `/undo` 恢复文件、裁剪消息并重渲染 transcript |
| Claude Code `Ctrl+V` / 图片引用 | 图片作为内容块进上下文 | `@path/to/img.png` 提及后自动 `base64` 图片块；按模型 modalities 门控（文本模型忽略并提示） |
| OpenCode `/details` | 折叠/展开全局输出 | ctrl+o 展开最近工具块（已实现）+ 输出默认折叠 8 行 |
| OpenCode `/compact` | 上下文压缩 | auto 85% + `/compact`（前一批） |
| OpenCode `@` 文件提及 | 模糊文件补全 | `@` 提及补全（前一批） |
| OpenCode attention/notifications | 权限请求/完成时响铃 | `/bell` 切换（permission ask + turn finish） |
| OpenCode `/thinking` toggle | 思考块显隐开关 | `/thinking`（对现存全部 ThinkingMsg 生效并持久化） |
| Claude Code 输入历史 | 上下键召回历史输入（带草稿恢复） | `_history_intercept`：首行 ↑ 回退、末行 ↓ 前进，持久化到 history.json |
| Claude Code `/export` | 导出会话 Markdown | `/export` → `~/.local/share/zcode-tui/exports/` |
| Claude Code prompt suggestions | 灰字建议 | （延后：placeholder 静态文案已有） |

## 调研结论 → 已评估暂不采纳

| 设计点 | 决策 |
|---|---|
| Gemini CLI checkpoint/restore via git | ztui 已有自研 /undo（文件 pre-image 级）；git 级 checkpointing 列入 roadmap |
| OpenCode /editor（$EDITOR 外编） | 低价值，延后 |
| OpenCode themes 命令面板 | Textual CSS 已可换；延后 |
| Claude Code vim mode | 大工程，延后 |
| OpenCode leader key (ctrl+x) | ztui 直接 / 命令 + 弹层，简化 |
| 桌面通知（terminal unfocused 时） | 响铃已覆盖核心场景 |

## 本批新增（zcode GUI 功能还原）

- `/automations`：只读列出 tasks-index.sqlite 的计划任务（状态圆点、cron 表达式、下次/上次运行、最近 outcome、最近错误）。
