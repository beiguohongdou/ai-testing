"""断言区分力检查：拿录制的真实响应做变异，body 断言在变异后仍全部通过 = 这组断言区分不了。

    python -m aigen mutate             # 先录制运行一遍接口用例，再检查
    python -m aigen mutate 录制文件     # 用已有录制（AIGEN_RECORD=文件 时运行 pytest 产生）

变异体：空 body；删掉被断言的字段；改掉被断言字段的值；给「应不存在」的字段加值；
以及最关键的——同一接口「相反结果」的真实响应（被拒用例拿成功响应来试，反之亦然）。
只检查 JSON body 断言（WF-7：body 要能独立判断，不能只靠状态码）。
变量：let 的值运行时录制下来；extract 的值用录制的响应离线重算；断言里直接写动态数据（${unique} 等）的跳过并计数。
"""
import copy
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from aigen import runtime
from aigen.ir import GENS, case_files, func_name, gen_root, load
from aigen.render import render_assert, render_extract
from aigen.runtime import root

HELPERS = {n: getattr(runtime, n) for n in ("assert_contract", "const", "matches", "sha256", "xml_text")}


def checkable(step) -> tuple[list[str], int]:
    """可离线判定的 body 断言代码行，以及因直接写了动态数据而跳过的条数。"""
    lines, skipped = [], 0
    for e in step.expected:
        if not e.target.startswith("body"):
            continue
        if any(f"${{{g}" in repr(e.value) for g in GENS):
            skipped += 1
            continue
        lines += render_assert(e, step.body_type)
    return lines, skipped


def passes(lines: list[str], body, variables: dict | None = None) -> bool:
    env = {**HELPERS, **(variables or {}), "body": body}
    try:
        for line in lines:
            exec(line, env)
        return True
    except Exception:
        return False


def replay_extract(step, body, variables: dict) -> dict:
    """用录制的响应重算这一步 extract 出的变量。"""
    env = {**HELPERS, **variables, "body": body}
    try:
        for var, src in step.extract.items():
            exec("\n".join(render_extract(var, src)), env)
    except Exception:
        return variables
    return variables | {var: env[var] for var in step.extract if var in env}


def _parent(data, parts):
    for p in parts[:-1]:
        data = data[int(p)] if p.isdigit() else data[p]
    return data, (int(parts[-1]) if parts[-1].isdigit() else parts[-1])


def _mutate(body, parts, how):
    m = copy.deepcopy(body)
    try:
        parent, key = _parent(m, parts)
        if how == "add":
            if isinstance(parent, dict) and key not in parent:
                parent[key] = "mutated"
                return m
            return None
        v = parent[key]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if how == "delete":
        del parent[key]
    elif isinstance(v, bool):
        parent[key] = not v
    elif isinstance(v, (int, float)):
        parent[key] = v + 1
    elif isinstance(v, str):
        parent[key] = "__mutated__"  # 整体替换：追加后缀的话 contains 断言照样通过，是假报警
    elif isinstance(v, list):
        parent[key] = [] if v else [{"mutated": True}]
    elif isinstance(v, dict):
        parent[key] = {}
    else:
        parent[key] = "mutated"
    return m


def mutants(body, step, opposite):
    """每个变异体都应让断言失败；只按断言声称的内容变异：
    not_exists → 加上该字段；exists → 删掉；关心取值的（eq / contains / 列表类）→ 删掉、改值。"""
    body_checks = [e for e in step.expected if e.target.startswith("body")]
    if any(e.op != "not_exists" for e in body_checks):  # 全是「应不存在」时，空 body 本来就该通过
        yield "空 body", {} if isinstance(body, dict) else []
    for e in body_checks:
        parts = e.target.split(".")[1:]
        if not parts:
            continue
        hows = {"not_exists": [("add", "加上")], "exists": [("delete", "删掉")]}.get(e.op, [("delete", "删掉"), ("change", "改掉")])
        for how, word in hows:
            if (m := _mutate(body, parts, how)) is not None:
                yield f"{word} {e.target}", m
    yield from opposite


def run_and_record() -> Path:
    out = root() / "reports" / "aigen_record.jsonl"
    out.parent.mkdir(exist_ok=True)
    out.unlink(missing_ok=True)
    print(f"录制：运行 {gen_root().name}/api_gen …")
    subprocess.run([sys.executable, "-m", "pytest", str(gen_root() / "api_gen"), "-q", "-p", "no:cacheprovider", "-o", "addopts="],
                   env={**os.environ, "AIGEN_RECORD": str(out)}, cwd=root(), stdout=subprocess.DEVNULL)
    return out


def main(record: str | None = None) -> int:
    path = Path(record) if record else run_and_record()
    recs, lets = defaultdict(list), defaultdict(dict)
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["kind"] == "step":
            recs[r["test"]].append(r)
        elif r["kind"] == "var":
            lets[r["test"]][r["name"]] = r["value"]

    # 每个用例步骤对应的录制响应 + 当时的变量；按「方法 + 路径模板」归组，用来找相反结果
    pairs, by_endpoint = [], defaultdict(list)
    for p in case_files():
        suite = load(p)
        if suite.kind != "api":
            continue
        for c in suite.cases:
            variables = dict(lets.get(func_name(c.case_id), {}))
            for i, (step, rec) in enumerate(zip(c.steps, recs.get(func_name(c.case_id), []))):
                key = f"{step.request.method} {step.request.path}"
                pairs.append((c, i, step, rec, key, dict(variables)))
                by_endpoint[key].append((c.case_id, rec))
                if step.body_type == "json":
                    variables = replay_extract(step, rec["body"], variables)

    weak = checked = total = skipped = no_body = baseline_fail = 0
    for c, i, step, rec, key, variables in pairs:
        if step.body_type != "json":
            no_body += 1
            continue
        lines, s = checkable(step)
        skipped += s
        if not lines:
            no_body += 1
            continue
        if not passes(lines, rec["body"], variables):
            baseline_fail += 1  # 已知缺陷或本次失败的用例，原响应就不满足断言
            continue
        checked += 1
        ok = rec["status"] < 400
        opposite = [(f"{cid} 的 {r['status']} 响应（{key} 的相反结果）", r["body"])
                    for cid, r in by_endpoint[key] if (r["status"] < 400) != ok and r["body"] is not None][:3]
        for desc, body in mutants(rec["body"], step, opposite):
            total += 1
            if passes(lines, body, variables):
                weak += 1
                print(f"[WEAK] {c.case_id} 步骤{i + 1}（{key}）：body 断言在「{desc}」上仍全部通过")
    print(f"mutate: 检查 {checked} 个步骤、{total} 个变异体，存活 {weak} 个；"
          f"跳过直接写动态数据的断言 {skipped} 条；无可检查 JSON body 断言的步骤 {no_body} 个；原响应不满足断言（已知缺陷/失败）{baseline_fail} 个")
    return 1 if weak else 0
