"""调度主流程：detect → triage → patch ⇄ verify → review → ship → release → distill。

每个 AI 阶段都是一次独立的 kimi 会话，输入是编排器按预算拼好的 ContextPack，
输出是一个结构化 handoff JSON。阶段之间只传 handoff，不传对话记录。
"""

from __future__ import annotations

import difflib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bot import contract, github, state as st, surface as sf, target as tg
from bot.context import memory
from bot.context.logs import digest_build_log, head_tail
from bot.context.pack import ContextPack
from bot.kimi import read_handoff, run_kimi
from bot.util import ROOT, armed, cfg, log, now_iso, sh, sh_shell, write_json

SURFACE_PATH = ROOT / "memory" / "surface.json"
RUNS = ROOT / "runs"


@dataclass
class Run:
    conf: Dict[str, Any]
    id: str
    state: Dict[str, Any]
    dry_run: bool
    label: str = ""

    @property
    def dir(self) -> Path:
        return RUNS / self.id

    def sdir(self, stage: str) -> Path:
        return self.dir / stage

    @property
    def side_effects(self) -> bool:
        return (not self.dry_run) and armed(self.conf)

    def budget(self, stage: str) -> int:
        return int(cfg(self.conf, f"context.budget_tokens.{stage}", 20000))

    def ci_link(self) -> str:
        if os.environ.get("GITHUB_RUN_ID"):
            return f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        return f"local run {self.id}"


# ───────────────────────────── detect（纯确定性，无 LLM） ─────────────────────────────

def refresh_surface(conf: Dict[str, Any], wd: Path) -> Dict[str, Any]:
    data = sf.scan(wd, cfg(conf, "target.surface_globs", []), cfg(conf, "target.surface_extra", []),
                   cfg(conf, "target.surface_ignore", []))
    write_json(SURFACE_PATH, data)
    return data


def detect(conf: Dict[str, Any], surface: Dict[str, Any], base_sha: str, head_sha: str) -> contract.Report:
    up = conf["upstream"]
    old = github.file_at(up["repo"], up["contract_path"], base_sha) or ""
    new = github.file_at(up["repo"], up["contract_path"], head_sha) or ""
    if not new:
        raise RuntimeError(f"上游 {head_sha[:8]} 找不到 {up['contract_path']}，契约位置可能变了")
    if not old:
        raise RuntimeError(f"基线 {base_sha[:8]} 上没有 {up['contract_path']}（契约文件 2026-11-20 才引入），"
                           f"请用 `python3 -m bot seed <ref>` 把基线设到之后的提交")
    return contract.analyze(old, new, sf.ops_set(surface), base_sha, head_sha)


def render_delta(rep: contract.Report, surface: Dict[str, Any], ops_filter: Optional[set] = None) -> str:
    """按操作分组渲染被消费的契约变更，并附上 MoonCode 源码指针。"""
    groups: Dict[str, List[contract.Change]] = {}
    for c in rep.consumed:
        if ops_filter is None or c.op in ops_filter:
            groups.setdefault(c.op, []).append(c)
    ptrs = sf.pointers_for(surface, groups.keys())
    out = []
    for op, items in sorted(groups.items(), key=lambda kv: min(contract.SEVERITY_ORDER[c.severity] for c in kv[1])):
        sev = min((c.severity for c in items), key=lambda s: contract.SEVERITY_ORDER[s])
        out.append(f"### `{op}`  [{sev}]")
        for c in items[:40]:
            out.append(f"- `{c.line()}` — {c.reason}")
        if len(items) > 40:
            out.append(f"- …另有 {len(items) - 40} 行")
        if op in ptrs:
            out.append("  MoonCode 位置：" + ", ".join(ptrs[op]))
        out.append("")
    return "\n".join(out)


