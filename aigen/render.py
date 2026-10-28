"""IR → pytest 代码（纯模板翻译，不调用 AI）。

    python -m aigen render [cases/xxx.yaml ...]   # 默认渲染 cases/ 下全部
"""
from pathlib import Path
from typing import Any

from aigen.ir import REF, VAR, Case, Pick, Request, Step, Suite, case_files, check_contract_ref, func_name, gen_root, load, out_path, step_refs


def ref_expr(ref: str) -> str:
    """${...} → Python 表达式。"""
    name, _, arg = ref.partition(":")
    if name == "today":
        return f"gen.today({arg!r})"
    if name == "unique":
        return "gen.unique()"
    if name == "len":
        return f"gen.text({int(arg)})"
    if ref.isupper():
        return f"const({ref!r})"
    return ref


def py(v: Any) -> str:
    """IR 值 → Python 表达式。整串是一个 ${...} 时直接用表达式，嵌在字符串里时拼接。"""
    if isinstance(v, dict):
        return "{" + ", ".join(f"{py(k)}: {py(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(py(x) for x in v) + "]"
    if isinstance(v, str) and (m := VAR.match(v)):
        return ref_expr(m.group(1))
    if isinstance(v, str) and REF.search(v):
        pieces = []
        for i, part in enumerate(REF.split(v)):
            if i % 2:
                expr = ref_expr(part)
                pieces.append(expr if expr.startswith("gen.") else f"str({expr})")
            elif part:
                pieces.append(repr(part))
        return " + ".join(pieces)
    return repr(v)


def accessor(parts: list[str], root: str = "body") -> str:
    """['data', '0', 'name'] → body['data'][0]['name']"""
    return root + "".join(f"[{p}]" if p.isdigit() else f"[{p!r}]" for p in parts)


def item_field(path: str, root: str = "item") -> str:
    """'category.id' → item['category']['id']"""
    return accessor(path.split("."), root)


RUNTIME = ("assert_contract", "client", "const", "matches", "on_fail", "remember", "sha256", "title", "ui_timeout",
           "upload", "xml_body", "xml_text")


def runtime_names(code: str) -> list[str]:
    """生成代码里实际用到的 aigen.runtime 名字（只 import 用到的）。"""
    names = [n for n in RUNTIME if f"{n}(" in code]
    return sorted(names + (["gen"] if "gen." in code else []))


def render_request(r: Request, client: str = "api") -> str:
    path = repr(r.path)
    if REF.search(r.path):
        path = "f" + repr(REF.sub(r"{\1}", r.path))
    args = [repr(r.method), path]
    for kw, v in (("headers", r.headers), ("params", r.params), ("json", r.body), ("data", r.form)):
        if v is not None:
            args.append(f"{kw}={py(v)}")
    if r.files:
        args.append("files={" + ", ".join(f"{k!r}: upload({v!r})" for k, v in r.files.items()) + "}")
    return f"{client}.request({', '.join(args)})"


PARSE = {"json": "resp.json()", "text": "resp.text", "bytes": "resp.content", "xml": "xml_body(resp)"}


def render_assert(e, body_type: str = "json") -> list[str]:
    if e.target.startswith("header."):
        h = e.target.split(".", 1)[1]
        return {
            "eq": [f"assert resp.headers.get({h!r}) == {py(e.value)}"],
            "contains": [f"assert {py(e.value)} in resp.headers.get({h!r}, '')"],
            "exists": [f"assert {h!r} in resp.headers"],
            "not_exists": [f"assert {h!r} not in resp.headers"],
        }[e.op]
    parts = e.target.split(".")[1:]
    if e.op == "matches_contract":
        material, method, path, code = check_contract_ref(e.value)
        return [f"assert_contract(body, {material!r}, {method!r}, {path!r}, {code!r})"]
    if e.op == "sha256":
        return [f"assert sha256(body) == {e.value!r}"]
    if body_type == "xml":
        xpath = "/".join(parts) or "."
        return {
            "eq": [f"assert xml_text(body, {xpath!r}) == {py(e.value)}"],
            "contains": [f"assert {py(e.value)} in (xml_text(body, {xpath!r}) or '')"],
            "matches": [f"assert matches({e.value!r}, xml_text(body, {xpath!r}))"],
            "exists": [f"assert body.find({xpath!r}) is not None"],
            "not_exists": [f"assert body.find({xpath!r}) is None"],
        }[e.op]
    if e.op == "matches":
        return [f"assert matches({e.value!r}, {accessor(parts)})"]
    if e.op == "exists":
        return [f"assert {parts[-1]!r} in {accessor(parts[:-1])}"]
    if e.op == "not_exists":
        return [f"assert {parts[-1]!r} not in {accessor(parts[:-1])}"]
    if e.op == "eq":
        return [f"assert {accessor(parts)} == {py(e.value)}"]
    if e.op == "contains":
        return [f"assert {py(e.value)} in {accessor(parts)}"]
    if e.op == "len_gt":
        return [f"assert len({accessor(parts)}) > {py(e.value)}"]
    if e.op == "has_item":
        cond = " and ".join(f"item.get({k!r}) == {py(v)}" for k, v in e.value.items())
        return [f"assert any({cond} for item in {accessor(parts)})"]
    # 空列表时 all() 恒为真，先断言非空，避免假绿
    lines = [f'assert len({accessor(parts)}) > 0, "列表为空，{e.op} 断言没有意义"']
    if e.op == "every":
        cond = " and ".join(f"{item_field(k)} == {py(v)}" for k, v in e.value.items())
        lines.append(f"assert all({cond} for item in {accessor(parts)})")
    elif e.op == "every_between":
        cond = " and ".join(f"{lo!r} <= {item_field(k)} <= {hi!r}" for k, (lo, hi) in e.value.items())
        lines.append(f"assert all({cond} for item in {accessor(parts)})")
    else:
        vals = f"[{item_field(e.value['by'])} for item in {accessor(parts)}]"
        reverse = ", reverse=True" if e.value["order"] == "desc" else ""
        lines.append(f"assert {vals} == sorted({vals}{reverse})")
    return lines


