"""用例 IR：结构校验（Schema Gate）+ 追溯检查。

    python -m aigen validate [cases/xxx.yaml ...]   # 默认校验 cases/ 下全部
    python -m aigen trace                           # IR 与 testcase/*_gen 是否一致 + PRD 验收覆盖
"""
import ast
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aigen.runtime import cached, const, consts, contract_doc, has_const, profile, response_schema, root

VAR = re.compile(r"^\$\{([^}]+)\}$")
REF = re.compile(r"\$\{([^}]+)\}")
# ${大写} = 常量（环境变量优先）；${小写} = let / extract 定义的变量；${today:..} ${unique} ${len:N} = 动态数据
CONST = r"[A-Z][A-Z0-9_]*"
STEP_VAR = r"[a-z][a-z0-9_]*"
GENS = {"today": r"([+-]\d+[ymd])+", "unique": "", "len": r"[1-9]\d*"}
BUILTIN_SOURCES = ("实测", "DOM 实测", "推测")
SECRET_HINTS = ("PASSWORD", "PWD", "SECRET", "TOKEN", "KEY", "EMAIL")


def cases_dir() -> Path:
    return root() / "cases"


def gen_root() -> Path:
    return root() / "testcase"


def ac_ids(text: str) -> list[str]:
    return re.findall(profile().ac_pattern, text)


@cached
def secrets() -> frozenset[str]:
    """账号密码只能写成 ${NAME} 引用常量，明文出现在 IR 里即拒绝。"""
    names = {n for pair in profile().api.roles.values() for n in pair}
    if consts() is not None:
        names |= {k for k in vars(consts()) if k.isupper() and any(h in k for h in SECRET_HINTS)}
    return frozenset(str(const(n)) for n in names if has_const(n) and isinstance(const(n), str) and const(n))


def is_var(ref: str) -> bool:
    return bool(re.fullmatch(STEP_VAR, ref)) and ref not in GENS


def check_ref(ref: str) -> None:
    name, _, arg = ref.partition(":")
    if name in GENS:
        if not re.fullmatch(GENS[name], arg):
            raise ValueError(f"${{{ref}}} 参数不对，示例：${{today:-18y+1d}} ${{unique}} ${{len:251}}")
    elif re.fullmatch(CONST, ref):
        if not has_const(ref):
            raise ValueError(f"${{{ref}}} 不在环境变量里，也不在常量模块 {profile().constants} 中")
    elif not is_var(ref):
        raise ValueError(f"${{{ref}}}：常量全大写，变量小写下划线，动态数据见 today / unique / len")


def check_values(v: Any) -> Any:
    """递归检查所有 ${...} 引用，并拒绝账号密码明文。"""
    if isinstance(v, dict):
        for x in v.values():
            check_values(x)
    elif isinstance(v, list):
        for x in v:
            check_values(x)
    elif isinstance(v, str):
        for ref in REF.findall(v):
            check_ref(ref)
        if v in secrets():
            raise ValueError("出现账号/密码明文，请改用 ${NAME} 引用常量")
    return v


def step_refs(*objs: Any) -> set[str]:
    """收集引用到的变量（${小写}），含路径里内嵌的。"""
    found: set[str] = set()

    def walk(v):
        if isinstance(v, BaseModel):
            walk(v.model_dump())
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            found.update(n for n in REF.findall(v) if is_var(n))

    for o in objs:
        walk(o)
    return found


@cached
def material_text(name: str) -> str:
    m = next(m for m in profile().materials if m.name == name)
    p = root() / m.path
    if not p.exists():
        raise ValueError(f"aigen.yaml 里的材料 {m.name} 不存在：{m.path}")
    return p.read_text(encoding=profile().encoding) if p.is_file() else ""


CONTRACT_REF = r"\s+(GET|POST|PUT|PATCH|DELETE)\s+(/[\w\-/{}.]*)(?:\s+responses\.(\d{3}))?"