def render_shapes(rep: contract.Report, old_text: str, new_text: str, limit: int = 8) -> str:
    """对“引用的 shape 换了”这类变更，展开新旧 shape 的字段，避免 agent 只看到两个哈希。"""
    import re
    old, new = contract.parse(old_text), contract.parse(new_text)
    pairs = []
    for c in rep.consumed:
        if c.kind == "changed":
            a = re.findall(r"shape ([0-9a-f]{6,})", c.old or "")
            b = re.findall(r"shape ([0-9a-f]{6,})", c.new or "")
            if a and b and a != b:
                pairs.append((c.key, a[0], b[0]))
    out = []
    for key, a, b in pairs[:limit]:
        fa, fb = contract.shape_fields(old, a), contract.shape_fields(new, b)
        d = "\n".join(difflib.unified_diff(fa, fb, f"shape {a}", f"shape {b}", lineterm="", n=1))
        out.append(f"### {key}\n```diff\n{d}\n```")
    return "\n\n".join(out)


def upstream_intents(conf: Dict[str, Any], base: str, head: str) -> str:
    up = conf["upstream"]
    commits = github.commits_touching(up["repo"], up["contract_path"], base, head, int(up.get("max_intent_commits", 12)))
    out = []
    for c in commits:
        pr = github.pr_intent(up["repo"], c["message"])
        title = c["message"].splitlines()[0]
        if pr:
            out.append(f"- **{title}** ({pr['url']})\n  {pr['body'].replace(chr(10), ' ')[:600]}")
        else:
            out.append(f"- **{title}** ({c['sha'][:8]})")
    return "\n".join(out)


def kap_doc_diff(conf: Dict[str, Any], base: str, head: str, max_chars: int = 5000) -> str:
    up = conf["upstream"]
    if github.blob_sha(up["repo"], up["kap_doc_path"], base) == github.blob_sha(up["repo"], up["kap_doc_path"], head):
        return ""
    a = (github.file_at(up["repo"], up["kap_doc_path"], base) or "").splitlines()
    b = (github.file_at(up["repo"], up["kap_doc_path"], head) or "").splitlines()
    return head_tail("\n".join(difflib.unified_diff(a, b, "kap.md@base", "kap.md@head", lineterm="", n=2)), max_chars)


# ───────────────────────────── AI 阶段 ─────────────────────────────

def _common_memory(run: Run, pack: ContextPack, ledger_priority: int = 3) -> None:
    pack.add("长期记忆：兼容史与适配模式（memory/ledger.md）", memory.read_ledger(), ledger_priority,
             source=str(memory.LEDGER))
    pack.add("最近几次运行摘要", memory.recent_episodes(int(cfg(run.conf, "context.episodes_in_context", 3))), 7,
             source=str(memory.EPISODES))


def triage(run: Run, rep: contract.Report, wd: Path, surface: Dict, extras: Dict[str, str]) -> Dict[str, Any]:
    sd = run.sdir("triage")
    handoff = sd / "triage.json"
    pack = ContextPack("triage", run.budget("triage"))
    pack.add("任务", f"""上游 Kimi Code 发布了 {run.label}，KAP 契约发生变化（{rep.base_sha[:8]} → {rep.head_sha[:8]}）。
下面是**已按 MoonCode 实际使用的接口面过滤并初步定级**的契约差异。请在当前目录（MoonCode 仓库）里
对照源码逐条确认：MoonCode 现有代码在新版服务端上是否会出错（解码失败、请求被拒、功能静默失效）。

只读分析，不要修改任何文件。把结论写入 `{handoff}`（JSON，字段见系统提示词中的 triage handoff 规范）。""", 0)
    pack.add("契约差异（已过滤到 MoonCode 使用面）", render_delta(rep, surface), 1, min_tokens=2000,
             source=str(run.sdir("detect") / "detect.json"))
    pack.add("上游变更意图（相关 PR）", extras.get("intents", ""), 2)
    pack.add("变更涉及的 shape 展开", extras.get("shapes", ""), 4)
    pack.add("KAP 协议指南 kap.md 的变化", extras.get("kap_doc", ""), 5)
    _common_memory(run, pack)
    pack.add("未被 MoonCode 使用、已忽略的变更操作", "\n".join(f"- {o}" for o in rep.ignored_ops), 9, min_tokens=400)
    res = run_kimi(pack.render(sd), cwd=wd, stage_dir=sd, conf=run.conf, agent="triage", tag="triage")
    _assert_readonly(wd, "triage")
    return read_handoff(handoff, res, required=("incompatible", "items"))


