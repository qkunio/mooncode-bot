"""Token 预算：粗估 + 按优先级装箱。"""

from __future__ import annotations

import re

_CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")


def estimate_tokens(text: str) -> int:
    """中文约 1 字 1 token，其余约 4 字符 1 token。偏保守即可，目的是控量而非计费。"""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    return cjk + (len(text) - cjk + 3) // 4


def truncate_to_tokens(text: str, tokens: int, note: str = "") -> str:
    if estimate_tokens(text) <= tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:  # 二分找到能放下的最长前缀
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= tokens:
            lo = mid
        else:
            hi = mid - 1
    cut = text.rfind("\n", 0, lo)
    cut = cut if cut > lo * 0.6 else lo
    dropped = len(text) - cut
    return text[:cut] + f"\n…[已截断 {dropped} 字符{('；' + note) if note else ''}]\n"