def check_contract_ref(text: str) -> tuple[str, str, str, str]:
    """'契约名 POST /messages responses.200' → 校验存在并有结构定义，返回 (材料, 方法, 路径, 状态码)。"""
    for m in profile().materials:
        if m.kind == "openapi" and (hit := re.fullmatch(re.escape(m.name) + CONTRACT_REF, text.strip())) and hit.group(3):
            method, path, code = hit.groups()
            if response_schema(m.name, method, path, code) is None:
                raise ValueError(f"契约 {method} {path} responses.{code} 不存在或没有定义响应结构")
            return m.name, method, path, code
    raise ValueError("matches_contract 的 value 须形如「OpenAPI POST /messages responses.200」（契约材料名 + 方法 + 路径 + 状态码）")


def check_source(src: str) -> None:
    """出处须以已登记材料开头；引用契约的接口/状态码、PRD 的验收编号要真实存在。"""
    parts = [p.strip() for p in src.split("；")]
    names = [m.name for m in profile().materials]
    if not parts[0].startswith(tuple(names) + BUILTIN_SOURCES):
        raise ValueError(f"source 须以 {names + list(BUILTIN_SOURCES)} 之一开头，实际 {parts[0]!r}")
    for part in parts:
        for m in profile().materials:
            if not part.startswith(m.name):
                continue
            if m.kind == "openapi":
                material_text(m.name)
                hit = re.match(re.escape(m.name) + CONTRACT_REF, part)
                if hit:
                    method, path, code = hit.groups()
                    op = (contract_doc(m.name).get("paths") or {}).get(path, {}).get(method.lower())
                    if op is None:
                        raise ValueError(f"出处引用的 {method} {path} 在契约里不存在")
                    if code and code not in {str(k) for k in op.get("responses", {})}:
                        raise ValueError(f"契约 {method} {path} 没有声明 responses.{code}")
            elif m.kind == "prd":
                acs = ac_ids(part)
                if not acs:
                    raise ValueError(f"PRD 出处要写验收编号（格式 {profile().ac_pattern}）：{part!r}")
                if missing := [a for a in acs if a not in ac_ids(material_text(m.name))]:
                    raise ValueError(f"PRD 里没有验收 {missing}")


def check_regex(pattern: Any) -> None:
    try:
        re.compile(str(pattern))
    except re.error as e:  # re.error 不是 ValueError，不转换的话 pydantic 不会把它当成校验错误
        raise ValueError(f"正则写错了 {pattern!r}：{e}") from None


def is_guess(src: str) -> bool:
    return any(p.strip().startswith("推测") for p in src.split("；"))


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Pending(Strict):
    type: Literal["契约缺失", "需求不一致", "断言能力", "测试数据", "数据清理", "其他"]
    note: str = Field(min_length=1)


class Request(Strict):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(pattern=rf"^/([\w\-/.]|\$\{{{STEP_VAR}\}})*$")
    headers: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    body: dict[str, Any] | list | None = Field(None, alias="json")
    form: dict[str, Any] | None = None  # application/x-www-form-urlencoded
    files: dict[str, str] | None = None  # multipart 上传：{字段名: 文件路径（相对项目根）}，可与 form 同用

    @model_validator(mode="after")
    def _values(self):
        if self.body is not None and (self.form is not None or self.files is not None):
            raise ValueError("json 不能和 form / files 同时用")
        for v in (self.headers, self.params, self.body, self.form):
            check_values(v)
        for field, path in (self.files or {}).items():
            if not (root() / path).is_file():
                raise ValueError(f"上传文件 {field}: {path} 不存在（路径相对项目根）")
        return self