def _assert_readonly(wd: Path, stage: str, baseline: str = "") -> None:
    """只读阶段的确定性护栏：agent 若改了仓库，直接回滚并判失败。"""
    now = tg.git(wd, "status", "--porcelain", check=False)
    if now != baseline:
        tg.git(wd, "checkout", "--", ".", check=False)
        raise RuntimeError(f"{stage} 阶段是只读的，但工作区被修改了：\n{now[:800]}")


def _verify(run: Run, wd: Path, round_no: int) -> Tuple[bool, str]:
    log_path = run.dir / f"verify-{round_no}.log"
    chunks = []
    ok = True
    for cmd in cfg(run.conf, "verify.commands", []):
        log(f"verify ▶ {cmd[:80]}")
        proc = sh_shell(cmd, wd, timeout=int(cfg(run.conf, "verify.timeout_sec", 1800)))
        chunks.append(f"$ {cmd}\n{proc.stdout}\n[exit {proc.returncode}]")
        if proc.returncode != 0:
            ok = False
            break
    full = "\n".join(chunks)
    log_path.write_text(full, "utf-8")
    log(f"verify ◀ {'PASS' if ok else 'FAIL'} ({log_path.name})")
    if ok:
        return True, ""
    return False, digest_build_log(full, int(cfg(run.conf, "context.log_digest_max_chars", 6000)), str(wd))


def patch(run: Run, rep: contract.Report, tri: Dict[str, Any], wd: Path, surface: Dict) -> Tuple[Dict[str, Any], str, int]:
    """生成补丁 + 构建验证闭环。返回 (patch handoff, session_id, 修复轮数)。"""
    sd = run.sdir("patch")
    handoff = sd / "patch.json"
    ops = {o for it in tri["items"] for o in it.get("ops", [])} or None
    pack = ContextPack("patch", run.budget("patch"))
    pack.add("任务", f"""为 MoonCode 实现对 Kimi Code {run.label}（{rep.head_sha[:8]}）的协议兼容。
你在 MoonCode 仓库的分支 `{git_branch(run, rep)}` 上工作，直接修改文件即可，**不要 commit / push**。

完成标准：
1. 逐条处理下方「待适配项」；新服务端走新协议，旧服务端（用户电脑上可能仍是旧版 Kimi Code）保持可用；
2. 在 `reference/README.md` 末尾追加本次兼容说明小节（沿用已有小节的写法）；
3. 运行下方验证命令全部通过；
4. 把结果写入 `{handoff}`（字段见系统提示词中的 patch handoff 规范）。""", 0)
    pack.add("待适配项（来自 triage 阶段的结论）", json.dumps(tri["items"], ensure_ascii=False, indent=2), 1, min_tokens=1500)
    pack.add("对应的契约差异与源码位置", render_delta(rep, surface, ops), 2, min_tokens=1500,
             source=str(run.sdir("detect") / "detect.json"))
    pack.add("验证命令（必须全部通过）", "\n".join(f"- `{c}`" for c in cfg(run.conf, "verify.commands", [])), 1)
    _common_memory(run, pack)
    res = run_kimi(pack.render(sd), cwd=wd, stage_dir=sd, conf=run.conf, agent="patcher", tag="patch")
    session = res.session_id or ""

    rounds = 0
    max_rounds = int(cfg(run.conf, "verify.max_fix_rounds", 3))
    while True:
        if not tg.changed_files(wd, run.conf):
            raise RuntimeError("patcher 没有产生任何改动")
        ok, digest = _verify(run, wd, rounds)
        if ok:
            break
        if rounds >= max_rounds or not session:
            raise RuntimeError(f"验证在 {rounds} 轮修复后仍失败：\n{digest[:1500]}")
        rounds += 1
        fix = ContextPack("fix", run.budget("fix"))
        fix.add("验证失败", f"第 {rounds} 轮修复。下面是编排器从构建日志蒸馏出的错误摘要（原始日志已省略）。"
                "只修复这些错误，不要扩大改动范围；修完后重新运行验证命令，并更新 handoff 文件。", 0)
        fix.add("错误摘要", digest, 1, source=str(run.dir / f"verify-{rounds - 1}.log"))
        res = run_kimi(fix.render(run.sdir(f"fix-{rounds}")), cwd=wd, stage_dir=sd, conf=run.conf,
                       session=session, tag=f"fix-{rounds}")
        session = res.session_id or session
    return read_handoff(handoff, res, required=("summary",)), session, rounds


