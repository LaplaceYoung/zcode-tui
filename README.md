# ztui

A terminal agent client (Claude Code / OpenCode-style TUI) that runs on **your existing ZCode model configuration** — no separate API keys, no zcode daemon required. Pure Python + [Textual](https://textual.textualize.io).

ztui reads `~/.zcode/v2/config.json` (providers, API keys, model limits, reasoning variants) read-only and talks to the model APIs directly. It never writes to any ZCode file or database.

## Features (M4)

- **Streaming chat agent** with the Claude Code visual vocabulary: `⏺` bullets, `⎿` collapsible output, inline diffs, animated `✻ Cogitating…` shimmer, streaming `▍` cursor, todo checklists, colored status line, gradient ASCII logo.
- **Dual protocol**: `anthropic` + `openai-compatible` — every provider configured in zcode works.
- **Tools**: `bash` (+ background → `shell_output`/`shell_kill`), `read`, `write`, `edit`, `glob`, `grep`, `todo`, `webfetch`, **`task` (sub-agent with live nested progress)** — the parent's tool block shows the sub-agent's own tool calls scrolling in real time (▸ running → ✔ done with output head).
- **Context compaction**: auto at ~85% + `/compact`.
- **Skills**: 33 zcode/user skills as slash commands.
- **Interaction layer (researched from OpenCode TUI & Claude Code docs, see `docs/design-notes.md`)**: **message queueing while busy**, **`!` shell prefix** (output injected into the conversation), **`/undo`** (reverts last turn's file changes + messages), **image input via `@img.png`**, `/export` Markdown, `/bell`, `/thinking`, input history (↑/↓ with draft restore, persisted), **`/goal`** (objective injected into system prompt), **`/loop`** (minute-scale autonomous repeats), **`/search`** (ctrl+f, transcript find with ctrl+n/ctrl+p jumping), **OSC8 hyperlinks** (bare URLs clickable), **📎 image attachment chips** with clickable file:// links.
- **Plugins**: `/plugins` — marketplace → plugin → skills browser (read-only); all skills also available as slash commands (33 loaded).
- **zcode interop (read-only)**: `/sessions` continues GUI conversations, `/automations` lists scheduled tasks, **`/checkpoints` browses workspace checkpoint inventories with drift analysis**, **`/workflow` inspects dynamic workflow runs** (status, tokens, actors, node stats, conclusions), memory dir shared.
- Permission modes, numbered approvals with diff preview, sessions/resume, `/init`, `/model` picker, print mode.
- **Personalization**: `/theme` — 5 prebuilt palettes (**python 黄蓝 default**, claude-dark / gruvbox / tokyo-night / paper-light) via Textual theme variables + live Rich recolor, persisted; `/notify` — desktop notifications (`term` OSC9 → iTerm/kitty/wezTerm, `mac` macOS 通知中心) on permission ask & turn completion.
- **Remote phone control (`/remote`)**: official cloud relay — scan the QR (or open the official mobile page zcode.z.ai/remote/v4) to take over this workspace from your phone, exactly like the GUI's pairing. Mobile actions sync via the relay into the TUI.

## Install & run

```bash
cd zcode-tui
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/ztui                 # interactive TUI
.venv/bin/ztui -p "explain this repo"
.venv/bin/ztui --model GLM-5.3-Flash
.venv/bin/ztui --resume ztui_xxxxxxxxxxxx
```

Keys: `Enter` send · `shift+enter`/`ctrl+j` newline · `esc` interrupt · `ctrl+o` expand · `ctrl+c`×2 quit.

## Architecture

```
zcode_tui/
├── zconfig.py     # read-only adapters for zcode config/setting/sqlite defaults
├── reasoning.py   # reasoning level -> request body patches (catalog + fallback)
├── providers/     # anthropic streaming client (+ openai-compat stub for M2)
├── agent/
│   ├── loop.py    # stream -> tool_use -> permission -> execute -> respond
│   ├── context.py # system prompt + AGENTS.md chain
│   ├── permission.py # plan/build/yolo + rules + dangerous-command list
│   ├── session.py # JSONL session store
│   └── tools/     # bash read write edit glob grep todo webfetch
└── ui/            # Textual app: transcript blocks, input dock, modals, status line
```

## Roadmap

功能对账已完成：zcode GUI 本地可实现的功能点全部实现（对账明细见 `docs/coverage.md`）。明确暂缓/边界（附书面结论）：workflow 执行/派生、checkpoint 云端内容恢复、会话分享、bots 集成、`~/.zcode/agents` 自定义 agent 加载。体验增强方向（任意后续批次）：Textual 主题扩充、Alt 键单词编辑、bots 只读状态展示。
