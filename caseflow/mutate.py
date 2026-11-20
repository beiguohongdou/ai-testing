"""断言区分力检查（防假绿）：把接口响应故意改坏，再跑一遍用例；改坏了还通过的用例 = 断言没检查到要害。

    caseflow mutate                  # 默认跑 tests/api
    caseflow mutate tests/api/test_items.py

变异体（只改业务请求，不改登录）：
- empty_body   响应体清空            → 抓「只断言状态码」
- flip_status  HTTP 状态码成功↔失败   → 抓「只看 body、不管请求成没成功」
- flip_code    envelope 业务码成功↔失败（配了 api.envelope 才有）→ 抓「HTTP 200 就算过」
- null_data    envelope 的 data 置空 → 抓「只看 code、不看数据」
注意：多步用例里前面步骤被改坏会导致提前失败，这种算「拦住了」，可能偏乐观。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from caseflow.config import project_root, setting

MUTANTS = ("empty_body", "flip_status", "flip_code", "null_data")


def _run(targets: list[str], mutant: str | None, workdir: Path) -> tuple[dict[str, str], set[str]]:
    """跑一遍 pytest，返回 {用例: passed/failed/skipped} 和被变异影响到的用例集合。"""
    xml = workdir / f"{mutant or 'baseline'}.xml"
    touched = workdir / f"{mutant or 'baseline'}.touched"
    envs = {**os.environ, "CASEFLOW_PROJECT": str(project_root()), "CASEFLOW_TOUCHED": str(touched)}
    envs.pop("CASEFLOW_MUTANT", None)
    if mutant:
        envs["CASEFLOW_MUTANT"] = mutant
    subprocess.run([sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider",
                    f"--junitxml={xml}", "-o", "addopts=", "--rootdir", str(project_root())],
                   cwd=project_root(), env=envs, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = {}
    if xml.exists():
        for tc in ET.parse(xml).getroot().iter("testcase"):
            name = f"{tc.get('classname')}.{tc.get('name')}"
            kinds = {child.tag for child in tc}
            results[name] = "failed" if kinds & {"failure", "error"} else "skipped" if "skipped" in kinds else "passed"
    hit = set()
    if touched.exists():
        for line in touched.read_text(encoding="utf-8").splitlines():
            path, _, func = line.partition("::")
            hit.add(path.replace("\\", "/").removesuffix(".py").replace("/", ".") + "." + func.replace("::", "."))
    return results, hit


def main(targets: list[str] | None = None) -> int:
    targets = targets or ["tests/api"]
    mutants = [m for m in MUTANTS if setting("api", "envelope") or m in ("empty_body", "flip_status")]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        base, _ = _run(targets, None, work)
        passed = {k for k, v in base.items() if v == "passed"}
        failed = {k for k, v in base.items() if v == "failed"}
        print(f"基线：{len(base)} 条用例，通过 {len(passed)} 条（只检查通过的用例）")
        if not passed:
            if base and not failed:
                print(f"[SKIP] 共 {len(base)} 条用例均为 xfail/skip，无通过用例可做变异检查")
                return 0
            print(f"[FAIL] 基线有 {len(failed)} 条失败（总计 {len(base)} 条），没有通过的用例可检查")
            return 1
        weak: dict[str, list[str]] = {}
        for m in mutants:
            res, hit = _run(targets, m, work)
            for name in passed:
                if res.get(name) == "passed" and name in hit:
                    weak.setdefault(name, []).append(m)
    for name, ms in sorted(weak.items()):
        print(f"[WEAK] {name}：响应被改成 {ms} 后仍然通过")
    print(f"mutate：检查 {len(passed)} 条用例 × {len(mutants)} 个变异体，弱断言用例 {len(weak)} 条")
    return 1 if weak else 0