def review(run: Run, rep: contract.Report, tri: Dict[str, Any], wd: Path, round_no: int) -> Dict[str, Any]:
    sd = run.sdir(f"review-{round_no}")
    handoff = sd / "review.json"
    per_file = int(cfg(run.conf, "context.diff_max_chars_per_file", 12000))
    diff_parts = [head_tail(chunk, per_file) for _, chunk in tg.split_diff_by_file(tg.diff_vs_main(wd, run.conf))]
    pack = ContextPack("review", run.budget("review"))
    pack.add("任务", f"""你是独立审查者，第一次看到这份补丁。它为 MoonCode 适配 Kimi Code {run.label}（{rep.head_sha[:8]}）。
对照「待适配项」审查下方 diff（需要时可自行读取仓库文件，但不要修改任何文件）。把结论写入 `{handoff}`。""", 0)
    pack.add("待适配项", json.dumps(tri["items"], ensure_ascii=False, indent=2), 1)
    pack.add("补丁 diff（相对 origin/main）", "\n\n".join(f"```diff\n{p}\n```" for p in diff_parts), 2, min_tokens=3000)
    pack.add("长期记忆：适配模式", memory.read_ledger(), 5, source=str(memory.LEDGER))
    before = tg.git(wd, "status", "--porcelain", check=False)
    res = run_kimi(pack.render(sd), cwd=wd, stage_dir=sd, conf=run.conf, agent="reviewer", tag="review")
    after = tg.git(wd, "status", "--porcelain", check=False)
    if after != before:
        raise RuntimeError(f"review 阶段是只读的，但工作区被修改了：\n{after[:800]}")
    return read_handoff(handoff, res, required=("verdict",))


def address_review(run: Run, wd: Path, session: str, rv: Dict[str, Any], round_no: int) -> None:
    pack = ContextPack("fix", run.budget("fix"))
    pack.add("审查意见", "独立审查者对你的补丁提出了以下必须处理的问题。逐条修复后重新运行验证命令，并更新 handoff 文件。", 0)
    blocking = [i for i in rv.get("issues", []) if i.get("severity") in ("blocker", "major")]
    pack.add("问题列表", json.dumps(blocking or rv.get("issues", []), ensure_ascii=False, indent=2), 1)
    run_kimi(pack.render(run.sdir(f"address-{round_no}")), cwd=wd, stage_dir=run.sdir("patch"), conf=run.conf,
             session=session, tag=f"address-{round_no}")


def release(run: Run, wd: Path, notes_hint: str, pr_url: str) -> Dict[str, Any]:
    tg.ensure_clone(run.conf)  # 回到最新 main（已含合并后的补丁）
    latest = tg.latest_version_tag(wd)
    version = tg.next_version(latest, cfg(run.conf, "release.version_floor", "1.10"))
    tag = f"v{version}"
    sd = run.sdir("release")
    handoff = sd / "release.json"
    pack = ContextPack("release", run.budget("release"))
    pack.add("发版参数", f"- 版本：`{version}`（上一个 Tag：`{latest or '无'}`）\n- Tag：`{tag}`\n- 本次改动 PR：{pr_url}\n"
             f"- 结果写入：`{handoff}`", 0)
    pack.add("更新日志素材（面向用户改写，不要出现协议/接口术语）", notes_hint, 1)
    prompt = f"拉起 MoonCode {tag} 审核。\n\n" + pack.render(sd)
    if not run.side_effects:
        log(f"[dry-run] 跳过发版：would run kimi releaser for {tag}")
        return {"skipped": True, "version": version}
    res = run_kimi(prompt, cwd=wd, stage_dir=sd, conf=run.conf, agent="releaser", tag="release")
    data = read_handoff(handoff, res, required=("tag",))
    if not tg.remote_tag_exists(wd, tag):
        raise RuntimeError(f"releaser 声称完成，但远端没有 Tag {tag}")
    data["workflow_run"] = _wait_release_run(run, tag)
    return data


