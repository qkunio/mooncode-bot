"""ContextPack：一个阶段喂给 agent 的全部输入，按优先级 + 预算装配，并落盘 manifest 便于追溯。

规则：
- 每个 section 有 priority（越小越重要）和可选 min_tokens（被截断时至少保留的量）；
- 先按优先级把放得下的整段放进去；放不下的截断到剩余预算；预算耗尽的直接丢弃并在 manifest 里记录；
- 被截断/丢弃的段若有 source（磁盘路径），在包里留指针，agent 需要时自己去读；
- 渲染顺序 = 添加顺序（保证阅读顺序自然），装箱顺序 = 优先级。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from bot.context.budget import estimate_tokens, truncate_to_tokens
from bot.util import write_json


@dataclass
class Section:
    title: str
    body: str
    priority: int = 5
    source: Optional[str] = None
    min_tokens: int = 200
    order: int = 0


@dataclass
class ContextPack:
    stage: str
    budget: int
    sections: List[Section] = field(default_factory=list)

    def add(self, title: str, body: str, priority: int = 5, source: Optional[str] = None, min_tokens: int = 200) -> "ContextPack":
        if body and body.strip():
            self.sections.append(Section(title, body.strip(), priority, source, min_tokens, len(self.sections)))
        return self

    def render(self, out_dir: Optional[Path] = None) -> str:
        remaining = self.budget
        kept: Dict[int, str] = {}
        manifest: List[Dict] = []
        for s in sorted(self.sections, key=lambda x: (x.priority, x.order)):
            cost = estimate_tokens(s.body) + estimate_tokens(s.title) + 4
            entry = {"title": s.title, "priority": s.priority, "tokens": cost, "source": s.source}
            if cost <= remaining:
                kept[s.order] = s.body
                remaining -= cost
                entry["status"] = "full"
            elif remaining >= s.min_tokens:
                note = f"完整内容见 {s.source}" if s.source else ""
                body = truncate_to_tokens(s.body, remaining - 8, note)
                kept[s.order] = body
                entry["status"] = "truncated"
                entry["kept_tokens"] = estimate_tokens(body)
                remaining = 0
            else:
                entry["status"] = "dropped"
                if s.source:
                    kept[s.order] = f"（因预算省略，需要时读取 `{s.source}`）"
            manifest.append(entry)

        parts = [f"<!-- context-pack stage={self.stage} budget={self.budget} used={self.budget - remaining} -->"]
        for s in self.sections:
            if s.order in kept:
                parts.append(f"## {s.title}\n\n{kept[s.order]}")
        text = "\n\n".join(parts) + "\n"
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "context.md").write_text(text, "utf-8")
            write_json(out_dir / "manifest.json", {"stage": self.stage, "budget": self.budget,
                                                   "used": self.budget - remaining, "sections": manifest})
        return text
