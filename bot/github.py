"""GitHub 访问层：统一走 `gh` CLI（本地用钥匙串登录，CI 里用 GH_TOKEN）。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from bot.util import CmdError, sh


def api(path: str, raw: bool = False, method: str = "GET", fields: Optional[Dict[str, str]] = None) -> Any:
    cmd = ["gh", "api", path, "--method", method]
    if raw:
        cmd += ["-H", "Accept: application/vnd.github.raw"]
    for k, v in (fields or {}).items():
        cmd += ["-f", f"{k}={v}"]
    out = sh(cmd, timeout=120).stdout
    if raw:
        return out
    return json.loads(out) if out.strip() else None


def resolve_sha(repo: str, ref: str) -> str:
    """分支 / Tag（含带注释 Tag）/ 短 SHA → 完整 commit SHA。"""
    return api(f"repos/{repo}/commits/{quote(ref, safe='')}")["sha"]


def file_at(repo: str, path: str, ref: str) -> Optional[str]:
    try:
        return api(f"repos/{repo}/contents/{path}?ref={quote(ref, safe='')}", raw=True)
    except CmdError as e:
        if "404" in e.err or "Not Found" in e.err:
            return None
        raise


def blob_sha(repo: str, path: str, ref: str) -> Optional[str]:
    try:
        return api(f"repos/{repo}/contents/{path}?ref={quote(ref, safe='')}")["sha"]
    except CmdError as e:
        if "404" in e.err or "Not Found" in e.err:
            return None
        raise


def commit_info(repo: str, sha: str) -> Dict[str, Any]:
    c = api(f"repos/{repo}/commits/{sha}")
    return {"sha": c["sha"], "date": c["commit"]["committer"]["date"], "message": c["commit"]["message"]}


def commits_touching(repo: str, path: str, base_sha: str, head_sha: str, limit: int = 12) -> List[Dict[str, Any]]:
    """head 往前、直到 base 为止，修改过 path 的提交（新→旧）。"""
    base_date = commit_info(repo, base_sha)["date"]
    items = api(f"repos/{repo}/commits?sha={head_sha}&path={quote(path)}&per_page={limit}")
    result = []
    for c in items or []:
        if c["sha"] == base_sha or c["commit"]["committer"]["date"] <= base_date:
            break
        result.append({"sha": c["sha"], "date": c["commit"]["committer"]["date"], "message": c["commit"]["message"]})
    return result


def pr_intent(repo: str, message: str, body_chars: int = 600) -> Optional[Dict[str, str]]:
    """从提交标题里的 (#1234) 拿 PR 标题 + 截断正文，作为“变更意图”上下文。"""
    m = re.search(r"\(#(\d+)\)", message.splitlines()[0] if message else "")
    if not m:
        return None
    try:
        pr = api(f"repos/{repo}/pulls/{m.group(1)}")
    except CmdError:
        return None
    body = (pr.get("body") or "").strip()
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S).strip()
    if len(body) > body_chars:
        body = body[:body_chars] + "…"
    return {"number": m.group(1), "title": pr.get("title", ""), "body": body, "url": pr.get("html_url", "")}