def _wait_release_run(run: Run, tag: str) -> Optional[str]:
    deadline = time.time() + int(cfg(run.conf, "release.wait_for_run_sec", 180))
    while time.time() < deadline:
        out = sh(["gh", "run", "list", "-R", cfg(run.conf, "target.repo"), "--workflow", cfg(run.conf, "release.workflow"),
                  "--limit", "5", "--json", "headBranch,url,status"], check=False).stdout
        for r in json.loads(out or "[]"):
            if r.get("headBranch") == tag:
                return r.get("url")
        time.sleep(15)
    log(f"⚠️ 未在时限内看到 {tag} 的 release workflow 运行")
    return None


def distill(run: Run, summary: Dict[str, Any]) -> None:
    sd = run.sdir("distill")
    handoff = sd / "distill.json"
    over = memory.ledger_over_limit(int(cfg(run.conf, "context.ledger_max_chars", 12000)))
    pack = ContextPack("distill", run.budget("distill"))
    pack.add("任务", f"""把本次运行沉淀为长期记忆。写入 `{handoff}`：
- `episode`：≤300 字中文摘要（上游区间、改了什么、踩了什么坑、结果）；
- `lessons`：0~5 条**可复用**的经验（下次适配能直接用的规律），已在 ledger 里的不要重复；
{'- `ledger_rewrite`：ledger 已超长，请输出压缩后的完整 ledger（≤ ' + str(int(cfg(run.conf, 'context.ledger_max_chars', 12000)) * 7 // 10) + ' 字符，保留所有仍有效的协议事实与模式，合并重复、删除过时细节）。' if over else '- 不需要输出 `ledger_rewrite`。'}""", 0)
    pack.add("本次运行结构化记录", json.dumps(summary, ensure_ascii=False, indent=2), 1)
    pack.add("当前 ledger", memory.read_ledger(), 2)
    res = run_kimi(pack.render(sd), cwd=ROOT, stage_dir=sd, conf=run.conf, agent="distiller", tag="distill")
    try:
        data = read_handoff(handoff, res)
    except RuntimeError as e:
        log(f"distill 失败（不影响主流程）：{e}")
        return
    if data.get("episode"):
        memory.write_episode(run.id, data["episode"])
    memory.append_lessons(data.get("lessons") or [], run.id)
    if over and data.get("ledger_rewrite"):
        memory.rewrite_ledger(data["ledger_rewrite"])


def git_branch(run: Run, rep: contract.Report) -> str:
    return f"{cfg(run.conf, 'target.branch_prefix', 'bot/kap-')}{run.label or rep.head_sha[:8]}"


# ───────────────────────────── 总调度 ─────────────────────────────

def resolve_target(conf: Dict[str, Any], version: Optional[str] = None) -> Tuple[str, str]:
    """返回 (上游 commit, 人类可读标签)。release 模式下以 npm 版本号为准。"""
    up = conf["upstream"]
    if version or up.get("trigger", "release") == "release":
        version = version or github.npm_dist_tag(up["npm_package"], up.get("npm_dist_tag", "latest"))
        return github.resolve_sha(up["repo"], up["tag_format"].format(version=version)), version
    sha = github.resolve_sha(up["repo"], up["branch"])
    return sha, sha[:8]