# 不同响应类型能用的断言：json 用 body.字段；text / bytes 只能断言整个 body；xml 用 body.元素/子元素
OPS_BY_BODY = {
    "json": {"eq", "exists", "not_exists", "contains", "matches", "len_gt", "has_item", "every", "every_between", "sorted", "matches_contract"},
    "text": {"eq", "contains", "matches", "len_gt"},
    "bytes": {"len_gt", "sha256"},
    "xml": {"eq", "exists", "not_exists", "contains", "matches"},
}


class Expect(Strict):
    target: str = Field(pattern=r"^(status|header\.[\w\-]+|body(\.\w+)*)$")
    op: Literal["eq", "exists", "not_exists", "contains", "matches", "len_gt", "has_item", "every", "every_between",
                "sorted", "sha256", "matches_contract"]
    value: Any = None
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _shape(self):
        has_value = "value" in self.model_fields_set
        if self.op == "matches":
            check_regex(self.value)
        if self.op == "sha256" and not re.fullmatch(r"[0-9a-f]{64}", str(self.value)):
            raise ValueError("sha256 的 value 须是 64 位小写十六进制")
        if self.op == "matches_contract":
            if self.target != "body":
                raise ValueError("matches_contract 只能用于 target: body")
            check_contract_ref(str(self.value))
        if self.op in ("exists", "not_exists") and has_value:
            raise ValueError(f"op={self.op} 不需要 value")
        if self.op in ("exists", "not_exists") and self.target == "body":
            raise ValueError(f"op={self.op} 要指明字段，如 body.access_token")
        if self.target.startswith("header.") and self.op not in ("eq", "contains", "exists", "not_exists"):
            raise ValueError("响应头只支持 eq / contains / exists / not_exists")
        if self.op in ("has_item", "every", "every_between") and not (isinstance(self.value, dict) and self.value):
            raise ValueError(f"{self.op} 的 value 必须是非空字典，如 {{name: ForgeFlex Tools}}")
        if self.op in ("every", "every_between") and not all(re.fullmatch(r"\w+(\.\w+)*", k) for k in self.value):
            raise ValueError(f"{self.op} 的字段须形如 price 或 category.id")
        if self.op == "every_between" and not all(
            isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v) and v[0] <= v[1]
            for v in self.value.values()
        ):
            raise ValueError("every_between 的值须是 [下限, 上限]，如 {price: [10, 30]}")
        if self.op == "sorted" and not (
            isinstance(self.value, dict) and set(self.value) == {"by", "order"} and self.value["order"] in ("asc", "desc")
        ):
            raise ValueError("sorted 的 value 须形如 {by: price, order: asc}")
        if self.op not in ("exists", "not_exists") and not has_value:
            raise ValueError(f"op={self.op} 必须给 value")
        if self.target == "status" and self.op != "eq":
            raise ValueError("status 只支持 op=eq")
        check_values(self.value)
        check_source(self.source)
        return self


class Pick(Strict):
    """按条件从列表里取值：{from: body, where: {name: Pliers}, take: id}"""
    from_: str = Field(alias="from", pattern=r"^body(\.\w+)*$")
    where: dict[str, Any] = Field(min_length=1)
    take: str = Field(pattern=r"^\w+(\.\w+)*$")

    @model_validator(mode="after")
    def _where(self):
        if not all(re.fullmatch(r"\w+(\.\w+)*", k) for k in self.where):
            raise ValueError("where 的字段须形如 name 或 category.slug")
        check_values(self.where)
        return self


class Step(Strict):
    name: str = ""
    request: Request
    body_type: Literal["json", "text", "bytes", "xml"] = "json"
    expected: list[Expect] = Field(min_length=1)
    extract: dict[str, str | Pick] = {}

    @model_validator(mode="after")
    def _extract(self):
        for e in self.expected:
            if e.target.startswith("body") and e.op not in OPS_BY_BODY[self.body_type]:
                raise ValueError(f"body_type={self.body_type} 不支持 op={e.op}，可用：{sorted(OPS_BY_BODY[self.body_type])}")
            if e.target.startswith("body.") and self.body_type in ("text", "bytes"):
                raise ValueError(f"body_type={self.body_type} 只能断言整个 body，不能写 {e.target}")
        if self.extract and self.body_type != "json":
            raise ValueError("extract 只支持 body_type=json")
        for var, path in self.extract.items():
            if not is_var(var):
                raise ValueError(f"extract 变量 {var!r} 须为小写下划线，且不能叫 {sorted(GENS)}")
            if isinstance(path, str) and not re.fullmatch(r"body(\.\w+)+", path):
                raise ValueError(f"extract {var}: {path!r} 须形如 body.data.0.id")
        return self


