"""通用小工具：配置、日志、子进程。保持纯标准库、兼容 Python 3.9。"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    p = Path(path or os.environ.get("BOT_CONFIG") or ROOT / "config" / "bot.json")
    return json.loads(p.read_text("utf-8"))


def cfg(conf: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    cur: Any = conf
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return default if cur is None else cur


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def run_id() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def log(msg: str) -> None:
    ts = _dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


class CmdError(RuntimeError):
    def __init__(self, cmd: Sequence[str], code: int, out: str, err: str):
        super().__init__(f"command failed ({code}): {' '.join(cmd)}\n{err.strip()[-2000:]}")
        self.code, self.out, self.err = code, out, err


def sh(
    cmd: Sequence[str],
    cwd: Optional[Path] = None,
    check: bool = True,
    timeout: Optional[int] = None,
    env: Optional[Dict[str, str]] = None,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    proc = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=full_env,
        input=input_text,
    )
    if check and proc.returncode != 0:
        raise CmdError(cmd, proc.returncode, proc.stdout, proc.stderr)
    return proc


def sh_shell(command: str, cwd: Path, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    """运行一条 shell 字符串（verify 命令来自配置文件），合并 stdout/stderr。"""
    return subprocess.run(
        command,
        cwd=str(cwd),
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text("utf-8"))


def armed(conf: Dict[str, Any]) -> bool:
    """只有显式武装（环境变量 BOT_ARMED=true）时才允许 push / merge / 打 Tag。"""
    name = cfg(conf, "safety.armed_env", "BOT_ARMED")
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def extract_json_block(text: str) -> Optional[Any]:
    """从模型回复里兜底提取最后一个 ```json 代码块。"""
    import re

    blocks: List[str] = re.findall(r"```(?:json)?\s*\n(.*?)\n```", text or "", flags=re.S)
    for raw in reversed(blocks):
        try:
            return json.loads(raw)
        except ValueError:
            continue
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None