def tick(run: Run, force_head: Optional[str] = None, skip_agents: bool = False, version: Optional[str] = None) -> str:
    conf, s = run.conf, run.state
    up = conf["upstream"]
    inflight = s.get("inflight") or {}
    if force_head:
        head, label = force_head, force_head[:8]
    else:
        head, label = resolve_target(conf, version)
    run.label = label
    # release 模式快速路径：版本号没变就什么都不做（只花 1 次 npm 请求）
    if (up.get("trigger", "release") == "release" and not force_head and not version
            and label == s["upstream"].get("seen_version") and inflight.get("status") != "awaiting_merge"):
        log(f"Kimi Code 版本仍是 {label}，无需检查")
        return "no_change"
    s["upstream"]["seen_version"] = label
    compat = s["upstream"].get("compat_sha") or github.resolve_sha(up["repo"], up["seed_ref"])
    head_blob = github.blob_sha(up["repo"], up["contract_path"], head)
    compat_blob = s["upstream"].get("compat_blob") or github.blob_sha(up["repo"], up["contract_path"], compat)

    # 0) 有未合并的适配 PR：先看它的命运
    if inflight.get("pr") and inflight.get("status") == "awaiting_merge":
        state_ = tg.pr_state(conf, inflight["pr"])
        if state_ == "MERGED":
            log(f"适配 PR 已合并：{inflight['pr']}")
            _advance(s, inflight["head"], inflight["contract_blob"], inflight.get("label"))
            s["inflight"] = None
            wd = tg.ensure_clone(conf)
            if cfg(conf, "release.auto", True):
                rel = release(run, wd, inflight.get("notes_hint", ""), inflight["pr"])
                s["last_release"] = {**rel, "at": now_iso()}
            st.record(s, run.id, "merged_and_released", pr=inflight["pr"])
            return "merged_and_released"
        if state_ == "OPEN" and inflight.get("contract_blob") == head_blob:
            log("适配 PR 仍待合并，契约无新变化，跳过")
            return "awaiting_merge"
        if state_ == "OPEN" and run.side_effects:
            tg.close_pr(conf, inflight["pr"], f"Kimi Code 又发布了新版本（{label}），由新的适配取代。")
        s["inflight"] = None

    # 1) 快速路径：契约文件 blob 没变 → 什么都不做（每 15 分钟一次也只花 3 个 API 调用）
    if head_blob == compat_blob:
        log(f"{label}：契约无变化，MoonCode 兼容")
        _advance(s, head, head_blob, label)
        return "no_change"

    if inflight.get("contract_blob") == head_blob and inflight.get("attempts", 0) >= 2 and not force_head:
        log("同一份契约已连续失败 2 次，等待人工介入或上游新变更")
        return "blocked"

    # 2) 检测（确定性）
    wd = tg.ensure_clone(conf)
    surface = refresh_surface(conf, wd)
    rep = detect(conf, surface, compat, head)
    write_json(run.sdir("detect") / "detect.json", rep.to_dict())
    log(f"检测：{rep.total_changes} 处契约变化，{len(rep.consumed)} 处涉及 MoonCode，最高级别 {rep.max_severity}")
    if not rep.needs_adaptation:
        _advance(s, head, head_blob, label)
        st.record(s, run.id, "compatible", version=label, changes=rep.total_changes)
        return "compatible"
    if skip_agents:
        print(render_delta(rep, surface))
        return "needs_adaptation"

    summary: Dict[str, Any] = {"run": run.id, "base": compat[:8], "head": head[:8], "version": label, "max_severity": rep.max_severity,
                               "consumed_changes": len(rep.consumed)}
    s["inflight"] = {"head": head, "label": label, "contract_blob": head_blob, "status": "adapting",
                     "attempts": (inflight.get("attempts", 0) + 1) if inflight.get("contract_blob") == head_blob else 1}
    try:
        old_text = github.file_at(up["repo"], up["contract_path"], compat) or ""
        new_text = github.file_at(up["repo"], up["contract_path"], head) or ""
        extras = {"intents": upstream_intents(conf, compat, head), "shapes": render_shapes(rep, old_text, new_text),
                  "kap_doc": kap_doc_diff(conf, compat, head)}

        # 3) triage：AI 二次确认
        tri = triage(run, rep, wd, surface, extras)
        summary["triage"] = {"incompatible": tri["incompatible"], "items": [i.get("title", i.get("id")) for i in tri["items"]]}
        if not tri["incompatible"]:
            _advance(s, head, head_blob, label)
            s["inflight"] = None
            st.record(s, run.id, "compatible_after_triage", version=label)
            distill(run, {**summary, "outcome": "compatible_after_triage", "reason": tri.get("summary")})
            return "compatible_after_triage"

        # 4) patch ⇄ verify
        branch = git_branch(run, rep)
        tg.start_branch(wd, conf, branch)
        pt, session, fix_rounds = patch(run, rep, tri, wd, surface)
        summary.update({"patch": pt.get("summary"), "fix_rounds": fix_rounds, "files": tg.changed_files(wd, conf)})

        # 5) review（全新会话，独立视角）
        rv: Dict[str, Any] = {}
        for r in range(int(cfg(conf, "review.max_rounds", 1)) + 1):
            rv = review(run, rep, tri, wd, r)
            if rv.get("verdict") == "approve":
                break
            if r >= int(cfg(conf, "review.max_rounds", 1)):
                raise RuntimeError(f"审查未通过：{json.dumps(rv.get('issues', []), ensure_ascii=False)[:1500]}")
            address_review(run, wd, session, rv, r)
            ok, digest = _verify(run, wd, 100 + r)
            if not ok:
                raise RuntimeError(f"处理审查意见后验证失败：{digest[:1500]}")
        summary["review"] = rv.get("verdict")

        # 6) ship
        notes_hint = pt.get("release_note_zh") or tri.get("release_notes_hint") or ""
        if not run.side_effects:
            log(f"[dry-run] 补丁已在 {wd} 的 {branch} 分支上，未推送")
            summary["outcome"] = "dry_run_patched"
            distill(run, summary)
            return "dry_run_patched"
        tg.commit_all(wd, f"fix(kap): 适配 Kimi Code {label} 协议变更\n\n{pt.get('summary', '')}\n\n"
                          f"Upstream: {compat[:8]}..{head[:8]}\nGenerated-by: mooncode-bot {run.id}")
        tg.push_branch(wd, branch)
        body = _pr_body(run, rep, tri, pt, rv, fix_rounds)
        pr = tg.open_pr(conf, branch, f"fix(kap): 适配 Kimi Code {label}", body)
        summary["pr"] = pr
        s["inflight"].update({"status": "awaiting_merge", "pr": pr, "notes_hint": notes_hint})
        if not cfg(conf, "ship.auto_merge", True):
            st.record(s, run.id, "pr_opened", pr=pr)
            distill(run, {**summary, "outcome": "pr_opened"})
            return "pr_opened"
        tg.merge_pr(conf, pr)
        _advance(s, head, head_blob, label)
        s["inflight"] = None

        # 7) release：kimi -p "拉起 MoonCode vX.Y 审核"
        if cfg(conf, "release.auto", True):
            rel = release(run, wd, notes_hint, pr)
            s["last_release"] = {**rel, "at": now_iso()}
            summary["release"] = rel
        st.record(s, run.id, "adapted_and_released", pr=pr, version=label)
        distill(run, {**summary, "outcome": "adapted_and_released"})
        return "adapted_and_released"
    except Exception as e:  # noqa: BLE001 —— 失败也要沉淀记忆，并保留 inflight 便于重试计数
        if s.get("inflight"):
            s["inflight"]["last_error"] = str(e)[:2000]
        st.record(s, run.id, "failed", version=label, error=str(e)[:300])
        summary.update({"outcome": "failed", "error": str(e)[:2000]})
        try:
            distill(run, summary)
        finally:
            raise


