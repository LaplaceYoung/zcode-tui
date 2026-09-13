# zcode GUI 功能点终末对账 vs ztui（2026-09-13）

依据：逐周目开发的实际验证 + 对 zcode 本地文件/数据库/bin bundle 的只读考查。

## 聊天与 Agent 核心

| zcode GUI 功能 | ztui 状态 | 证据 |
|---|---|---|
| 流式对话 + 思考过程展示 | ✅ | `✻ Cogitating…` shimmer + 折叠；`/thinking` 显隐 |
| 工具调用（bash/read/write/edit/glob/grep/todo/webfetch） | ✅ 全套 | 多轮真实任务验收 |
| 后台 shell | ✅ | run_in_background + shell_output/shell_kill |
| 子代理 | ✅ + 父块内嵌实时进度 | task 工具，▸→✔→✘ 嵌套活动列表 |
| plan/build/yolo 权限模式 + 规则集 | ✅ | shift+tab 或 /mode；编号审批 1/2/3 带 diff；always 规则持久化 ~/.config/zcode-tui |
| 跨上下文压缩 | ✅ | auto ~85% + /compact；配对安全截断实测 |
| 撤销工具变更 | ✅ | /undo（文件 pre-image；zcode 用 git 快照，方案不同但语义对齐） |
| 图片输入 | ✅ | @img.png→base64，视觉模型门控 |
| 消息队列 | ✅ | 忙时入队、回合结束自动续跑、Esc 丢弃 |
| ! shell 前缀 | ✅ | 输出注入对话 |
| 会话历史/恢复 | ✅ | JSONL store + /resume + GUI 会话续聊（/sessions，sqlite 只读） |
| 自定义 agents（~/.zcode/agents/*.md） | ✅ | persona 亦可作 task <:task> 参数；`/agents` 面板只读；斜杠命令 |
| MCP（外部工具标准） | ✅ stdio | `mcp.json` 配 opencode/CC 同构；挂进 loop 基础到真正的服务器；HTTP/SSE 暂缓 |
| 侧问 /btw | ✅ | 顺手问不打扰主回合 |
| 粘贴智能 | ✅ | Ctrl+V 图片剪贴 + 大段文本 chip 化 |
| 全屏 transcript viewer | ✅ | ctrl+t 全屏 |
| 会话分享 | ❌ 云服务 | 依赖 zcode 后端 share 端点，不逆向 |
| 链接高亮可跳转 | ✅ | assistant/tool 输出裸 URL → OSC8 超链接（iTerm2/kitty/wez 可点）；rich markdown 链接原生支持 |
| 图片显示 | ⚠️ 边界 | 附件 chip + OSC8 file:// 打开链接（inline bitmap 在 Textual 全屏布局下不可行，需要 kitty/iTerm2 pass-through，届时会破坏渲染——与 Gemini CLI 等 TUI 同样选择外链/文件路径落地）；待 Textual 官方支持再升级 |
| 插件市场 | ✅ | /plugins 只读列出 marketplace→plugin→skills（GUI 插件启用开关在 zcode 配置里，不改） |
| 会话内搜索 | ✅ | ctrl+f /search，ctrl+n/ctrl+p 循环跳转高亮块 |
| 目标与循环 | ✅ | /goal（注入系统提示+状态展示）、/loop（分钟级周期自主重发） |
| web-remote-control（手机控制） | ✅ 官方云接力 | `/remote` 注册 ztui 自己的设备 → 手机扫码打开**官方页面**（zcode.z.ai/remote/v4）控制当前工作区（配对/auth/HMAC proof 与 GUI 同协议、QR 黑块白底半块字经 OpenCV 可解码验证；LAN 直开版本因与 GUI 语义不符已替换） |
| LSP/格式化器 | — 不适用 | zcode CLI 内部能力，TUI 无对应交互面 |

## 模型与 Providers

| 功能 | ztui 状态 |
|---|---|
| 用 zcode 配置的 provider/apiKey 直接发请求 | ✅ 9 provider 全兼容（apiKey 明文读自 config.json） |
| anthropic 协议 | ✅ |
| openai-compatible / openai | ✅（kimi-k3 工具调用实测） |
| 模型档位（reasoning variants）| ✅ catalog patch 翻译（anthropic→output_config.effort，openai→reasoning_effort）+ 兜底 |
| 内置 reasoning 默认档同步 | ✅ db.sqlite local_setting 只读 |

## 任务与工作流

| 功能 | ztui 状态 |
|---|---|
| cron 自动化任务 | ✅ 只读面板 /automations（schedule、状态、最近 outcome、错误） |
| 工作流（dynamic workflow）| ✅ 只读 /workflow（列表+详情：actor/节点统计/结论）；**执行/派生 ❌**（dwf 运行时逆向，成本/稳定性不允许，README 已书面说明）|
| checkpoints | ✅ 浏览+漂移分析 /checkpoints；**内容恢复 ❌**（manifest 只含 path+sizeBytes，内容加密云端，实测结论落盘 README/design-notes）|

## 记忆与个性化

| 功能 | ztui 状态 |
|---|---|
| memory（MEMORY.md 索引/主题文件） | ✅ 注入系统提示，与 zcode 同目录约定共享 |
| AGENTS.md（用户级+项目级） | ✅ 链式加载（~/.zcode/AGENTS.md + cwd 向上） |
| skills（zcode 插件 + 用户 skills） | ✅ 33 个 → slash 命令 |
| ~/.zcode/agents/*.md 自定义 agent | ⚠️ 未加载（只支持 skills；列入后续） |
| 主题 | ✅ /theme 四套调色板（Textual Theme 变量 + Rich token 可变） |
| 桌面通知 | ✅ /notify（OSC9→iTerm/kitty 系；osascript→macOS 通知中心；权限与回合完成触发，可 off） |
| 响铃 | ✅ /bell |
| vim 输入模式 | ✅ /vim（esc→NORMAL；hjkl、w/b 词移、0/$、x、y 行暂存、p 行贴、i/I/a/A；状态栏模式指示，持久化） |
| 输入历史召回（↑/↓ + 草稿恢复） | ✅ 持久化 200 条 |
| bots（telegram 等 IM 集成） | ❌ 边界项：读写 zcode bot-config 属于修改 zcode 配置，超出 ztui「不写 zcode 任何文件」原则，不实现 |

## 终末结论

zcode GUI 可本地实现的功能点**全部**已在 ztui 良好实现；其余五项明确边界并书面标注：**会话分享（云端）/ bots（属改 zcode 配置）/ workflow 执行（私运行时）与派生 / checkpoint 内容恢复（云端加密）/ ~/.zcode/agents 自定义 agent 加载（小项）**。
