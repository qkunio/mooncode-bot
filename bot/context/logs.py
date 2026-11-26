"""日志蒸馏：把几万行 xcodebuild 输出压成几 KB 的“错误摘要”，再回喂给 agent。

原始日志只落盘（runs/<id>/verify-*.log），永远不直接进上下文。
"""

from __future__ import annotations

import re
from typing import List

_ERR = re.compile(r"(^|\s)(error|fatal error):|\*\* (BUILD|ARCHIVE) FAILED \*\*|^FAIL|Assertion failed|precondition failed|Fatal error:", re.I)
_LOC = re.compile(r"^(/[^:]+|[A-Za-z0-9_./-]+\.swift):(\d+):(\d+): (error|warning): (.*)$")


def digest_build_log(log: str, max_chars: int = 6000, repo_root: str = "") -> str:
    lines = log.splitlines()
    seen = set()
    picked: List[str] = []
    for i, line in enumerate(lines):
        if not _ERR.search(line):
            continue
        m = _LOC.match(line.strip())
        if m:
            path = m.group(1)
            if repo_root and path.startswith(repo_root):
                path = path[len(repo_root):].lstrip("/")
            key = (path, m.group(2), m.group(5))
            if key in seen:
                continue
            seen.add(key)
            block = [f"{path}:{m.group(2)}:{m.group(3)}: {m.group(4)}: {m.group(5)}"]
            block += [l for l in lines[i + 1:i + 3] if l.strip() and not _LOC.match(l.strip())]
            picked.append("\n".join(block))
        else:
            key = line.strip()
            if key in seen:
                continue
            seen.add(key)
            picked.append(key)
    if not picked:
        tail = "\n".join(lines[-60:])
        return f"（未匹配到 error 行，以下为日志末尾 60 行）\n{tail}"[-max_chars:]
    out = f"共 {len(picked)} 条不重复错误：\n\n" + "\n\n".join(picked)
    if len(out) > max_chars:
        out = out[:max_chars] + f"\n…[其余错误省略，共 {len(picked)} 条]"
    return out


def head_tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + f"\n…[中间省略 {len(text) - max_chars} 字符]…\n" + text[-half:]
