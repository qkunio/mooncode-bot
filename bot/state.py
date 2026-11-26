"""持久化状态（state/state.json）。由 bot 自己 commit 回仓库，保证跨次运行可追溯。

两个游标：
- seen_sha   最近一次检查过的上游 main HEAD；
- compat_sha 已确认 MoonCode 兼容的上游提交（契约基线）。检测永远 diff compat_sha → HEAD，
             所以多次小改动会累积到同一次适配里，不会漏。
"""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator

from bot.util import ROOT, now_iso, read_json, write_json

STATE_PATH = ROOT / "state" / "state.json"
HISTORY_LIMIT = 40

DEFAULT: Dict[str, Any] = {
    "upstream": {"seen_sha": None, "compat_sha": None},
    "inflight": None,
    "last_release": None,
    "history": [],
}


def load() -> Dict[str, Any]:
    data = read_json(STATE_PATH, None) or {}
    merged = {**DEFAULT, **data}
    merged["upstream"] = {**DEFAULT["upstream"], **(data.get("upstream") or {})}
    return merged


def save(state: Dict[str, Any]) -> None:
    state["history"] = state.get("history", [])[-HISTORY_LIMIT:]
    state["updated_at"] = now_iso()
    write_json(STATE_PATH, state)


def record(state: Dict[str, Any], run_id: str, outcome: str, **extra: Any) -> None:
    state.setdefault("history", []).append({"run": run_id, "at": now_iso(), "outcome": outcome, **extra})


@contextmanager
def lock(path: Path = ROOT / "state" / ".lock") -> Iterator[None]:
    """本地 launchd 与手动运行并发时互斥；CI 侧另有 concurrency group。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another mooncode-bot run is in progress")
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