def check_role(role: str | None, allow_anonymous: bool) -> None:
    roles = list(profile().api.roles)
    if role is not None and role not in roles and not (allow_anonymous and role == "anonymous"):
        raise ValueError(f"身份 {role!r} 不在 aigen.yaml 的 api.roles 里：{roles}")


class Case(Strict):
    case_id: str = Field(pattern=r"^[A-Z]+(-[A-Z0-9]+)+$")
    title: str = Field(min_length=1)
    req_ref: str = Field(min_length=1)
    design_method: Literal["equivalence", "boundary", "decision", "state", "scenario"]
    precondition: str = "anonymous"
    # 用例开始时算一次、各步骤共用的值，如 {email: "aigen.${unique}@example.com"}
    let: dict[str, Any] = {}
    # 单请求写 request + expected（+ body_type）；多步写 steps，二选一
    request: Request | None = None
    body_type: Literal["json", "text", "bytes", "xml"] = "json"
    expected: list[Expect] | None = None
    steps: list[Step] | None = None
    cleanup: list[Request] = []
    # 清理需要的身份与用例本身不同时填写（如注册的用户只有管理员能删）
    cleanup_as: str | None = None
    no_body_reason: str | None = None
    # 已确认的缺陷（缺陷单号或说明）：生成 xfail(strict=True)，缺陷修好后会提示
    known_issue: str | None = None
    pending: list[Pending] = []

    @model_validator(mode="after")
    def _steps(self):
        check_role(self.precondition, allow_anonymous=True)
        check_role(self.cleanup_as, allow_anonymous=False)
        if (self.request is None) == (self.steps is None):
            raise ValueError("request 与 steps 二选一")
        if self.steps is not None and self.body_type != "json":
            raise ValueError("多步用例的 body_type 写在各个 step 里")
        if self.request is not None:
            if not self.expected:
                raise ValueError("单请求用例必须有 expected")
            self.steps = [Step(request=self.request, body_type=self.body_type, expected=self.expected)]
        elif self.expected:
            raise ValueError("多步用例的 expected 写在各个 step 里")

        for var, v in self.let.items():
            if not is_var(var):
                raise ValueError(f"let 变量 {var!r} 须为小写下划线，且不能叫 {sorted(GENS)}")
            check_values(v)
            if step_refs(v):
                raise ValueError(f"let {var} 只能用常量和动态数据，不能引用其他变量")
        defined: set[str] = set(self.let)
        for i, s in enumerate(self.steps):
            picks = [p for p in s.extract.values() if isinstance(p, Pick)]
            if missing := step_refs(s.request, s.expected, *picks) - defined:
                raise ValueError(f"steps.{i} 引用了前面没有定义的变量 {sorted(missing)}")
            if not any(e.target == "status" for e in s.expected):
                raise ValueError(f"steps.{i} 缺少 status 断言")
            if not (self.no_body_reason or s.extract or any(e.target.startswith("body") for e in s.expected)):
                raise ValueError(f"steps.{i} 只断言 status 有假绿风险：补 body 断言，或填写 no_body_reason")
            defined |= s.extract.keys()
        if missing := step_refs(self.cleanup) - defined:
            raise ValueError(f"cleanup 引用了未定义的变量 {sorted(missing)}")
        if self.cleanup_as and not self.cleanup:
            raise ValueError("填了 cleanup_as 却没有 cleanup")
        if not self.pending and any(is_guess(e.source) for s in self.steps for e in s.expected):
            raise ValueError("source 里有「推测」，必须在 pending 里说明并等人工确认")
        return self