def _advance(s: Dict[str, Any], sha: str, blob: Optional[str], version: Optional[str] = None) -> None:
    s["upstream"]["compat_sha"] = sha
    if version:
        s["upstream"]["compat_version"] = version
    s["upstream"]["compat_blob"] = blob
    s["upstream"]["compat_at"] = now_iso()


def _pr_body(run: Run, rep: contract.Report, tri: Dict, pt: Dict, rv: Dict, fix_rounds: int) -> str:
    items = "\n".join(f"- **{i.get('title', i.get('id'))}**：{i.get('impact', '')}" for i in tri["items"])
    issues = "\n".join(f"- [{i.get('severity')}] {i.get('note', '')}" for i in rv.get("issues", [])) or "无"
    return f"""由 mooncode-bot 自动生成。

**上游版本**：Kimi Code {run.label}（MoonshotAI/kimi-code `{rep.base_sha[:8]}..{rep.head_sha[:8]}`）
**最高级别**：{rep.max_severity}，涉及 MoonCode 的契约变更 {len(rep.consumed)} 处

### 待适配项
{items}

### 补丁说明
{pt.get('summary', '')}

### 验证
- 构建与检查脚本：通过（修复轮数 {fix_rounds}）
- 独立审查：{rv.get('verdict')}；遗留意见：
{issues}

运行记录：{run.ci_link()}
"""
