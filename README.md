# ztui

**运行在你 ZCode 模型配置上的终端 Agent**——Claude Code / OpenCode 风格的 TUI，零额外 API key，纯 Python + Textual，直接复用你在 ZCode 客户端里配置好的所有 provider。

<p align="center"><img src="docs/images/banner.png" alt="ztui 欢迎屏（python 黄蓝默认主题）" width="86%"></p>

<p align="center"><img src="docs/images/demo.gif" alt="实时录屏：提问→bash 工具→完成" width="86%"></p>

## 为什么是 ztui

ZCode 桌面客户端很好用，但有时你只想在终端里把活干完。ztui 把那套**同宗同源**的能力全搬进终端：

- 读 `~/.zcode/v2/config.json`（只读）：provider / apiKey / 模型限额 / 思考档位直接可用
- Anthropic + OpenAI-Compatible 双协议：GLM-5.3、GLM-5.3-Flash、Kimi-K3、DeepSeek、OpenRouter 全通
- 与 zcode GUI 同协议的手机远程（云接力）：手机扫码接管当前工作区
- 尽力还原 zcode GUI 覆盖的交互：思考 shimmer、工具块、子代理、压缩、skills、@提及、vim、主题……

## 快速开始

```bash
git clone https://github.com/LaplaceYoung/zcode-tui
cd zcode-tui
python3 -m venv .venv && .venv/bin/pip install -e .

# 任意目录均可调用 ztui（向 PATH 写一个 wrapper 即可，如 ~/.local/bin/ztui）：
ztui                      # 启动 TUI（默认 Python 黄蓝主题）
ztui -p "总结当前目录"     # 打印模式（可脚本化调用）
ztui --model kimi-k3      # 换模型
ztui --resume             # 开 resume 弹层接管既有会话
```

**不需要**再配置什么（前提是本机已经装过 ZCode 桌面端并登录过 provider）。

## 界面分工怎么读这张图

<p align="center"><img src="docs/images/transcript.png" alt="一个完整的工具会话：ls + write + 确认" width="86%"></p>

- **`>` 用户消息**，之后紧跟 `✻ Thought for a while`（可折叠的思考内容，与 zcode GUI 的 reasoning 面板同级呈现）
- **`⏺ 工具块`**：`bash(ls -1) ✔ 0.07s`——头部 + ☑状态 + 耗时；`write` 带绿色 `+` 新增行的 diff
- **助手正文**：markdown（含「可点蓝链接」）
- **信息条（输入框上方）**:`0.7% ▓░░░░░ 6.5k/1.0M · ⎵ 2.94s · 14 tok/s · 21% cache · ↑33.5k ↓280`——上下文压力（绿/黄/红）+ 首字延迟 TTFT + 生成速度 + 缓存命中 + 用量读数
- **输入框**：`shift+tab` 切 plan/build/yolo；输入框边框反映模式色（plan 蓝 / build 主题黄 / yolo 红）
- **状态栏**（底部）：模型@档位 · provider · 模式 · cwd · token（+运行中 timer）

## 模型与思考档位

`/model`：provider → 模型 → 思考档（low/high/max 等由 zcode 定义档位翻译成请求体补丁）：

<p align="center"><img src="docs/images/model.png" alt="/model 选择器" width="62%"></p>

缺省沿用 zcode 客户端目前在用的 provider+档位（bigmodel-coding-plan GLM-5.3@max 等），也可在 `~/.config/zcode-tui/config.toml` 覆盖持久化。

## 权限与安全网

build 模式下任何可变更操作（写文件 / 执行命令 / 联网抓取）都会弹出 **带命令或 diff 预览的审批**：

<p align="center"><img src="docs/images/permission.png" alt="权限审批：1 Yes / 2 Always / 3 No" width="62%"></p>

`1 Yes` / `2 Always`（规则持久化）/ `3 No`（回绝不影响会话）——plan-mode 下不小心发起任何变更会被直接拒并说明；`rm/sudo/动 ~/.zcode` 在所有模式都会强制重审。

## 手机远程接管

`/remote`：ztui 注册自己的设备 → 终端输出**白卡二维码**（GUI 同协议），用手机相机扫码，或打开链接 (`https://zcode.z.ai/remote/v4?…`)，就能像 GUI 一样远程监控/操控当前工作区。

