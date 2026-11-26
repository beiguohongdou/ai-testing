"""防篡改硬门禁：检查 git diff，拦截擅自删减断言、改弱预期或私加 skip/xfail 的行为。

    caseflow guard                 # 检查工作区相对 HEAD 的改动
    caseflow guard --base main     # 检查相对指定分支/提交的改动
    caseflow guard --allow         # 人工确认需求变更后放行（仍打印清单，返回 0）
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from caseflow.config import project_root

_PY_ASSERT_RE = re.compile(r"\b(assert|expect)\b|\.(expect_ok|expect_code|expect)\s*\(")
_PY_SKIP_RE = re.compile(r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail)\s*\(")
_YAML_EXPECT_RE = re.compile(r"^\s*(expect|status|code|ok)\s*:")
_YAML_XFAIL_RE = re.compile(r"^\s*(xfail|xfail_strict)\s*:")


def _is_target_file(path: str) -> str | None:
    norm = path.replace("\\", "/")
    if norm.startswith("tests/") and norm.endswith(".py"):
        return "py"
    if norm.startswith("data/") and norm.endswith((".yaml", ".yml")):
        return "yaml"
    return None


def scan_diff(diff_text: str) -> list[str]:
    """解析 unified diff，返回可疑的断言篡改/绕过告警列表。"""
    findings: list[str] = []
    cur_file: str | None = None
    cur_kind: str | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            cur_file = None
            cur_kind = None
            continue
        if line.startswith("+++ b/"):
            cur_file = line[6:].strip()
            cur_kind = _is_target_file(cur_file)
            continue
        if not cur_kind or not cur_file:
            continue
        if line.startswith(("+++", "---", "@@")):
            continue

        if line.startswith("-"):
            body = line[1:]
            stripped = body.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            if cur_kind == "py" and _PY_ASSERT_RE.search(stripped):
                findings.append(f"[DEL-ASSERT] {cur_file}: 删除/修改断言 -> {stripped[:100]}")
            elif cur_kind == "yaml" and _YAML_EXPECT_RE.search(body):
                findings.append(f"[MOD-EXPECT] {cur_file}: 删除/修改预期字段 -> {stripped[:100]}")

        elif line.startswith("+"):
            body = line[1:]
            stripped = body.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            if cur_kind == "py" and _PY_SKIP_RE.search(stripped):
                findings.append(f"[ADD-SKIP] {cur_file}: 新增 skip/xfail 标记 -> {stripped[:100]}")
            elif cur_kind == "yaml" and _YAML_XFAIL_RE.search(body):
                findings.append(f"[ADD-XFAIL] {cur_file}: 新增 xfail 标记 -> {stripped[:100]}")

    return findings


def _git_diff(root: Path, base: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", "diff", "--unified=0", base, "--", "tests", "data"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


def main(base: str = "HEAD", allow: bool = False) -> int:
    root = project_root()
    code, out, err = _git_diff(root, base)
    if code != 0:
        msg = (err or "").strip()
        if "not a git repository" in msg.lower() or "bad revision" in msg.lower():
            print(f"[SKIP] 当前目录无可用 Git 基准（{base}），跳过断言防篡改检查")
            return 0
        print(f"[FAIL] git diff 执行失败：{msg}")
        return 1

    findings = scan_diff(out)
    if not findings:
        print("[OK] guard：未发现擅自删改断言或私加 skip/xfail 的变更")
        return 0

    for item in findings:
        print(item)
    if allow:
        print(f"[ALLOW] guard：检测到 {len(findings)} 处预期/跳过变更，已按 --allow 放行")
        return 0
    print(f"[BLOCKED] guard：发现 {len(findings)} 处疑似弱化/绕过断言的变更，请人工确认；确认无误可加 --allow 放行")
    return 1
