"""KAP 契约差异分析。

输入是上游 `packages/kap-server/schema/contract.txt`：每行一个字段，形如
    POST /api/v1/sessions/{session_id}:undo request.turn_id: string minLength=1
    ws server message item_delta.delta: string
    shape 66b4a738.code: enum 0
本模块把两版契约做逐行 diff，归到“操作”（REST 路由 / WS 消息 / shape），
再按 MoonCode 实际使用的接口面（surface）过滤并定级。整个检测阶段不需要 LLM。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
SEVERITY_ORDER = {"breaking": 0, "likely_breaking": 1, "additive": 2, "ignored": 3}


def parse(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(": ")
        if not sep:  # 形如 "GET /x: operation" 末尾无空格的情况
            if line.endswith(":"):
                key, value = line[:-1], ""
            else:
                key, value = line, ""
        out[key] = value
    return out


def norm_path(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def op_of(key: str) -> str:
    """字段行 → 所属操作的规范名（路径参数统一成 {}）。"""
    parts = key.split(" ")
    if parts[0] in METHODS and len(parts) >= 2:
        return f"{parts[0]} {norm_path(parts[1])}"
    if parts[0] == "ws" and len(parts) >= 4:
        m = re.match(r"[A-Za-z0-9_]+", parts[3])
        return f"ws {parts[1]} {m.group(0) if m else parts[3]}"
    if parts[0] == "shape" and len(parts) >= 2:
        return f"shape {parts[1].split('.')[0]}"
    return key


def direction(key: str) -> str:
    if key.startswith("ws client"):
        return "request"
    if key.startswith("ws server"):
        return "response"
    if " param " in key or " request" in key:
        return "request"
    if " response" in key:
        return "response"
    if key.startswith("shape "):
        return "shape"
    return "operation"


@dataclass
class Change:
    kind: str  # added | removed | changed
    key: str
    op: str
    old: Optional[str] = None
    new: Optional[str] = None
    severity: str = "ignored"
    consumed: bool = False
    reason: str = ""

    def line(self) -> str:
        if self.kind == "added":
            return f"+ {self.key}: {self.new}"
        if self.kind == "removed":
            return f"- {self.key}: {self.old}"
        return f"~ {self.key}: {self.old}  →  {self.new}"


def diff(old: Dict[str, str], new: Dict[str, str]) -> List[Change]:
    changes: List[Change] = []
    for k in sorted(set(old) | set(new)):
        if k not in new:
            changes.append(Change("removed", k, op_of(k), old=old[k]))
        elif k not in old:
            changes.append(Change("added", k, op_of(k), new=new[k]))
        elif old[k] != new[k]:
            changes.append(Change("changed", k, op_of(k), old=old[k], new=new[k]))
    return changes


def _is_optional(value: Optional[str]) -> bool:
    return bool(value) and (" optional" in value or value.endswith("optional") or "nullable" in value)


_TAIL = re.compile(r"(\.[^.<\[]+|\[\]|<[^>]*>)$")


def ancestors(key: str) -> List[str]:
    """request.content[]<text>.text → [request.content[]<text>, request.content[], request.content, request]"""
    out = []
    cur = key
    while True:
        nxt = _TAIL.sub("", cur)
        if nxt == cur or not nxt:
            return out
        out.append(nxt)
        cur = nxt


def grade(ch: Change, old: Optional[Dict[str, str]] = None, new: Optional[Dict[str, str]] = None) -> None:
    """对单条变更定级（在已确认“被 MoonCode 使用”之后调用）。"""
    old, new = old or {}, new or {}
    d = direction(ch.key)
    is_op_line = ch.old == "operation" or ch.new == "operation"
    if is_op_line and ch.kind == "removed":
        ch.severity, ch.reason = "breaking", "接口/消息被删除"
        return
    if d == "request":
        if ch.kind == "added" and not _is_optional(ch.new):
            anc = ancestors(ch.key)
            parent_existed = bool(anc) and anc[0] in old
            optional_anc = any(_is_optional(new.get(a)) for a in anc)
            new_variant = any(a.endswith(">") and a not in old for a in [ch.key] + anc)
            if " param " in ch.key and not anc:
                parent_existed = True  # 顶层 query/path 参数
            if parent_existed and not optional_anc and not new_variant:
                ch.severity, ch.reason = "breaking", "新增必填请求字段"
            else:
                ch.severity, ch.reason = "additive", "新增请求字段（所在对象可选或为新变体）"
        elif ch.kind == "added":
            ch.severity, ch.reason = "additive", "新增可选请求字段"
        elif ch.kind == "removed":
            ch.severity, ch.reason = "likely_breaking", "请求字段被删除（closed 对象会拒绝多余字段）"
        else:
            ch.severity, ch.reason = "likely_breaking", "请求字段类型/约束变化"
        return
    if d in ("response", "operation", "shape"):
        if ch.kind == "added":
            if "enum" in (ch.new or "") or "const" in (ch.new or ""):
                ch.severity, ch.reason = "additive", "新增响应字段（含枚举，需确认解码容错）"
            else:
                ch.severity, ch.reason = "additive", "新增响应字段"
        elif ch.kind == "removed":
            ch.severity, ch.reason = "likely_breaking", "响应字段被删除"
        else:
            old, new = ch.old or "", ch.new or ""
            if "enum" in old and "enum" in new:
                ch.severity, ch.reason = "likely_breaking", "枚举取值变化（Swift 严格枚举会解码失败）"
            elif _is_optional(new) and not _is_optional(old):
                ch.severity, ch.reason = "likely_breaking", "响应字段变为可选/可空"
            else:
                ch.severity, ch.reason = "likely_breaking", "响应字段类型/结构变化"


def shape_refs(contract: Dict[str, str], ops: Set[str]) -> Set[str]:
    """被某些操作直接或间接引用的 shape（闭包）。"""
    refs: Set[str] = set()
    frontier = [v for k, v in contract.items() if op_of(k) in ops]
    while frontier:
        nxt = []
        for v in frontier:
            for h in re.findall(r"shape ([0-9a-f]{6,})", v or ""):
                if h not in refs:
                    refs.add(h)
                    nxt.extend(val for k, val in contract.items() if k.startswith(f"shape {h}"))
        frontier = nxt
    return refs


def route_match(contract_path: str, used_path: str) -> bool:
    """逐段匹配：契约的 {} 可匹配 MoonCode 里写死的字面量段（如 agents/main/turns）。"""
    a, b = contract_path.split("/"), used_path.split("/")
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x == y:
            continue
        # {} 只匹配不带 :action 后缀的段，避免 sessions/{} 把 sessions/{}:fork 也算进来
        if (x == "{}" and ":" not in y) or (y == "{}" and ":" not in x):
            continue
        # 带 action 后缀的段：{}:undo ↔ {}:undo
        if ":" in x and ":" in y and x.split(":", 1)[1] == y.split(":", 1)[1] and "{}" in (x.split(":")[0], y.split(":")[0]):
            continue
        return False
    return True


def surface_match(op: str, surface_ops: Set[str]) -> bool:
    if op in surface_ops:
        return True
    parts = op.split(" ", 1)
    if parts[0] in METHODS:
        for s in surface_ops:
            sp = s.split(" ", 1)
            if len(sp) == 2 and sp[0] in (parts[0], "*") and route_match(parts[1], sp[1]):
                return True
        return False
    if op.startswith("ws "):
        _, side, name = op.split(" ", 2)
        return f"ws {side} {name}" in surface_ops or f"ws * {name}" in surface_ops
    return False


@dataclass
class Report:
    base_sha: str
    head_sha: str
    total_changes: int = 0
    consumed: List[Change] = field(default_factory=list)
    ignored_ops: List[str] = field(default_factory=list)
    contract_unchanged: bool = False

    @property
    def max_severity(self) -> str:
        if not self.consumed:
            return "ignored"
        return min((c.severity for c in self.consumed), key=lambda s: SEVERITY_ORDER[s])

    @property
    def needs_adaptation(self) -> bool:
        return self.max_severity in ("breaking", "likely_breaking")

    def to_dict(self) -> dict:
        return {
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "contract_unchanged": self.contract_unchanged,
            "total_changes": self.total_changes,
            "max_severity": self.max_severity,
            "needs_adaptation": self.needs_adaptation,
            "consumed": [asdict(c) for c in self.consumed],
            "ignored_ops": self.ignored_ops,
        }


_SHAPE_REF = re.compile(r"^shape ([0-9a-f]{6,})(.*)$")


def _shape_map(contract: Dict[str, str], h: str) -> Dict[str, str]:
    prefix = f"shape {h}"
    return {k[len(prefix):]: v for k, v in contract.items() if k.startswith(prefix)}


def shape_delta(old: Dict[str, str], new: Dict[str, str], a: str, b: str, depth: int = 0) -> List[Tuple[str, str, Optional[str], Optional[str]]]:
    """shape a（旧契约）→ shape b（新契约）的字段级差异；嵌套 shape 引用递归展开。"""
    fa, fb = _shape_map(old, a), _shape_map(new, b)
    out: List[Tuple[str, str, Optional[str], Optional[str]]] = []
    for k in sorted(set(fa) | set(fb)):
        va, vb = fa.get(k), fb.get(k)
        if va == vb:
            continue
        ma, mb = _SHAPE_REF.match(va or ""), _SHAPE_REF.match(vb or "")
        if ma and mb and ma.group(2) == mb.group(2) and depth < 4:
            out.extend((kind, k + sub, o, n) for kind, sub, o, n in shape_delta(old, new, ma.group(1), mb.group(1), depth + 1))
        elif va is None:
            out.append(("added", k, None, vb))
        elif vb is None:
            out.append(("removed", k, va, None))
        else:
            out.append(("changed", k, va, vb))
    return out


def grade_shape_swap(ch: Change, old: Dict[str, str], new: Dict[str, str]) -> bool:
    """引用从 shape A 换成 shape B 时，按 A→B 的真实字段差异定级。返回是否已处理。"""
    ma, mb = _SHAPE_REF.match(ch.old or ""), _SHAPE_REF.match(ch.new or "")
    if not (ch.kind == "changed" and ma and mb and ma.group(2) == mb.group(2)):
        return False
    delta = shape_delta(old, new, ma.group(1), mb.group(1))
    worst, notes = "additive", []
    for kind, sub, o, n in delta:
        sub_ch = Change(kind, ch.key + sub, ch.op, old=o, new=n)
        grade(sub_ch, old, new)
        if SEVERITY_ORDER[sub_ch.severity] < SEVERITY_ORDER[worst]:
            worst = sub_ch.severity
        sym = {"added": "+", "removed": "-", "changed": "~"}[kind]
        notes.append(f"{sym}{sub or '(self)'}" + (f" {o}→{n}" if kind == "changed" else f" {n or o}"))
    ch.severity = worst
    shown = "；".join(notes[:8]) + ("…" if len(notes) > 8 else "")
    ch.reason = ("结构等价扩展：" if worst == "additive" else "结构变化：") + (shown or "仅 shape 重新编号")
    return True


def _kind_of(op: str) -> str:
    """ws server xxx → 'ws server'；POST /api/v1/sessions/{}:cancel → '/api/v1/sessions'"""
    parts = op.split(" ")
    if parts[0] == "ws":
        return " ".join(parts[:2])
    if parts[0] in METHODS and len(parts) > 1:
        return "/".join(parts[1].split("/")[:4]).split(":")[0].replace("/{}", "")
    return parts[0]


def analyze(old_text: str, new_text: str, surface_ops: Iterable[str], base_sha: str = "", head_sha: str = "") -> Report:
    ops = set(surface_ops)
    old, new = parse(old_text), parse(new_text)
    rep = Report(base_sha, head_sha)
    if old == new:
        rep.contract_unchanged = True
        return rep
    changes = diff(old, new)
    rep.total_changes = len(changes)
    all_ops = {op_of(k) for k in list(old) + list(new)}
    hit_cache = {o: surface_match(o, ops) for o in all_ops}
    consumed_ops = {o for o, hit in hit_cache.items() if hit}
    used_shapes = shape_refs(old, consumed_ops) | shape_refs(new, consumed_ops)
    ignored: Set[str] = set()
    old_ops = {op_of(k) for k in old}
    new_ops = {op_of(k) for k in new}
    collapsed: Dict[str, int] = {}
    representatives: Dict[str, Change] = {}
    for ch in changes:
        if ch.op.startswith("shape "):
            h = ch.op.split(" ", 1)[1]
            # 整个 shape 新增/消失 = 重新编号，已由引用处的 grade_shape_swap 覆盖
            if h not in used_shapes or not (_shape_map(old, h) and _shape_map(new, h)):
                if h not in used_shapes:
                    ignored.add(ch.op)
                continue
            hit = True
        else:
            hit = hit_cache.get(ch.op, False)
        if not hit:
            ignored.add(ch.op)
            continue
        # 整个操作新增 / 删除：折叠成一行，避免几十行字段级噪音淹没真正的问题
        if not ch.op.startswith("shape ") and (ch.op not in old_ops or ch.op not in new_ops):
            collapsed[ch.op] = collapsed.get(ch.op, 0) + 1
            is_op_line = ch.old == "operation" or ch.new == "operation"
            rep_existing = representatives.get(ch.op)
            if rep_existing is not None and not is_op_line:
                continue
            ch.consumed = True
            if ch.kind == "removed":
                ch.severity, ch.reason = "breaking", "接口/消息被删除"
            else:
                ch.severity, ch.reason = "additive", "新增接口/消息"
            if rep_existing is not None:
                rep.consumed.remove(rep_existing)
            representatives[ch.op] = ch
            rep.consumed.append(ch)
            continue
        ch.consumed = True
        if not grade_shape_swap(ch, old, new):
            grade(ch, old, new)
        rep.consumed.append(ch)
    for ch in rep.consumed:
        if ch.op in collapsed:
            ch.reason += f"（含 {collapsed[ch.op]} 行字段，已折叠）"
    # 改名/替代候选：被使用的接口消失时，把同类的全新接口一并呈现（例如 context 帧改名为 ctx）
    removed_kinds = {_kind_of(c.op) for c in rep.consumed if c.severity == "breaking" and c.kind == "removed"}
    if removed_kinds:
        for op in sorted((new_ops - old_ops) - consumed_ops):
            if op.startswith("shape ") or _kind_of(op) not in removed_kinds:
                continue
            first = next((k for k in sorted(new) if op_of(k) == op), None)
            if first is None:
                continue
            cand = Change("added", first, op, new=new[first], severity="additive", consumed=True,
                          reason="新增（可能是被删除接口的替代/改名，请核对）")
            rep.consumed.append(cand)
            ignored.discard(op)
    rep.consumed.sort(key=lambda c: (SEVERITY_ORDER[c.severity], c.op, c.key))
    rep.ignored_ops = sorted(ignored)
    return rep


def shape_fields(contract: Dict[str, str], h: str) -> List[str]:
    prefix = f"shape {h}"
    return [f"{k[len(prefix):] or '(self)'}: {v}" for k, v in sorted(contract.items()) if k.startswith(prefix)]
