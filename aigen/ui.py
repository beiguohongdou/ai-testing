"""UI 用例 IR：步骤只能调用页面对象已有的方法，YAML 里不允许出现定位器。"""
import ast
import re
from typing import Any, Literal

from pydantic import Field, model_validator

from aigen.ir import Pending, Strict, check_regex, check_source, check_values, func_name, is_guess
from aigen.runtime import cached, profile, root


@cached
def po_index() -> dict[str, tuple[str, dict[str, tuple[int, int, bool]]]]:
    """扫描页面对象目录：{类名: (模块, {方法: (必填参数数, 参数总数, 是否有 return)})}，含从父类继承的方法。
    父类必须也在页面对象目录里；来自第三方库的父类看不到源码，它的方法不会进清单。"""
    page_dir = profile().ui.page_dir
    own, bases, modules = {}, {}, {}
    for f in sorted((root() / page_dir).glob("*.py")):
        for cls in ast.parse(f.read_text(encoding="utf-8")).body:
            if not isinstance(cls, ast.ClassDef):
                continue
            methods = {}
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef) and not fn.name.startswith("_"):
                    n = len(fn.args.args) - 1
                    returns = any(isinstance(x, ast.Return) and x.value is not None for x in ast.walk(fn))
                    methods[fn.name] = (n - len(fn.args.defaults), n, returns)
            own[cls.name] = methods
            bases[cls.name] = [b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in cls.bases]
            modules[cls.name] = f"{page_dir.replace('/', '.')}.{f.stem}"

    def all_methods(name: str, seen: frozenset = frozenset()) -> dict:
        merged = {}
        for b in bases.get(name, []):
            if b in own and b not in seen:
                merged |= all_methods(b, seen | {name})
        return merged | own[name]  # 子类覆盖父类

    return {name: (modules[name], all_methods(name)) for name in own}


def check_call(ref: str, args: list, need_return: bool) -> None:
    cls, _, method = ref.partition(".")
    index = po_index()
    if cls not in index:
        raise ValueError(f"{profile().ui.page_dir}/ 里没有页面对象 {cls}")
    methods = index[cls][1]
    if method not in methods:
        raise ValueError(f"{cls} 没有方法 {method}，可用：{sorted(methods)}；缺方法请先读 DOM 给页面对象新增并自检")
    lo, hi, returns = methods[method]
    if not lo <= len(args) <= hi:
        raise ValueError(f"{ref} 需要 {lo}～{hi} 个参数，给了 {len(args)} 个")
    if need_return and not returns:
        raise ValueError(f"{ref} 没有 return，不能作为断言目标（断言目标要返回 Locator）")
    check_values(args)


class UiStep(Strict):
    call: str = Field(pattern=r"^[A-Z]\w*\.\w+$")
    args: list[Any] = []

    @model_validator(mode="after")
    def _po(self):
        check_call(self.call, self.args, need_return=False)
        return self


class UiExpect(Strict):
    target: str = Field(pattern=r"^(url|[A-Z]\w*\.\w+)$")
    args: list[Any] = []
    op: Literal["visible", "not_visible", "count", "contains_text", "url_matches"]
    value: Any = None
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _shape(self):
        if (self.target == "url") != (self.op == "url_matches"):
            raise ValueError("url 只能配 url_matches，url_matches 只能用于 url")
        if self.op in ("visible", "not_visible") and self.value is not None:
            raise ValueError(f"op={self.op} 不需要 value")
        if self.op == "count" and not isinstance(self.value, int):
            raise ValueError("op=count 的 value 必须是整数")
        if self.op in ("contains_text", "url_matches") and not isinstance(self.value, str):
            raise ValueError(f"op={self.op} 的 value 必须是字符串")
        if self.op == "url_matches":
            check_regex(self.value)
        else:
            check_call(self.target, self.args, need_return=True)
        check_source(self.source)
        return self


class UiCase(Strict):
    case_id: str = Field(pattern=r"^[A-Z]+(-[A-Z0-9]+)+$")
    title: str = Field(min_length=1)
    req_ref: str = Field(min_length=1)
    design_method: Literal["equivalence", "boundary", "decision", "state", "scenario"]
    steps: list[UiStep] = Field(min_length=1)
    expected: list[UiExpect] = Field(min_length=1)
    known_issue: str | None = None
    pending: list[Pending] = []

    @model_validator(mode="after")
    def _guess(self):
        if not self.pending and any(is_guess(e.source) for e in self.expected):
            raise ValueError("source 里有「推测」，必须在 pending 里说明并等人工确认")
        return self


class UiSuite(Strict):
    kind: Literal["ui"]
    module: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    cases: list[UiCase] = Field(min_length=1)


def var_name(cls: str) -> str:
    """LoginPage → login_page"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", cls).lower()


def render_ui_suite(s: UiSuite, py) -> str:
    """py 由 render.py 传入（值 → Python 表达式），避免循环导入。"""
    fixture = profile().ui.fixture
    used: list[str] = []
    funcs = []
    ready = [c for c in s.cases if not c.pending]
    for c in ready:
        classes = []
        for ref in [st.call for st in c.steps] + [e.target for e in c.expected if e.target != "url"]:
            cls = ref.split(".")[0]
            if cls not in classes:
                classes.append(cls)
        used += [x for x in classes if x not in used]
        lines = []
        if c.known_issue:
            lines.append(f"@pytest.mark.xfail(strict=True, reason={('已知缺陷：' + c.known_issue)!r})")
        lines += [
            f"@title({f'{c.case_id} {c.title}'!r})",
            f"def {func_name(c.case_id)}({fixture}: Page):",
            f'    """{c.req_ref}"""',
        ]
        lines += [f"    {var_name(x)} = {x}({fixture})" for x in classes]
        for st in c.steps:
            cls, method = st.call.split(".")
            lines.append(f"    {var_name(cls)}.{method}({', '.join(py(a) for a in st.args)})")
        for e in c.expected:
            if e.op == "url_matches":
                lines.append(f"    expect({fixture}).to_have_url(re.compile({e.value!r}), timeout=ui_timeout())")
                continue
            cls, method = e.target.split(".")
            loc = f"{var_name(cls)}.{method}({', '.join(py(a) for a in e.args)})"
            check = {
                "visible": "to_be_visible(timeout=ui_timeout())",
                "not_visible": "not_to_be_visible(timeout=ui_timeout())",
                "count": f"to_have_count({e.value}, timeout=ui_timeout())",
                "contains_text": f"to_contain_text({e.value!r}, timeout=ui_timeout())",
            }[e.op]
            lines.append(f"    expect({loc}).{check}")
        funcs.append("\n".join(lines))

    from aigen.render import runtime_names

    index = po_index()
    imports = "".join(f"from {index[x][0]} import {x}\n" for x in sorted(used))
    has_url = any(e.op == "url_matches" for c in ready for e in c.expected)
    runtime = runtime_names("\n".join(funcs))
    return (
        f"# 由 aigen 生成，勿手改：改 cases/{s.module}.yaml 后运行 python -m aigen render\n"
        + ("import re\n\n" if has_url else "")
        + ("import pytest\n" if any(c.known_issue for c in ready) else "")
        + "from playwright.sync_api import Page, expect\n\n"
        f"from aigen.runtime import {', '.join(sorted(runtime))}\n"
        f"{imports}\n\n"
        + "\n\n\n".join(funcs)
        + "\n"
    )
