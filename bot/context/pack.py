"""ContextPack：一个阶段喂给 agent 的全部输入，按优先级装配，并落盘 manifest 便于追溯。

规则：
- budget=None（默认）：不截断，完整保留所有已筛选的 section，同时在 manifest.json 记录每段实际 token；
- budget 为整数时：按 priority（越小越重要）装箱，放不下的截断到剩余预算、耗尽的丢弃并在有 source 时留磁盘指针；
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
    budget: Optional[int] = None
    sections: List[Section] = field(default_factory=list)

    def add(self, title: str, body: str, priority: int = 5, source: Optional[str] = None, min_tokens: int = 200) -> "ContextPack":
        if body and body.strip():
            self.sections.append(Section(title, body.strip(), priority, source, min_tokens, len(self.sections)))
        return self

    def render(self, out_dir: Optional[Path] = None) -> str:
        remaining = self.budget
        used = 0
        kept: Dict[int, str] = {}
        manifest: List[Dict] = []
        for s in sorted(self.sections, key=lambda x: (x.priority, x.order)):
            cost = estimate_tokens(s.body) + estimate_tokens(s.title) + 4
            entry = {"title": s.title, "priority": s.priority, "tokens": cost, "source": s.source}
            if remaining is None or cost <= remaining:
                kept[s.order] = s.body
                used += cost
                if remaining is not None:
                    remaining -= cost
                entry["status"] = "full"
            elif remaining >= s.min_tokens:
                note = f"完整内容见 {s.source}" if s.source else ""
                body = truncate_to_tokens(s.body, remaining - 8, note)
                kept[s.order] = body
                kept_tok = estimate_tokens(body)
                used += kept_tok
                entry["status"] = "truncated"
                entry["kept_tokens"] = kept_tok
                remaining = 0
            else:
                entry["status"] = "dropped"
                if s.source:
                    kept[s.order] = f"（因预算省略，需要时读取 `{s.source}`）"
            manifest.append(entry)

        budget_label = "unlimited" if self.budget is None else str(self.budget)
        parts = [f"<!-- context-pack stage={self.stage} budget={budget_label} used={used} -->"]
        for s in self.sections:
            if s.order in kept:
                parts.append(f"## {s.title}\n\n{kept[s.order]}")
        text = "\n\n".join(parts) + "\n"
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "context.md").write_text(text, "utf-8")
            write_json(out_dir / "manifest.json", {"stage": self.stage, "budget": self.budget,
                                                   "used": used, "sections": manifest})
        return text