class Suite(Strict):
    kind: Literal["api"] = "api"
    module: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    cases: list[Case] = Field(min_length=1)


def func_name(case_id: str) -> str:
    return "test_" + case_id.replace("-", "_")


def load(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("kind") == "ui":
        from aigen.ui import UiSuite

        return UiSuite.model_validate(data)
    return Suite.model_validate(data)


def case_files() -> list[Path]:
    return sorted(cases_dir().glob("*.yaml"))


def out_path(suite, base: Path | None = None) -> Path:
    """接口用例生成到 testcase/api_gen/，UI 用例生成到 testcase/ui_gen/。"""
    return (base or gen_root()) / f"{suite.kind}_gen" / f"test_{suite.module}.py"


def validate(paths: list[Path] | None = None) -> int:
    errors, seen = [], {}
    pending, known = defaultdict(list), []
    for p in paths or case_files():
        try:
            suite = load(p)
        except ValidationError as e:
            for err in e.errors():
                loc = ".".join(str(x) for x in err["loc"])
                errors.append(f"{p.name}: {loc}: {err['msg']}")
            continue
        for c in suite.cases:
            if c.case_id in seen:
                errors.append(f"{p.name}: case_id {c.case_id} 与 {seen[c.case_id]} 重复")
            seen[c.case_id] = p.name
            for item in c.pending:
                pending[item.type].append(f"{c.case_id}：{item.note}")
            if c.known_issue:
                known.append(f"{c.case_id}：{c.known_issue}")
    for t, items in pending.items():
        print(f"[PENDING][{t}] {len(items)} 条（render 会跳过）")
        for x in items:
            print(f"    {x}")
    for x in known:
        print(f"[KNOWN] {x}（生成 xfail）")
    for e in errors:
        print(f"[FAIL] {e}")
    n_pending = len({x.split('：')[0] for items in pending.values() for x in items})
    print(f"validate: {len(seen)} 条用例, {len(errors)} 个错误, 待确认 {n_pending} 条, 已知缺陷 {len(known)} 条")
    return 1 if errors else 0


def trace(cases: Path | None = None, base: Path | None = None, coverage: bool = True) -> int:
    from aigen.render import render_suite

    cases, base = cases or cases_dir(), base or gen_root()
    problems = []
    ir_funcs, covered = set(), set()
    for p in sorted(cases.glob("*.yaml")):
        suite = load(p)
        ready = [c for c in suite.cases if not c.pending]
        for c in ready:
            covered.update(ac_ids(c.req_ref))
        if not ready:
            continue
        ir_funcs |= {func_name(c.case_id) for c in ready}
        out = out_path(suite, base)
        if not out.exists():
            problems.append(f"{out.name} 未生成")
        elif out.read_text(encoding="utf-8") != render_suite(suite):
            problems.append(f"{out.name} 与 IR 不一致（被手改或 IR 改后未 render）")
    code_funcs = set()
    for f in base.glob("*_gen/test_*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        code_funcs |= {n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}
    problems += [f"孤儿代码 {n}：IR 里没有对应用例" for n in sorted(code_funcs - ir_funcs)]
    if coverage:
        for m in profile().materials:
            if m.kind == "prd":
                acs = sorted(set(ac_ids(material_text(m.name))) - covered - set(profile().waive))
                problems += [f"{m.name} 验收 {a} 没有可运行的用例（或在 aigen.yaml 的 waive 里说明原因）" for a in acs]
    for p in problems:
        print(f"[FAIL] {p}")
    print(f"trace: IR {len(ir_funcs)} 条, 代码 {len(code_funcs)} 条, {len(problems)} 个问题")
    return 1 if problems else 0
