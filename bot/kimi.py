"""Kimi Code 执行器：所有 AI 工作都通过 `kimi -p ... --output-format stream-json` 完成。

- 每个阶段默认开新会话（上下文隔离）；同一阶段的修复轮次用 `-S <session_id>` 续接；
- 完整事件流写入 runs/<id>/<stage>/transcript.jsonl，只用于审计，**不会**回流到下一阶段；
- 结构化结果优先读 agent 写出的 handoff 文件，兜底解析回复里的 ```json 代码块。
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from bot.util import ROOT, extract_json_block, log, read_json


@dataclass
class KimiResult:
    exit_code: int
    session_id: Optional[str]
    final_text: str
    tool_calls: int
    seconds: float
    transcript: Path

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in (None, "text"))
    return ""


MAX_ARGV_BYTES = 100_000  # 远低于 Linux 单参数 128KB 与 macOS ARG_MAX 1MB 上限


def _cli_prompt(prompt: str, prompt_file: Path, max_bytes: int = MAX_ARGV_BYTES) -> str:
    """超长上下文包改走文件指针，避免命令行参数长度超限，同时不截断任何内容。"""
    if len(prompt.encode("utf-8")) <= max_bytes:
        return prompt
    log(f"prompt 超过 {max_bytes // 1000}KB，改为传文件路径 {prompt_file.name}")
    return (f"完整任务与上下文包已保存在 `{prompt_file}`（共 {len(prompt)} 字符），"
            f"请先用 Read 工具完整读取该文件，再严格按其中的要求完成任务。")


def run_kimi(
    prompt: str,
    *,
    cwd: Path,
    stage_dir: Path,
    conf: Dict[str, Any],
    agent: Optional[str] = None,
    session: Optional[str] = None,
    add_dirs: Sequence[Path] = (),
    tag: str = "run",
) -> KimiResult:
    stage_dir.mkdir(parents=True, exist_ok=True)
    transcript = stage_dir / f"{tag}.transcript.jsonl"
    errlog = stage_dir / f"{tag}.stderr.log"
    prompt_file = (stage_dir / f"{tag}.prompt.md").resolve()
    prompt_file.write_text(prompt, "utf-8")

    kc = conf.get("kimi", {})
    cmd: List[str] = [kc.get("bin") or "kimi", "-p", _cli_prompt(prompt, prompt_file), "--output-format", "stream-json"]
    if session:
        cmd += ["-S", session]  # 续接时不能再带 --agent-file
    elif agent:
        cmd += ["--agent-file", str(ROOT / (kc.get("agents_dir") or "agents") / f"{agent}.md")]
    skills = ROOT / (kc.get("skills_dir") or "skills")
    if skills.exists():
        cmd += ["--skills-dir", str(skills)]
    for d in [*add_dirs, stage_dir.parent]:  # handoff 与 prompt 文件都在 runs/<id>/ 下，需加入工作区
        cmd += ["--add-dir", str(d)]
    if kc.get("model"):
        cmd += ["-m", kc["model"]]

    log(f"kimi ▶ {agent or 'resume'} [{tag}] cwd={cwd.name} session={session or 'new'}")
    t0 = time.time()
    with open(transcript, "w", encoding="utf-8") as out, open(errlog, "w", encoding="utf-8") as err:
        try:
            proc = subprocess.run(cmd, cwd=str(cwd), stdout=out, stderr=err, text=True,
                                  timeout=int(kc.get("timeout_sec") or 2700))
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = 124
    seconds = time.time() - t0

    session_id, final_text, tool_calls = None, "", 0
    for line in transcript.read_text("utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "session.resume_hint":
            session_id = ev.get("session_id") or session_id
        if ev.get("role") == "assistant":
            if ev.get("tool_calls"):
                tool_calls += len(ev["tool_calls"])
            else:
                txt = _text_of(ev.get("content"))
                if txt.strip():
                    final_text = txt
    log(f"kimi ◀ exit={code} tools={tool_calls} {seconds:.0f}s session={session_id}")
    return KimiResult(code, session_id or session, final_text, tool_calls, seconds, transcript)


def read_handoff(path: Path, result: KimiResult, required: Sequence[str] = ()) -> Dict[str, Any]:
    data = read_json(path, None)
    if data is None:
        data = extract_json_block(result.final_text)
    if not isinstance(data, dict):
        raise RuntimeError(f"agent 没有产出合法的 handoff：{path}（见 {result.transcript}）")
    missing = [k for k in required if k not in data]
    if missing:
        raise RuntimeError(f"handoff 缺少字段 {missing}：{path}")
    return data
