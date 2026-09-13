"""Permission modes and rule-based gating for tool calls.

Modes (CC/OpenCode vocabulary):
  plan  — read-only: mutations are denied outright
  build — ask on first use of a tool/pattern, remember "always" decisions
  yolo  — auto-approve everything except an always-ask dangerous list
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from .tools import REGISTRY

ALLOW = "allow"
ASK = "ask"
DENY = "deny"

MODES = ("plan", "build", "yolo")

READONLY_COMMANDS = {
    "ls", "pwd", "cat", "head", "tail", "grep", "rg", "find", "echo",
    "which", "file", "stat", "wc", "sort", "uniq", "date", "uname", "id",
    "df", "du", "ps", "top", "env", "printenv", "hostname", "whoami",
    "git", "jq", "tree", "less", "more", "diff", "md5", "shasum", "sed",
}
GIT_MUTATING = {
    "add", "commit", "push", "pull", "merge", "rebase", "reset", "checkout",
    "switch", "restore", "rm", "mv", "tag", "stash", "apply", "cherry-pick",
}

# Commands that always require explicit approval, in every mode including yolo.
DANGEROUS_RES = [
    re.compile(p)
    for p in (
        r"\brm\s+(-[a-zA-Z]*f|-[a-zA-Z]*r|--force|--recursive)",
        r"\bsudo\b",
        r"\bmkfs\b",
        r"\bdd\b\s+.*of=/dev/",
        r">\s*/dev/sd",
        r"\bchmod\s+-R\b",
        r"\bchown\s+-R\b",
        r":\(\)\s*\{",
        r"\b(shutdown|reboot|halt|poweroff)\b",
        r"\.zcode/",  # never silently touch ZCode's own data directory
    )
]


@dataclass
class Decision:
    verdict: str  # ALLOW | ASK | DENY
    reason: str = ""


def _bash_is_readonly(command: str) -> bool:
    parts = [seg.strip() for seg in re.split(r"[;&|]+", command) if seg.strip()]
    for seg in parts:
        if ">" in seg or re.search(r"\btee\b", seg):
            return False
        tokens = seg.split()
        if not tokens:
            continue
        cmd = tokens[0].lstrip("sudo ").split("/")[-1]
        if cmd not in READONLY_COMMANDS:
            return False
        if cmd == "git" and len(tokens) > 1 and tokens[1] in GIT_MUTATING:
            return False
        if cmd in ("sed",) and "-i" in tokens:
            return False
        if cmd == "find" and ("-delete" in tokens or "-exec" in tokens):
            return False
    return True


def _bash_is_dangerous(command: str) -> str | None:
    for rx in DANGEROUS_RES:
        if rx.search(command):
            return rx.pattern
    return None


def _match_rule(rules: list[dict], tool: str, pattern: str) -> str | None:
    for rule in rules:
        if rule.get("tool") != tool:
            continue
        if fnmatch.fnmatch(pattern, rule.get("pattern", "*")):
            return rule.get("decision", ALLOW)
    return None


def pattern_for(tool: str, args: dict) -> str:
    """The pattern a rule would match for this call (also shown in 'always' prompts)."""
    if tool == "bash":
        return args.get("command", "")
    return args.get("path") or args.get("url") or args.get("pattern") or "*"


def check(tool: str, args: dict, mode: str, rules: list[dict]) -> Decision:
    spec = REGISTRY.get(tool)
    if spec is None:
        return Decision(DENY, f"unknown tool {tool}")

    if tool == "bash":
        danger = _bash_is_dangerous(args.get("command", ""))
        if danger:
            return Decision(ASK, "dangerous command — always requires approval")

    pattern = pattern_for(tool, args)
    verdict = _match_rule(rules, tool, pattern)
    if verdict:
        return Decision(verdict, "rule match")

    if mode == "plan":
        if spec.mutating:
            return Decision(DENY, "plan mode: mutation denied (switch to build with Tab)")
        if tool == "bash" and not _bash_is_readonly(args.get("command", "")):
            return Decision(DENY, "plan mode: only read-only bash allowed")
        return Decision(ALLOW)

    if mode == "yolo":
        return Decision(ALLOW)

    # build mode: read-only things flow, anything else asks.
    if tool == "bash":
        return Decision(ASK if not _bash_is_readonly(args.get("command", "")) else ALLOW)
    return Decision(ASK if spec.mutating or tool == "webfetch" else ALLOW)
