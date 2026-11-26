"""长期记忆（L1）：memory/ledger.md + memory/episodes/*.md。

- ledger.md：兼容史与适配模式的“浓缩版”，每次进入 triage/patch 的上下文，硬上限 ledger_max_chars；
- episodes/：每次运行一条 ≤300 字摘要，只取最近 N 条进上下文；
- 超限时由 distiller 阶段重写 ledger（压缩旧条目、保留模式与教训），而不是简单截断。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

from bot.util import ROOT

MEMORY_DIR = ROOT / "memory"
LEDGER = MEMORY_DIR / "ledger.md"
EPISODES = MEMORY_DIR / "episodes"
LESSONS_HEADER = "## 自动沉淀的经验"


def read_ledger() -> str:
    return LEDGER.read_text("utf-8") if LEDGER.exists() else ""


def ledger_over_limit(max_chars: int) -> bool:
    return len(read_ledger()) > max_chars


def append_lessons(lessons: List[str], run_id: str) -> None:
    lessons = [l.strip().lstrip("-• ").strip() for l in lessons if l and l.strip()]
    if not lessons:
        return
    text = read_ledger()
    if LESSONS_HEADER not in text:
        text = text.rstrip() + f"\n\n{LESSONS_HEADER}\n"
    existing = set(re.findall(r"^- (.+?)(?:（run [^）]+）)?$", text, flags=re.M))
    new_lines = [f"- {l}（run {run_id}）" for l in lessons if l not in existing]
    if new_lines:
        LEDGER.write_text(text.rstrip() + "\n" + "\n".join(new_lines) + "\n", "utf-8")


def rewrite_ledger(text: str) -> None:
    LEDGER.write_text(text.strip() + "\n", "utf-8")


def write_episode(run_id: str, text: str) -> Path:
    EPISODES.mkdir(parents=True, exist_ok=True)
    p = EPISODES / f"{run_id}.md"
    p.write_text(text.strip() + "\n", "utf-8")
    return p


def recent_episodes(n: int) -> str:
    if not EPISODES.exists() or n <= 0:
        return ""
    files = sorted(EPISODES.glob("*.md"))[-n:]
    return "\n\n".join(f"### {f.stem}\n{f.read_text('utf-8').strip()}" for f in files)
