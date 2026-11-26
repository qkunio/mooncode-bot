"""命令行入口：python3 -m bot <command>"""

from __future__ import annotations

import argparse
import json
import sys

from bot import contract, github, pipeline, state as st, target as tg
from bot.util import armed, cfg, load_config, log, run_id


def _run(conf, dry_run: bool) -> pipeline.Run:
    return pipeline.Run(conf=conf, id=run_id(), state=st.load(), dry_run=dry_run)


def cmd_tick(args, conf) -> int:
    run = _run(conf, args.dry_run)
    if not run.side_effects:
        log("演练模式：不会 push / merge / 打 Tag，也不会写 state（设置 BOT_ARMED=true 才会真正执行）")
    with st.lock():
        try:
            outcome = pipeline.tick(run, force_head=args.head, skip_agents=args.no_agent, version=args.version)
        finally:
            if run.side_effects:
                st.save(run.state)
    print(json.dumps({"run": run.id, "outcome": outcome}, ensure_ascii=False))
    _gh_output("outcome", outcome)
    return 0


def cmd_detect(args, conf) -> int:
    up = conf["upstream"]
    s = st.load()
    base = github.resolve_sha(up["repo"], args.base or s["upstream"].get("compat_sha") or up["seed_ref"])
    head = github.resolve_sha(up["repo"], args.head) if args.head else pipeline.resolve_target(conf)[0]
    wd = tg.ensure_clone(conf)
    surface = pipeline.refresh_surface(conf, wd)
    rep = pipeline.detect(conf, surface, base, head)
    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"base {base[:8]} → head {head[:8]}：契约变化 {rep.total_changes} 处，"
              f"涉及 MoonCode {len(rep.consumed)} 处，最高级别 {rep.max_severity}，"
              f"{'需要适配' if rep.needs_adaptation else '兼容'}\n")
        print(pipeline.render_delta(rep, surface))
    return 0


def cmd_surface(args, conf) -> int:
    wd = tg.ensure_clone(conf)
    data = pipeline.refresh_surface(conf, wd)
    print(f"{len(data['ops'])} 个接口/消息 → {pipeline.SURFACE_PATH}")
    if args.verbose:
        for op, where in data["ops"].items():
            print(f"{op:60s} {', '.join(where[:2])}")
    return 0


def cmd_release(args, conf) -> int:
    run = _run(conf, args.dry_run)
    wd = tg.ensure_clone(conf)
    rel = pipeline.release(run, wd, args.notes or "", args.pr or "")
    if run.side_effects:
        run.state["last_release"] = rel
        st.save(run.state)
    print(json.dumps(rel, ensure_ascii=False, indent=2))
    return 0


def cmd_seed(args, conf) -> int:
    up = conf["upstream"]
    sha = github.resolve_sha(up["repo"], args.ref)
    s = st.load()
    pipeline._advance(s, sha, github.blob_sha(up["repo"], up["contract_path"], sha))
    s["inflight"] = None
    st.save(s)
    print(f"compat 基线 → {args.ref} ({sha[:8]})")
    return 0


def cmd_status(args, conf) -> int:
    s = st.load()
    print(json.dumps({"armed": armed(conf), "upstream": s["upstream"], "inflight": s.get("inflight"),
                      "last_release": s.get("last_release"), "recent": s.get("history", [])[-5:]},
                     ensure_ascii=False, indent=2))
    return 0


def _gh_output(key: str, value: str) -> None:
    import os
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mooncode-bot", description="Kimi Code → MoonCode 兼容调度器")
    p.add_argument("--config")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("tick", help="跑一轮完整调度（检测→适配→验证→审查→合并→提审）")
    t.add_argument("--dry-run", action="store_true", help="不产生任何远端副作用")
    t.add_argument("--no-agent", action="store_true", help="只做确定性检测，不调用 kimi")
    t.add_argument("--head", help="指定上游 commit")
    t.add_argument("--version", help="指定 Kimi Code 版本（默认 npm latest）")
    t.set_defaults(fn=cmd_tick)

    d = sub.add_parser("detect", help="只看契约差异（不改状态、不调 AI）")
    d.add_argument("--base")
    d.add_argument("--head")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=cmd_detect)

    s = sub.add_parser("surface", help="重新扫描 MoonCode 使用的接口面")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_surface)

    r = sub.add_parser("release", help="手动让 kimi 拉起一次提审")
    r.add_argument("--notes", help="更新日志素材")
    r.add_argument("--pr", help="关联 PR 链接")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_release)

    sd = sub.add_parser("seed", help="把兼容基线设为某个上游 ref（如 @moonshot-ai/kimi-code@2.8.0）")
    sd.add_argument("ref")
    sd.set_defaults(fn=cmd_seed)

    sub.add_parser("status", help="查看状态").set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    conf = load_config(args.config)
    return args.fn(args, conf)


if __name__ == "__main__":
    sys.exit(main())
