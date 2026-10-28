"""YAML 用例 → 人看的测试用例文档（纯程序导出，和 YAML 永远一致）。

    python -m aigen export   # 写到 aigen.yaml 的 export_dir：用例表.md、用例表.csv（Excel 可打开）、验收覆盖.md
"""
import csv
import json

from aigen.ir import ac_ids, case_files, load, material_text
from aigen.runtime import profile, root

METHODS = {"equivalence": "等价类", "boundary": "边界值", "decision": "判定表", "state": "状态迁移", "scenario": "场景法"}
OPS = {
    "eq": "等于", "exists": "存在", "not_exists": "不存在", "contains": "包含", "len_gt": "长度大于",
    "has_item": "有一项满足", "every": "每一项满足", "every_between": "每一项在区间内", "sorted": "排序为",
    "matches": "匹配正则", "sha256": "SHA-256 为", "matches_contract": "符合契约结构",
    "visible": "可见", "not_visible": "不可见", "count": "数量为", "contains_text": "包含文字", "url_matches": "地址匹配",
}
COLUMNS = ["用例编号", "标题", "类型", "模块", "验收编号", "需求追溯", "设计方法", "前置条件", "步骤", "预期结果", "出处", "状态", "说明"]


def text(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def request_text(r) -> str:
    s = f"{r.method} {r.path}"
    for name, v in (("headers", r.headers), ("params", r.params), ("json", r.body), ("form", r.form)):
        if v is not None:
            s += f" {name}={text(v)}"
    return s


def expect_text(e) -> str:
    if e.target == "status":
        return f"状态码 = {e.value}"
    args = f"({', '.join(text(a) for a in e.args)})" if getattr(e, "args", None) else ""
    value = "" if e.value is None else f" {text(e.value)}"
    return f"{e.target}{args} {OPS[e.op]}{value}"


def numbered(steps: list[str]) -> str:
    """步骤编号；缩进的「取值」行跟在上一步后面，不占编号。"""
    out, n = [], 0
    for s in steps:
        if s.startswith(" "):
            out.append(s)
        else:
            n += 1
            out.append(f"{n}. {s}")
    return "\n".join(out)


def rows() -> list[dict]:
    out = []
    for p in case_files():
        suite = load(p)
        for c in suite.cases:
            pre, steps, expected = [], [], []
            if suite.kind == "ui":
                pre.append(f"打开 {profile().ui.fixture}")
                steps = [f"{st.call}({', '.join(text(a) for a in st.args)})" for st in c.steps]
                expected = [expect_text(e) for e in c.expected]
                sources = [e.source for e in c.expected]
            else:
                pre.append("未登录" if c.precondition == "anonymous" else f"以 {c.precondition} 登录")
                pre += [f"{k} = {text(v)}" for k, v in c.let.items()]
                sources = []
                for s in c.steps:
                    steps.append(request_text(s.request) + (f"  # {s.name}" if s.name else ""))
                    expected.append("；".join(expect_text(e) for e in s.expected))
                    sources += [e.source for e in s.expected]
                    steps += [f"  取值 {k} ← {text(v if isinstance(v, str) else v.model_dump(by_alias=True))}" for k, v in s.extract.items()]
                if c.cleanup:
                    steps.append("清理：" + "；".join(request_text(r) for r in c.cleanup) + (f"（以 {c.cleanup_as}）" if c.cleanup_as else ""))
            status = "待确认" if c.pending else "已知缺陷" if c.known_issue else "可运行"
            note = "；".join(f"[{x.type}] {x.note}" for x in c.pending) or (c.known_issue or "")
            out.append({
                "用例编号": c.case_id, "标题": c.title, "类型": "UI" if suite.kind == "ui" else "接口", "模块": suite.module,
                "验收编号": "、".join(ac_ids(c.req_ref)), "需求追溯": c.req_ref, "设计方法": METHODS[c.design_method],
                "前置条件": "\n".join(pre), "步骤": numbered(steps),
                "预期结果": "\n".join(expected), "出处": "\n".join(dict.fromkeys(sources)), "状态": status, "说明": note,
                "_endpoints": [] if suite.kind == "ui" else [f"{s.request.method} {s.request.path}" for s in c.steps],
            })
    return out


def md_cell(v: str) -> str:
    return v.replace("|", "\\|").replace("\n", "<br>")


def coverage(data: list[dict]) -> str:
    lines = ["# 验收覆盖矩阵（由 python -m aigen export 生成，勿手改）", ""]
    for m in profile().materials:
        if m.kind != "prd":
            continue
        src = material_text(m.name)
        lines += [f"## {m.name}：`{m.path}`", "", "| 验收 | 内容 | 可运行用例 | 待确认用例 | 涉及接口 |", "|---|---|---|---|---|"]
        for ac in dict.fromkeys(ac_ids(src)):
            desc = next((ln for ln in src.splitlines() if ac in ln), "")
            desc = desc.replace(ac, "").strip(" |").replace("|", " ").strip()
            hit = [r for r in data if ac in r["验收编号"].split("、")]
            ready = [r["用例编号"] for r in hit if r["状态"] != "待确认"]
            todo = [r["用例编号"] for r in hit if r["状态"] == "待确认"]
            apis = sorted({e for r in hit for e in r["_endpoints"]} | ({"UI"} if any(r["类型"] == "UI" for r in hit) else set()))
            mark = ", ".join(ready) or (f"豁免：{profile().waive[ac]}" if ac in profile().waive else "**未覆盖**")
            lines.append(f"| {ac} | {md_cell(desc)} | {mark} | {', '.join(todo)} | {md_cell(chr(10).join(apis))} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    data = rows()
    out = root() / profile().export_dir
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "用例表.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(data)
    md = ["# 测试用例表（由 python -m aigen export 从 cases/*.yaml 生成，勿手改）", "",
          f"共 {len(data)} 条：" + "，".join(f"{s} {sum(r['状态'] == s for r in data)}" for s in ("可运行", "已知缺陷", "待确认")), "",
          "| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    md += ["| " + " | ".join(md_cell(r[c]) for c in COLUMNS) + " |" for r in data]
    (out / "用例表.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (out / "验收覆盖.md").write_text(coverage(data), encoding="utf-8")
    print(f"export: {len(data)} 条用例 -> {out.relative_to(root())}/（用例表.md、用例表.csv、验收覆盖.md）")
    return 0
