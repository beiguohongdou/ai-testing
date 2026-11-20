"""反向导出用例表：扫描真正在跑的 data/*.yaml 和 tests/**/test_*.py，生成 reports/用例表.csv（Excel 可直接打开）和 .md。

代码里的用例按 docstring 第一行识别编号和标题，写法：
    def test_order_flow(api_as):
        \"\"\"TC-ORDER-01 下单→支付→查询状态\"\"\"
"""
from __future__ import annotations

import ast
import csv
import json
import re
from pathlib import Path

from caseflow.config import project_root

COLUMNS = ["编号", "标题", "类型", "文件", "请求 / 函数", "预期", "状态", "备注"]
_ID = re.compile(r"^\s*([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\s*(.*)$")


def _expect_text(exp: dict) -> str:
    parts = []
    if exp.get("ok") is True:
        parts.append("业务成功")
    if "code" in exp:
        parts.append(f"code={exp['code']}")
    for path, ops in (exp.get("body") or {}).items():
        ops = ops if isinstance(ops, dict) else {"eq": ops}
        parts += [f"{path} {op} {json.dumps(v, ensure_ascii=False)}" for op, v in ops.items()]
    if "status" in exp:
        parts.append(f"HTTP {exp['status']}")
    return "；".join(parts)


def data_rows(root: Path) -> list[dict]:
    from caseflow.data import read_cases

    rows = []
    for p in sorted((root / "data").rglob("*.y*ml")) if (root / "data").is_dir() else []:
        try:
            cases = read_cases(p)
        except ValueError as e:
            rows.append({"编号": "-", "标题": "数据文件格式错误", "类型": "接口", "文件": str(p.relative_to(root)),
                         "请求 / 函数": "", "预期": "", "状态": "错误", "备注": str(e)})
            continue
        for case, raw in cases:
            status = ("跳过" if raw.get("skip")
                      else "已知缺陷(xfail)" if raw.get("xfail_strict")
                      else "待校准(xfail)" if raw.get("xfail")
                      else "可运行")
            note = "；".join(str(x) for x in (raw.get("xfail_strict") or raw.get("xfail") or raw.get("skip"),
                                             case.note, case.source) if x)
            rows.append({"编号": case.id, "标题": case.title, "类型": "接口", "文件": str(p.relative_to(root)),
                         "请求 / 函数": f"{case.method} {case.path}" + (f"（{case.role}）" if case.role else ""),
                         "预期": _expect_text(case.expect), "状态": status, "备注": note})
    return rows


def _marks(fn: ast.FunctionDef) -> tuple[str, str, str]:
    """返回 (状态, 备注, 数据驱动文件)"""
    status, note, data_file = "可运行", "", ""
    for dec in fn.decorator_list:
        src = ast.unparse(dec)
        if "load_cases(" in src:
            m = re.search(r"load_cases\(\s*['\"]([^'\"]+)", src)
            data_file = m.group(1) if m else "?"
        elif ".skip" in src:
            status, note = "跳过", src
        elif ".xfail" in src:
            status, note = "已知问题(xfail)", src
    return status, note, data_file


def code_rows(root: Path) -> list[dict]:
    rows = []
    tests = root / "tests"
    for p in sorted(tests.rglob("test_*.py")) if tests.is_dir() else []:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        funcs = [(None, n) for n in tree.body if isinstance(n, ast.FunctionDef)]
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            funcs += [(cls.name, n) for n in cls.body if isinstance(n, ast.FunctionDef)]
        for cls, fn in funcs:
            if not fn.name.startswith("test"):
                continue
            status, note, data_file = _marks(fn)
            if data_file:
                continue  # 数据驱动的用例已经按 YAML 逐条列出
            doc = (ast.get_docstring(fn) or "").strip().splitlines()
            first = doc[0] if doc else ""
            m = _ID.match(first)
            cid, title = (m.group(1), m.group(2)) if m else ("-", first or fn.name)
            args = {a.arg for a in fn.args.args}
            kind = "UI" if "page" in args or "ui" in p.relative_to(root).parts else "接口"
            rows.append({"编号": cid, "标题": title, "类型": kind, "文件": str(p.relative_to(root)),
                         "请求 / 函数": f"{cls + '.' if cls else ''}{fn.name}", "预期": "；".join(doc[1:4]).strip(),
                         "状态": status, "备注": note if note else ("" if m else "docstring 第一行没写编号")})
    return rows


def _cell(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\n", "<br>")


def main(out_dir: str = "reports") -> int:
    root = project_root()
    rows = data_rows(root) + code_rows(root)
    out = root / out_dir
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "用例表.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    stats = {}
    for r in rows:
        stats[r["类型"]] = stats.get(r["类型"], 0) + 1
    md = ["# 用例表（caseflow export 从真实在跑的数据和代码生成，勿手改）", "",
          f"共 {len(rows)} 条：" + "，".join(f"{k} {v}" for k, v in stats.items()), "",
          "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    md += ["| " + " | ".join(_cell(r[c]) for c in COLUMNS) + " |" for r in rows]
    (out / "用例表.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"export：{len(rows)} 条用例 → {out / '用例表.csv'}、{out / '用例表.md'}")
    return 1 if any(r["状态"] == "错误" for r in rows) else 0