<p align="center"><img src="docs/images/remote.png" alt="web 远程控制二维码" width="52%"></p>

## 主题配色

`/theme` 切五套预置（默认 **python 黄蓝**、claude-dark、gruvbox、tokyo-night、paper-light）。

| tokyo-night | gruvbox |
|---|---|
| <img src="docs/images/theme-tokyo-night.png" width="92%"> | <img src="docs/images/theme-gruvbox.png" width="92%"> |

## 功能一览

**Agent 本体**：流式对话 + thinking、后台 bash 三件套（run_in_background / shell_output / shell_kill）、子代理（task）带**嵌套实时进度**、压缩 compaction、undo 文件回滚、plan/build/yolo 权限与持久规则、plan 模式安全网

**交互**:`!` 命令前缀 shell、忙时消息排队、@提及文件/图片、OSC8 链接跳转、ctrl+f 搜索、输入历史召回、`/goal` 目标任务命令、`/loop` 周期循环、`/export` 导出 Markdown

**zcode 互操作（全只读）**：会话续聊 `/sessions`、定时任务面板 `/automations`、checkpoints 漂移分析 `/checkpoints`、工作流巡检 `/workflow`、skills 复刻为 slash 命令（33 个已加载）、memory 共享

**个性化**:`/theme`、`/notify`、`/vim`、mode 色输入边框、placeholder、ASCII 二维码、渐变 logo

## 架构速览

```
目录结构：
zcode_tui/
├── zconfig.py    # zcode 配置只读适配（不碰任何 zcode 文件）
├── reasoning.py  # catalog 档位→协议 patch（anthropic/openai）
├── providers/    # anthropic + openai-compat 流式客户端（统一事件）
├── agent/
│   ├── loop.py        # 主循环: 流式→工具→权限→回注（支持中断与压缩）
│   ├── context.py     # AGENTS.md 链 + memory 注入 + goal
│   ├── permission.py  # plan/build/yolo 模式 + 规则集合 + 危险命令兜底
│   ├── session.py     # JSONL 会话存储 + /resume / zcode GUI 续聊
│   ├── compaction.py  # 上下文自动摘要（约 85% 阈值，工具配对截断）
│   ├── skills.py      # zcode/user skills → slash 命令
│   ├── tools/         # bash 后台/read/write/edit/glob/grep/todo/webfetch/task
│   └── z*.py          # zcode sqlite/tasks-index/checkpoints/dwf/plugins 只读层
└── ui/
    ├── app.py         # Textual 主 app, 事件路由, 面板, vim/goal/loop/search
    ├── widgets.py     # banner/tool/thinking/todo/attach/Qr metrics 渲染
    ├── theme_tokens.py# 五套配色 + 跟随主题的 live token
    └── relay/remote   # 官方云接力 client(wss+auth+proof)+QR 渲染
```

## 常见问题

- **用不用 ZCode 网页端？** 不需要：ztui 直接用你 zcode 桌面端 provider 配置的 apiKey，离线直连模型 API。
- **会话能恢复吗？** 本地 `/resume` 恢复 ztui 会话；`/sessions` 可续聊 zcode GUI 的历史会话（只读其 sqlite，不改 zcode 任何东西）。
- **`/remote` 和 ZCode 桌面端的手机控制冲突吗？** 不冲突：ztui 铸造自己的设备 sid+passHash，与 GUI 已有的设备并不互相踢掉；只读镜像手机端的"工作区"。
- **为什么不 inline 显示图片？** Textual 全屏渲染 + kitty/iTerm2 选择性穿透会破坏版面——项目选择 📎 附件 + OSC8 `file://` 外部链接，与 Gemini CLI 等同样选择，架构说明见 `docs/coverage.md`。

## 交付边界说明（书面）

以下依赖 zcode 私有运行时/云服务的功能本批不逆向实现：**会话分享链接**（zcode 云）、bots（改 zcode 配置）、workflow 执行/派生（dwf 运行时）、checkpoint 内容恢复（清单含 path+sizeBytes，不含内容而上云）。逐项查证记录见 `docs/`。

## License

MIT

---
**极客提示**：TUI 设计实践研究与采用清单在 `docs/design-notes.md`，功能终末对账在 `docs/coverage.md`，12 笔 `feat(*)` Conventional Commit 记录每一份来龙去脉。