def render_extract(var: str, src: str | Pick) -> list[str]:
    if isinstance(src, str):
        return [f"{var} = {accessor(src.split('.')[1:])}"]
    cond = " and ".join(f"{item_field(k)} == {py(v)}" for k, v in src.where.items())
    where = ", ".join(f"{k}={v!r}" for k, v in src.where.items())
    return [
        f"found = next((item for item in {accessor(src.from_.split('.')[1:])} if {cond}), None)",
        f'assert found is not None, "{src.from_} 里没有 {where} 的项"',
        f"{var} = {item_field(src.take, 'found')}",
    ]


def render_step(s: Step) -> list[str]:
    lines = [f"resp = {render_request(s.request)}"]
    body_checks = [e for e in s.expected if e.target.startswith("body")]
    header_checks = [e for e in s.expected if e.target.startswith("header.")]
    # 先断言 body/响应头 再断言 status（WF-7 防假绿），顺序由模板固定；on_fail 让失败时也看到状态码
    if body_checks or header_checks or s.extract:
        lines.append("with on_fail(resp):")
        inner = [f"body = {PARSE[s.body_type]}"] if body_checks or s.extract else []
        for e in body_checks + header_checks:
            inner += render_assert(e, s.body_type)
        for var, src in s.extract.items():
            inner += render_extract(var, src)
        lines += ["    " + x for x in inner]
    for e in s.expected:
        if e.target == "status":
            lines.append(f"assert resp.status_code == {py(e.value)}, resp.text")
    return lines


def render_cleanup(c: Case) -> list[str]:
    """清理 best-effort：失败只记 warning，不让用例失败（WF-8）。"""
    lines = []
    name, login = "api", []
    if c.cleanup_as:
        name = "cleaner"
        login = [f"    cleaner = client({c.cleanup_as!r})"]
    for r in c.cleanup:
        refs = sorted(step_refs(r))
        body = [
            "try:",
            *login,
            f"    r = {render_request(r, name)}",
            "    if r.status_code >= 400:",
            f'        logger.warning("清理失败 {c.case_id}: %s %s", r.status_code, r.text[:200])',
            "except Exception as e:",
            f'    logger.warning("清理异常 {c.case_id}: %s", e)',
        ]
        if refs:
            lines.append("if " + " and ".join(f"{v} is not None" for v in refs) + ":")
            body = ["    " + x for x in body]
        lines += body
    return lines


def render_case(c: Case) -> str:
    head = []
    if c.known_issue:
        head.append(f"@pytest.mark.xfail(strict=True, reason={('已知缺陷：' + c.known_issue)!r})")
    role = "" if c.precondition == "anonymous" else repr(c.precondition)
    head += [
        f"@title({f'{c.case_id} {c.title}'!r})",
        f"def {func_name(c.case_id)}():",
        f'    """{c.req_ref}"""',
        f"    api = client({role})",
    ]
    head += [f"    {var} = remember({var!r}, {py(v)})" for var, v in c.let.items()]

    multi = len(c.steps) > 1
    body: list[str] = []
    for i, s in enumerate(c.steps, 1):
        if multi:
            body.append(f"# 步骤{i}" + (f"：{s.name}" if s.name else ""))
        body += render_step(s)

    if not c.cleanup:
        return "\n".join(head + ["    " + x for x in body])
    # 清理放在 finally：断言中途失败也会执行；变量先置 None，没创建成功就跳过对应清理
    init = [f"    {v} = None" for v in sorted(step_refs(c.cleanup) - set(c.let))]
    return "\n".join(
        head + init + ["    try:"] + ["        " + x for x in body]
        + ["    finally:"] + ["        " + x for x in render_cleanup(c)]
    )


def render_suite(s: Suite) -> str:
    """只渲染 pending 为空的用例；有待确认项的用例不生成代码。"""
    if s.kind == "ui":
        from aigen.ui import render_ui_suite

        return render_ui_suite(s, py)
    ready = [c for c in s.cases if not c.pending]
    funcs = "\n\n\n".join(render_case(c) for c in ready)
    has_cleanup = any(c.cleanup for c in ready)
    return (
        f"# 由 aigen 生成，勿手改：改 cases/{s.module}.yaml 后运行 python -m aigen render\n"
        + ("import logging\n\n" if has_cleanup else "")
        + ("import pytest\n\n" if any(c.known_issue for c in ready) else "")
        + f"from aigen.runtime import {', '.join(runtime_names(funcs))}\n"
        + ("\nlogger = logging.getLogger(__name__)\n" if has_cleanup else "")
        + f"\n\n{funcs}\n"
    )


def main(paths: list[Path] | None = None) -> int:
    for p in paths or case_files():
        suite = load(p)
        for c in suite.cases:
            if c.pending:
                print(f"[SKIP] {c.case_id}: 待确认 {[f'{x.type}：{x.note}' for x in c.pending]}，未生成代码")
        if all(c.pending for c in suite.cases):
            continue
        out = out_path(suite)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_suite(suite), encoding="utf-8")
        print(f"[OK] {p.name} -> {out.relative_to(gen_root().parent)}")
    return 0
