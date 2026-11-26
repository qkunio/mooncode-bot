"""MoonCode 接口面（surface）扫描：MoonCode 到底用了上游哪些 REST 路由和 WS 消息。

产物 memory/surface.json 有两个用途：
1. 检测阶段过滤契约 diff —— 只关心 MoonCode 真正依赖的部分；
2. 上下文“指针”—— 每个操作记下 Swift 源码位置（file:line），喂给 agent 时只给指针不给全文，
   让 agent 按需读文件，而不是把 2 万行 Swift 一股脑塞进上下文。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Set

from bot.contract import METHODS, norm_path

CALL_RE = re.compile(
    r"\b(get|post|put|patch|delete|send|request|decode)\s*\(\s*"
    r"(?:method:\s*\"(?P<m>[A-Z]+)\"\s*,\s*)?(?:path:\s*)?\"(?P<p>[^\"\n]+)\""
)
APPEND_RE = re.compile(r"appending\(path:\s*\"(?P<p>[^\"\n]+)\"\)")
WS_CASE_RE = re.compile(r"case\s+((?:\"[a-z_.]+\"\s*,?\s*)+)\s*:")
WS_TYPE_RE = re.compile(r"\"type\"\s*:\s*\"(?P<t>[a-z_.]+)\"|type:\s*\"(?P<t2>[a-z_.]+)\"")


def _swift_path_to_route(p: str) -> str:
    p = re.sub(r"\\\([^)]*\)", "{}", p)  # \(sessionID) → {}
    p = p.lstrip("/")
    if p.startswith("api/v1/"):
        p = p[len("api/v1/"):]
    return "/api/v1/" + p


def scan(repo_dir: Path, globs: Iterable[str], extra: Iterable[str] = (), ignore: Iterable[str] = ()) -> Dict:
    ops: Dict[str, List[str]] = {}
    ws_names: Dict[str, List[str]] = {}

    def add(store: Dict[str, List[str]], key: str, where: str) -> None:
        lst = store.setdefault(key, [])
        if where not in lst and len(lst) < 6:
            lst.append(where)

    files: List[Path] = []
    for g in globs:
        files.extend(sorted(repo_dir.glob(g)))
    for f in files:
        rel = f.relative_to(repo_dir).as_posix()
        if "Demo" in f.name:  # 体验模式的本地假后端不依赖上游
            continue
        text = f.read_text("utf-8", errors="replace")

        def at(pos: int) -> str:
            return f"{rel}:{text.count(chr(10), 0, pos) + 1}"

        # 全文匹配：调用常跨行，如 post(\n    "sessions/\(id)/profile", body: …)
        for m in CALL_RE.finditer(text):
            path = m.group("p")
            if not re.match(r"^/?[a-z_]", path) or " " in path:
                continue
            route = _swift_path_to_route(path)
            if route.startswith("/api/v1/v1/"):  # relay 自身的 API（/v1/remote/...），不属于 KAP
                continue
            method = (m.group("m") or m.group(1)).upper()
            method = method if method in METHODS else "*"
            add(ops, f"{method} {norm_path(route)}", at(m.start("p")))
        for m in APPEND_RE.finditer(text):
            path = m.group("p")
            if path in ("api/v1", "ws") or path.startswith(("devices/", "v1/")) or "MoonCode/" in path:
                continue
            add(ops, f"* {norm_path(_swift_path_to_route(path))}", at(m.start("p")))
        for m in WS_CASE_RE.finditer(text):
            for name in re.findall(r"\"([a-z_.]+)\"", m.group(1)):
                add(ws_names, name, at(m.start()))
        for m in WS_TYPE_RE.finditer(text):
            add(ws_names, m.group("t") or m.group("t2"), at(m.start()))

    surface_ops: Dict[str, List[str]] = dict(ops)
    for name, where in ws_names.items():
        # 线上帧名 item.delta ↔ 契约名 item_delta；turn.started ↔ turn
        surface_ops.setdefault(f"ws * {name.replace('.', '_')}", []).extend(where)
        if "." in name:
            surface_ops.setdefault(f"ws * {name.split('.')[0]}", []).extend(where)
    for e in extra:
        surface_ops.setdefault(e, []).append("config:surface_extra")
    for ig in ignore:
        surface_ops.pop(ig, None)
    return {"ops": {k: sorted(set(v))[:6] for k, v in sorted(surface_ops.items())}}


def ops_set(surface: Dict) -> Set[str]:
    return set(surface.get("ops", {}).keys())


def pointers_for(surface: Dict, ops: Iterable[str]) -> Dict[str, List[str]]:
    """给定契约操作名，返回 MoonCode 中对应的源码位置。"""
    from bot.contract import route_match

    table = surface.get("ops", {})
    out: Dict[str, List[str]] = {}
    for op in ops:
        hits: List[str] = []
        parts = op.split(" ", 2)
        if parts[0] in METHODS:
            for key, where in table.items():
                kp = key.split(" ", 1)
                if len(kp) == 2 and kp[0] in (parts[0], "*") and route_match(parts[1], kp[1]):
                    hits.extend(where)
        elif parts[0] == "ws":
            hits.extend(table.get(op, []))
            hits.extend(table.get(f"ws * {parts[2]}", []))
        else:
            hits.extend(table.get(op, []))
        if hits:
            out[op] = sorted(set(hits))
    return out
