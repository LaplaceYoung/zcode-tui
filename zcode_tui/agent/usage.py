"""Token usage accumulation for the status line and /cost."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    requests: int = 0
    history: list[dict] = field(default_factory=list)

    def add(self, usage: dict) -> None:
        inp = int(usage.get("input_tokens", 0) or 0)
        out = int(usage.get("output_tokens", 0) or 0)
        cr = int(usage.get("cache_read_input_tokens", 0) or 0)
        cw = int(usage.get("cache_creation_input_tokens", 0) or 0)
        if not (inp or out or cr or cw):
            return
        self.input += inp
        self.output += out
        self.cache_read += cr
        self.cache_write += cw
        self.requests += 1
        self.history.append(
            {"input": inp, "output": out, "cache_read": cr, "cache_write": cw}
        )

    def reset_window(self) -> None:
        self.__init__()

    def fmt(self) -> str:
        parts = [f"in {self.input + self.cache_write}", f"out {self.output}"]
        if self.cache_read:
            parts.append(f"cached {self.cache_read}")
        return " · ".join(parts)

    def report(self) -> str:
        lines = [
            f"requests:      {self.requests}",
            f"input tokens:  {self.input}",
            f"output tokens: {self.output}",
            f"cache read:    {self.cache_read}",
            f"cache write:   {self.cache_write}",
            f"total:         {self.input + self.output + self.cache_read + self.cache_write}",
        ]
        return "\n".join(lines)
