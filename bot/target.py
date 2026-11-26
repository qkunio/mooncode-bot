"""MoonCode 工作副本上的 git / gh 操作。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bot.util import ROOT, cfg, log, sh


def workdir(conf: Dict[str, Any]) -> Tuple[Path, bool]:
    """返回 (路径, 是否 bot 独占)。独占副本才允许 reset --hard / clean。"""
    custom = os.environ.get("MOONCODE_DIR")
    if custom:
        return Path(custom).expanduser().resolve(), False
    return (ROOT / cfg(conf, "target.workdir", "workspace/mooncode")).resolve(), True


def git(wd: Path, *args: str, check: bool = True) -> str:
    return sh(["git", *args], cwd=wd, check=check, timeout=600).stdout.strip()


def ensure_clone(conf: Dict[str, Any]) -> Path:
    wd, owned = workdir(conf)
    repo, main = cfg(conf, "target.repo"), cfg(conf, "target.branch", "main")
    if not (wd / ".git").exists():
        wd.parent.mkdir(parents=True, exist_ok=True)
        log(f"cloning {repo} → {wd}")
        sh(["gh", "repo", "clone", repo, str(wd)], timeout=900)
    git(wd, "fetch", "origin", "--tags", "--prune", "--force")
    if owned:
        git(wd, "reset", "--hard")
        git(wd, "clean", "-fd", "-e", "build/")
        git(wd, "checkout", "-B", main, f"origin/{main}")
    elif git(wd, "status", "--porcelain"):
        raise SystemExit(f"MOONCODE_DIR={wd} 有未提交改动，bot 不会动它")
    return wd


def start_branch(wd: Path, conf: Dict[str, Any], name: str) -> None:
    main = cfg(conf, "target.branch", "main")
    git(wd, "checkout", "-B", name, f"origin/{main}")


def diff_vs_main(wd: Path, conf: Dict[str, Any]) -> str:
    main = cfg(conf, "target.branch", "main")
    git(wd, "add", "-N", ".", check=False)  # 让新文件也出现在 diff 里
    return git(wd, "diff", f"origin/{main}", "--", ".", ":(exclude)build")


def changed_files(wd: Path, conf: Dict[str, Any]) -> List[str]:
    main = cfg(conf, "target.branch", "main")
    git(wd, "add", "-N", ".", check=False)
    out = git(wd, "diff", "--name-only", f"origin/{main}", "--", ".", ":(exclude)build")
    return [l for l in out.splitlines() if l.strip()]


def split_diff_by_file(diff: str) -> List[Tuple[str, str]]:
    chunks = re.split(r"(?m)^(?=diff --git )", diff)
    out = []
    for c in chunks:
        m = re.match(r"diff --git a/(\S+) b/", c)
        if m:
            out.append((m.group(1), c))
    return out


def commit_all(wd: Path, message: str) -> str:
    git(wd, "add", "-A", "--", ".", ":(exclude)build")
    git(wd, "-c", "user.name=mooncode-bot", "-c", "user.email=mooncode-bot@users.noreply.github.com",
        "commit", "-m", message)
    return git(wd, "rev-parse", "HEAD")


def push_branch(wd: Path, branch: str) -> None:
    git(wd, "push", "-u", "origin", branch, "--force")


def open_pr(conf: Dict[str, Any], branch: str, title: str, body: str) -> str:
    repo, main = cfg(conf, "target.repo"), cfg(conf, "target.branch", "main")
    existing = sh(["gh", "pr", "list", "-R", repo, "--head", branch, "--state", "open", "--json", "url",
                   "--jq", ".[0].url"], check=False).stdout.strip()
    if existing:
        sh(["gh", "pr", "edit", existing, "-R", repo, "--title", title, "--body", body])
        return existing
    return sh(["gh", "pr", "create", "-R", repo, "--base", main, "--head", branch,
               "--title", title, "--body", body]).stdout.strip().splitlines()[-1]


def merge_pr(conf: Dict[str, Any], url: str) -> None:
    method = cfg(conf, "ship.merge_method", "squash")
    sh(["gh", "pr", "merge", url, "-R", cfg(conf, "target.repo"), f"--{method}", "--delete-branch"], timeout=300)


def pr_state(conf: Dict[str, Any], url: str) -> str:
    return sh(["gh", "pr", "view", url, "-R", cfg(conf, "target.repo"), "--json", "state", "--jq", ".state"],
              check=False).stdout.strip()


def close_pr(conf: Dict[str, Any], url: str, comment: str) -> None:
    sh(["gh", "pr", "close", url, "-R", cfg(conf, "target.repo"), "--comment", comment, "--delete-branch"], check=False)


def _vkey(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def latest_version_tag(wd: Path) -> Optional[str]:
    tags = [t[1:] for t in git(wd, "tag", "-l", "v*").splitlines() if re.match(r"^v\d+(\.\d+){1,2}$", t)]
    return max(tags, key=_vkey) if tags else None


def next_version(latest: Optional[str], floor: str) -> str:
    """X.Y → X.(Y+1)，且不低于 floor（App Store 上已有 1.9，下一版必须 ≥ 1.10）。"""
    if latest:
        parts = list(_vkey(latest))[:2] + [0] * (2 - len(_vkey(latest)[:2]))
        cand = f"{parts[0]}.{parts[1] + 1}"
    else:
        cand = floor
    return cand if _vkey(cand) >= _vkey(floor) else floor


def remote_tag_exists(wd: Path, tag: str) -> bool:
    return bool(git(wd, "ls-remote", "--tags", "origin", f"refs/tags/{tag}", check=False))
