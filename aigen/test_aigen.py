"""aigen 自检：校验器该拒的拒、生成器输出可用、trace / export / mutate 能工作。

不依赖任何真实项目：在临时目录里搭一个假项目（Swagger 2.0 YAML 契约、REQ-xx 编号的需求、
嵌套 token 登录、固定 API Key 头），换到哪个项目都能直接跑。

    python -m aigen selftest
"""
import copy
import csv
import json
import os
import sys
from datetime import date

import pytest
import requests
import yaml
from pydantic import ValidationError

from aigen import export, mutate, runtime
from aigen.ir import Suite, trace
from aigen.render import render_suite
from aigen.runtime import Client, const, gen, on_fail, profile
from aigen.ui import UiSuite

PROFILE = """
constants: demo_consts
ac_pattern: "REQ-\\\\d+"
api:
  base_url: DEMO_API
  headers: {X-Api-Key: "${DEMO_KEY}"}
  login: {path: /login, user_field: user, password_field: pwd, token_field: data.token}
  roles: {member: [DEMO_USER, DEMO_PWD], admin: [DEMO_ADMIN, DEMO_ADMIN_PWD]}
ui: {page_dir: pages, fixture: page}
materials:
  - {name: 契约, path: contract.yaml, kind: openapi}
  - {name: 需求, path: prd.md, kind: prd}
"""
CONSTS = """
DEMO_API = "http://demo.invalid/api/"
DEMO_USER = "member@demo.test"
DEMO_PWD = "Secret#1"
DEMO_ADMIN = "admin@demo.test"
DEMO_ADMIN_PWD = "Secret#2"
DEMO_KEY = "k-123"
"""
CONTRACT = """
swagger: "2.0"
paths:
  /login: {post: {responses: {200: {description: ok}}}}
  /me: {get: {responses: {200: {description: ok, schema: {$ref: "#/definitions/User"}}, 401: {description: unauthorized}}}}
  /carts: {post: {responses: {201: {description: created}}}}
  /carts/{id}: {get: {responses: {200: {description: ok}}}, delete: {responses: {204: {description: deleted}}}}
  /items: {get: {responses: {200: {description: ok}}}}
definitions:
  User:
    type: object
    required: [id]
    properties:
      id: {type: string}
      age: {type: integer, nullable: true}
"""
PRD = "| REQ-01 | 登录后能看到本人信息（测试账号 member@demo.test） |\n| REQ-02 | 购物车能创建和查询 |\n"
BASE_PAGE = '''
class BasePage:
    def open(self, path="/"): ...
'''
PAGES = '''
from pages.base_page import BasePage


class LoginPage(BasePage):
    def sign_in(self): ...
    def login(self, user, pwd): ...
    def title(self):
        return "locator"


class AccountPage:
    def title(self):
        return "locator"
'''
SRC = "实测 demo"


def case(case_id, title, req_ref, steps_or_request, **extra):
    c = {"case_id": case_id, "title": title, "req_ref": req_ref, "design_method": "equivalence", **steps_or_request, **extra}
    return c


# 断言区分力：同一接口一个成功、两个被拒；TC-MUT-02 的 body 断言和 F7 一样区分不了成功与失败
MUT_CASES = {"module": "mut", "cases": [
    case("TC-MUT-01", "创建成功", "需求 REQ-02", {"request": {"method": "POST", "path": "/carts"}, "expected": [
        {"target": "body.id", "op": "exists", "source": SRC},
        {"target": "status", "op": "eq", "value": 201, "source": "契约 POST /carts responses.201"}]}),
    case("TC-MUT-02", "被拒（弱断言）", "需求 REQ-02", {"request": {"method": "POST", "path": "/carts"}, "expected": [
        {"target": "body.message", "op": "exists", "source": SRC},
        {"target": "status", "op": "eq", "value": 422, "source": SRC}]}),
    case("TC-MUT-03", "被拒（强断言）", "需求 REQ-02", {"request": {"method": "POST", "path": "/carts"}, "expected": [
        {"target": "body.id", "op": "not_exists", "source": SRC},
        {"target": "status", "op": "eq", "value": 422, "source": SRC}]}),
    # 断言引用变量：extract 的值离线重算、let 的值来自录制
    case("TC-MUT-04", "变量重放", "需求 REQ-02", {"steps": [
        {"request": {"method": "POST", "path": "/carts"}, "extract": {"cart_id": "body.id"},
         "expected": [{"target": "status", "op": "eq", "value": 201, "source": SRC}]},
        {"request": {"method": "GET", "path": "/carts/${cart_id}"},
         "expected": [{"target": "body.id", "op": "eq", "value": "${cart_id}", "source": SRC},
                      {"target": "body.owner", "op": "eq", "value": "${email}", "source": SRC},
                      {"target": "status", "op": "eq", "value": 200, "source": SRC}]}]},
         let={"email": "u.${unique}@demo.test"}),
]}
MUT_RECORDS = [
    {"test": "test_TC_MUT_01", "kind": "step", "method": "POST", "status": 201, "body": {"id": "c1", "message": "ok"}},
    {"test": "test_TC_MUT_02", "kind": "step", "method": "POST", "status": 422, "body": {"message": ["bad"]}},
    {"test": "test_TC_MUT_03", "kind": "step", "method": "POST", "status": 422, "body": {"message": ["bad"]}},
    {"test": "test_TC_MUT_04", "kind": "var", "name": "email", "value": "u.abc@demo.test"},
    {"test": "test_TC_MUT_04", "kind": "step", "method": "POST", "status": 201, "body": {"id": "c9"}},
    {"test": "test_TC_MUT_04", "kind": "step", "method": "GET", "status": 200, "body": {"id": "c9", "owner": "u.abc@demo.test"}},
]


@pytest.fixture(scope="module", autouse=True)
def demo_project(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo_project")
    (root / "aigen.yaml").write_text(PROFILE, encoding="utf-8")
    (root / "demo_consts.py").write_text(CONSTS, encoding="utf-8")
    (root / "contract.yaml").write_text(CONTRACT, encoding="utf-8")
    (root / "prd.md").write_text(PRD, encoding="utf-8")
    (root / "pages").mkdir()
    (root / "pages" / "login_page.py").write_text(PAGES, encoding="utf-8")
    (root / "pages" / "base_page.py").write_text(BASE_PAGE, encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "a.txt").write_bytes(b"hello")
    (root / ".env").write_text("# 本机配置\nDEMO_FROM_DOTENV=\"dotenv-value\"\nAIGEN_LLM_API_KEY=\n", encoding="utf-8")
    (root / "cases").mkdir()
    (root / "cases" / "mut.yaml").write_text(yaml.safe_dump(MUT_CASES, allow_unicode=True), encoding="utf-8")
    old = os.environ.get("AIGEN_PROJECT")
    os.environ["AIGEN_PROJECT"] = str(root)
    sys.path.insert(0, str(root))
    runtime.reset()
    yield root
    sys.path.remove(str(root))
    sys.modules.pop("demo_consts", None)
    if old is None:
        os.environ.pop("AIGEN_PROJECT")
    else:
        os.environ["AIGEN_PROJECT"] = old
    runtime.reset()


GOOD = {
    "module": "demo",
    "cases": [case("TC-DEMO-01", "合法用例", "需求 REQ-01", {
        "request": {"method": "POST", "path": "/login", "json": {"user": "${DEMO_USER}"}},
        "expected": [
            {"target": "status", "op": "eq", "value": 200, "source": "契约 POST /login responses.200"},
            {"target": "body.data.token", "op": "exists", "source": SRC},
            {"target": "body.items", "op": "has_item", "value": {"name": "X"}, "source": "需求 REQ-01"},
        ]}, precondition="member")],
}


def bad(mutate_fn):
    d = copy.deepcopy(GOOD)
    mutate_fn(d["cases"][0])
    return d


def test_good_case_passes():
    Suite.model_validate(GOOD)


@pytest.mark.parametrize(
    "mutate_fn, reason",
    [
        (lambda c: c["expected"][1].pop("source"), "source"),
        (lambda c: c.update(expected=[c["expected"][0]]), "假绿"),
        (lambda c: c["request"]["json"].update(pwd="Secret#1"), "明文"),
        (lambda c: c["request"]["json"].update(user="${NOT_IN_CONSTS}"), "不在环境变量里"),
        (lambda c: c["expected"][2].update(value="X"), "has_item"),
        (lambda c: c.update(precondition="customer"), "aigen.yaml"),
        (lambda c: c.update(extra_field=1), "extra"),
        (lambda c: c["expected"][1].update(source="OpenAPI GET /me"), "source 须以"),
        (lambda c: c["expected"][0].update(source="契约 GET /no/such/path responses.200"), "契约里不存在"),
        (lambda c: c["expected"][0].update(source="契约 GET /me responses.418"), "没有声明 responses.418"),
        (lambda c: c["expected"][0].update(source="需求 REQ-99"), "里没有验收"),
        (lambda c: c["expected"][0].update(source="需求 第三条"), "验收编号"),
        (lambda c: c["expected"][0].update(source="推测：无材料依据"), "推测"),
        (lambda c: c.update(pending=["待确认"]), "valid dictionary"),
        (lambda c: c["request"]["json"].update(dob="${today:18}"), "参数不对"),
        (lambda c: c["request"]["json"].update(name="${len:0}"), "参数不对"),
        (lambda c: c.update(let={"unique": "x"}), "let 变量"),
        (lambda c: c.update(let={"a": "${b}"}), "不能引用其他变量"),
        (lambda c: c.update(cleanup=[{"method": "DELETE", "path": "/x"}], cleanup_as="root"), "aigen.yaml"),
        (lambda c: c["request"].update(form={"a": 1}), "json 不能和 form"),
        (lambda c: c["request"].update(json=None, files={"file": "data/no_such.txt"}), "上传文件"),
        (lambda c: c["expected"][1].update(op="matches", value="("), "正则写错了"),
        (lambda c: c["expected"][1].update(target="body", op="matches_contract", value="契约 GET /me responses.404"), "不存在或没有定义响应结构"),
        (lambda c: c.update(body_type="bytes"), "body_type=bytes 不支持"),
        (lambda c: c["expected"][1].update(target="header.X-Trace", op="len_gt", value=1), "响应头只支持"),
    ],
    ids=[
        "断言缺出处", "只断言status", "密码明文", "引用不存在的常量", "has_item值不是字典", "未配置的身份", "未定义字段",
        "出处不是登记材料", "出处编造契约接口", "出处编造状态码", "出处编造验收编号", "需求出处没写编号", "推测却没有pending",
        "pending不是结构化", "日期表达式错误", "长度表达式错误", "let变量占用生成函数名", "let引用变量", "清理身份未配置",
        "json和form同时出现", "上传文件不存在", "正则写错", "契约里没有这个响应结构", "响应类型不支持该断言", "响应头不支持的op",
    ],
)
def test_bad_case_rejected(mutate_fn, reason):
    with pytest.raises(ValidationError, match=reason):
        Suite.model_validate(bad(mutate_fn))


def test_guess_with_pending_passes_and_is_skipped():
    d = bad(lambda c: c.update(pending=[{"type": "契约缺失", "note": "未声明状态码"}]))
    d["cases"][0]["expected"][0]["source"] = "推测：无材料依据"
    assert "def test_" not in render_suite(Suite.model_validate(d))


def test_render_compiles_and_body_before_status():
    code = render_suite(Suite.model_validate(GOOD))
    compile(code, "generated.py", "exec")
    assert "api = client('member')" in code
    assert "json={'user': const('DEMO_USER')}" in code
    assert code.index("assert 'token' in body['data']") < code.index("assert resp.status_code == 200")


def test_render_headers_form_and_header_assert():
    d = bad(lambda c: c["request"].update(headers={"X-Tenant": "t1"}, form={"user": "${DEMO_USER}"}, json=None))
    d["cases"][0]["request"].pop("json")
    d["cases"][0]["expected"].append({"target": "header.Content-Type", "op": "contains", "value": "json", "source": SRC})
    code = render_suite(Suite.model_validate(d))
    compile(code, "generated.py", "exec")
    assert "headers={'X-Tenant': 't1'}" in code and "data={'user': const('DEMO_USER')}" in code
    assert "assert 'json' in resp.headers.get('Content-Type', '')" in code


def test_render_skips_pending_case():
    d = copy.deepcopy(GOOD)
    pending = copy.deepcopy(d["cases"][0])
    pending.update(case_id="TC-DEMO-02", pending=[{"type": "其他", "note": "待确认"}])
    d["cases"].append(pending)
    code = render_suite(Suite.model_validate(d))
    assert "test_TC_DEMO_01" in code and "test_TC_DEMO_02" not in code


def test_known_issue_renders_strict_xfail():
    code = render_suite(Suite.model_validate(bad(lambda c: c.update(known_issue="BUG-1"))))
    compile(code, "generated.py", "exec")
    assert "@pytest.mark.xfail(strict=True, reason='已知缺陷：BUG-1')" in code


def test_let_and_generators_render():
    d = bad(lambda c: c.update(let={"email": "aigen.${unique}@example.com"}))
    d["cases"][0]["request"]["json"] = {"email": "${email}", "dob": "${today:-18y+1d}", "note": "${len:251}"}
    code = render_suite(Suite.model_validate(d))
    compile(code, "generated.py", "exec")
    assert "email = remember('email', 'aigen.' + gen.unique() + '@example.com')" in code
    assert "'dob': gen.today('-18y+1d'), 'note': gen.text(251)" in code


@pytest.mark.parametrize(
    "offset, base, expected",
    [
        ("-18y+1d", date(2026, 10, 27), "2008-10-28"),
        ("-1y", date(2024, 2, 29), "2023-02-28"),
        ("+1m", date(2026, 1, 31), "2026-02-28"),
        ("-30d", date(2026, 3, 1), "2026-01-30"),
    ],
    ids=["差一天满18岁", "闰日减一年", "月末加一月", "跨月减天数"],
)
def test_gen_today(offset, base, expected):
    assert gen.today(offset, base) == expected


def test_gen_text_and_unique():
    assert len(gen.text(251)) == 251 and gen.text(10) == "0123456789"
    assert gen.unique() != gen.unique()


def test_on_fail_reports_status():
    class Resp:
        status_code, text = 500, "server error"

    with pytest.raises(AssertionError, match="KeyError.*status=500.*server error"):
        with on_fail(Resp()):
            {}["success"]


def test_const_prefers_environment(monkeypatch):
    assert const("DEMO_USER") == "member@demo.test"
    monkeypatch.setenv("DEMO_USER", "from-ci@demo.test")
    assert const("DEMO_USER") == "from-ci@demo.test"


def test_dotenv_between_environment_and_constants(monkeypatch):
    assert const("DEMO_FROM_DOTENV") == "dotenv-value"  # 引号去掉
    monkeypatch.setenv("DEMO_FROM_DOTENV", "from-system")
    assert const("DEMO_FROM_DOTENV") == "from-system"  # 系统环境变量优先
    assert runtime.env("AIGEN_LLM_API_KEY") is None  # 留空 = 没配


def test_draft_reports_missing_key(demo_project, monkeypatch, capsys):
    from aigen import draft

    for k in ("AIGEN_LLM_BASE_URL", "AIGEN_LLM_API_KEY", "AIGEN_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert draft.main(["drafted"]) == 1
    assert "AIGEN_LLM_API_KEY" in capsys.readouterr().out and not (demo_project / "cases" / "drafted.yaml").exists()


def test_client_profile_headers_and_nested_token(monkeypatch):
    class Fake:
        status_code, text = 200, ""

        def __init__(self, method):
            self.request = type("R", (), {"method": method})()

        def json(self):
            return {"data": {"token": "T-1"}}

    seen = []
    monkeypatch.setattr(requests.Session, "request", lambda self, m, url, **kw: seen.append((m, url, kw)) or Fake(m))
    api = Client("member")
    assert api.base == "http://demo.invalid/api"
    assert api.session.headers["X-Api-Key"] == "k-123"
    assert seen[0][1] == "http://demo.invalid/api/login" and seen[0][2]["json"] == {"user": "member@demo.test", "pwd": "Secret#1"}
    assert api.session.headers["Authorization"] == "Bearer T-1"


MULTI = case("TC-DEMO-03", "多步用例", "需求 REQ-02", {"steps": [
    {"request": {"method": "POST", "path": "/carts"},
     "expected": [{"target": "body.id", "op": "exists", "source": SRC},
                  {"target": "status", "op": "eq", "value": 201, "source": "契约 POST /carts responses.201"}],
     "extract": {"cart_id": "body.id"}},
    {"request": {"method": "GET", "path": "/carts/${cart_id}"},
     "expected": [{"target": "body.id", "op": "eq", "value": "${cart_id}", "source": SRC},
                  {"target": "status", "op": "eq", "value": 200, "source": "契约 GET /carts/{id} responses.200"}]},
]}, cleanup=[{"method": "DELETE", "path": "/carts/${cart_id}"}])


def multi(mutate_fn=lambda c: None):
    c = copy.deepcopy(MULTI)
    mutate_fn(c)
    return {"module": "demo", "cases": [c]}


def test_multi_step_passes_and_renders_cleanup_in_finally():
    code = render_suite(Suite.model_validate(multi()))
    compile(code, "generated.py", "exec")
    assert "cart_id = None" in code
    assert code.index("finally:") < code.index("'DELETE'")
    assert "f'/carts/{cart_id}'" in code


@pytest.mark.parametrize(
    "mutate_fn, reason",
    [
        (lambda c: c["steps"][0].pop("extract"), "前面没有定义的变量"),
        (lambda c: c["cleanup"][0].update(path="/carts/${other_id}"), "cleanup"),
        (lambda c: c["steps"][0]["extract"].update(bad="data.id"), "body.data.0.id"),
        (lambda c: c.update(request={"method": "GET", "path": "/x"}), "二选一"),
        (lambda c: c["steps"][0]["extract"].update(x={"from": "body", "where": {"name": "${nope}"}, "take": "id"}), "前面没有定义的变量"),
    ],
    ids=["引用未提取的变量", "清理引用未提取的变量", "提取路径格式错误", "request与steps同时出现", "条件提取引用未定义变量"],
)
def test_bad_multi_step_rejected(mutate_fn, reason):
    with pytest.raises(ValidationError, match=reason):
        Suite.model_validate(multi(mutate_fn))


def test_pick_extract_finds_by_condition():
    d = multi(lambda c: c["steps"][0].update(extract={"cart_id": {"from": "body.items", "where": {"slug": "pliers"}, "take": "meta.id"}}))
    code = render_suite(Suite.model_validate(d))
    lines = code[code.index("found = "):].splitlines()[:3]
    env = {"body": {"items": [{"slug": "hammer", "meta": {"id": 1}}, {"slug": "pliers", "meta": {"id": 2}}]}}
    exec("\n".join(x.strip() for x in lines), env)
    assert env["cart_id"] == 2
    env["body"]["items"].pop()
    with pytest.raises(AssertionError, match="没有 slug='pliers'"):
        exec("\n".join(x.strip() for x in lines), env)


def test_cleanup_as_uses_separate_session():
    c = copy.deepcopy(MULTI)
    c["cleanup_as"] = "admin"
    code = render_suite(Suite.model_validate({"module": "demo", "cases": [c]}))
    compile(code, "generated.py", "exec")
    assert "cleaner = client('admin')" in code and "cleaner.request('DELETE'" in code


def list_case(op, value):
    return {"module": "demo", "cases": [case("TC-DEMO-04", "列表断言", "需求 REQ-02", {
        "request": {"method": "GET", "path": "/items"},
        "expected": [{"target": "body.data", "op": op, "value": value, "source": SRC},
                     {"target": "status", "op": "eq", "value": 200, "source": "契约 GET /items responses.200"}]})]}


@pytest.mark.parametrize(
    "op, value, data, ok",
    [
        ("every", {"brand.id": "b1"}, [{"brand": {"id": "b1"}}, {"brand": {"id": "b1"}}], True),
        ("every", {"brand.id": "b1"}, [{"brand": {"id": "b1"}}, {"brand": {"id": "b2"}}], False),
        ("every", {"brand.id": "b1"}, [], False),
        ("every_between", {"price": [10, 30]}, [{"price": 10}, {"price": 30}], True),
        ("every_between", {"price": [10, 30]}, [{"price": 9.99}], False),
        ("sorted", {"by": "price", "order": "asc"}, [{"price": 1}, {"price": 2}], True),
        ("sorted", {"by": "name", "order": "desc"}, [{"name": "A"}, {"name": "B"}], False),
    ],
    ids=["every全部匹配", "every有一项不匹配", "every空列表不能假绿", "区间含边界", "区间外", "升序", "降序不成立"],
)
def test_list_assertions_behave(op, value, data, ok):
    code = render_suite(Suite.model_validate(list_case(op, value)))
    func = code[code.index("def "):]
    assert_lines = [x.strip() for x in func.splitlines() if x.strip().startswith("assert") and "status_code" not in x]
    assert mutate.passes(assert_lines, {"data": data}) is ok


UI = {"kind": "ui", "module": "demo_ui", "cases": [case("TC-DEMO-UI-01", "UI 用例", "需求 REQ-01", {
    "steps": [{"call": "LoginPage.sign_in"}, {"call": "LoginPage.login", "args": ["${DEMO_USER}", "${DEMO_PWD}"]}],
    "expected": [{"target": "AccountPage.title", "op": "contains_text", "value": "我的账户", "source": SRC},
                 {"target": "url", "op": "url_matches", "value": ".*/account$", "source": SRC}]})]}


def ui(mutate_fn=lambda c: None):
    d = copy.deepcopy(UI)
    mutate_fn(d["cases"][0])
    return d


def test_ui_case_passes_and_renders():
    code = render_suite(UiSuite.model_validate(UI))
    compile(code, "generated.py", "exec")
    assert "def test_TC_DEMO_UI_01(page: Page):" in code
    assert "from pages.login_page import AccountPage" in code
    assert "login_page.login(const('DEMO_USER'), const('DEMO_PWD'))" in code
    assert "expect(account_page.title()).to_contain_text('我的账户', timeout=ui_timeout())" in code


@pytest.mark.parametrize(
    "mutate_fn, reason",
    [
        (lambda c: c["steps"][0].update(call="LoginPage.no_such_method"), "没有方法"),
        (lambda c: c["steps"][0].update(call="NoSuchPage.open"), "没有页面对象"),
        (lambda c: c["steps"][1].update(args=["${DEMO_USER}"]), "个参数"),
        (lambda c: c["expected"][0].update(target="LoginPage.login", args=["a", "b"]), "没有 return"),
        (lambda c: c["steps"][1].update(args=["${DEMO_USER}", "Secret#1"]), "明文"),
        (lambda c: c["steps"][0].update(call="#email"), "call"),
        (lambda c: c["expected"][0].update(source="凭感觉"), "source 须以"),
    ],
    ids=["调用不存在的方法", "不存在的页面对象", "参数个数不对", "动作方法当断言目标", "密码明文", "直接写定位器", "出处不是登记材料"],
)
def test_bad_ui_case_rejected(mutate_fn, reason):
    with pytest.raises(ValidationError, match=reason):
        UiSuite.model_validate(ui(mutate_fn))


def test_trace_detects_drift_orphan_and_uncovered_requirement(tmp_path):
    cases, base = tmp_path / "cases", tmp_path
    gen_dir = base / "api_gen"
    cases.mkdir(), gen_dir.mkdir()
    (cases / "demo.yaml").write_text(yaml.safe_dump(GOOD, allow_unicode=True), encoding="utf-8")
    out = gen_dir / "test_demo.py"
    out.write_text(render_suite(Suite.model_validate(GOOD)), encoding="utf-8")
    assert trace(cases, base, coverage=False) == 0
    assert trace(cases, base, coverage=True) == 1  # 只覆盖了 REQ-01，REQ-02 没有用例

    out.write_text(out.read_text(encoding="utf-8") + "\n# 手改\n", encoding="utf-8")
    assert trace(cases, base, coverage=False) == 1

    out.write_text(render_suite(Suite.model_validate(GOOD)), encoding="utf-8")
    (gen_dir / "test_extra.py").write_text("def test_TC_DEMO_99():\n    pass\n", encoding="utf-8")
    assert trace(cases, base, coverage=False) == 1


def test_mutate_finds_weak_assertion_only(demo_project, capsys):
    rec = demo_project / "record.jsonl"
    rec.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in MUT_RECORDS), encoding="utf-8")
    assert mutate.main(str(rec)) == 1
    out = capsys.readouterr().out
    assert "[WEAK] TC-MUT-02" in out and "TC-MUT-01 的 201 响应" in out
    assert "[WEAK] TC-MUT-03" not in out and "[WEAK] TC-MUT-01" not in out and "[WEAK] TC-MUT-04" not in out
    # TC-MUT-04 步骤2 引用了 ${cart_id}、${email}，靠重放检查到了，不算跳过
    assert "检查 4 个步骤" in out and "跳过直接写动态数据的断言 0 条" in out


def test_export_writes_case_table_and_coverage(demo_project):
    assert export.main() == 0
    out = demo_project / profile().export_dir
    with open(out / "用例表.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert [r["用例编号"] for r in rows] == ["TC-MUT-01", "TC-MUT-02", "TC-MUT-03", "TC-MUT-04"]
    assert rows[0]["预期结果"] == "body.id 存在；状态码 = 201"
    cov = (out / "验收覆盖.md").read_text(encoding="utf-8")
    assert "| REQ-01 | 登录后能看到本人信息（测试账号 member@demo.test） | **未覆盖** |" in cov
    assert "TC-MUT-01, TC-MUT-02, TC-MUT-03, TC-MUT-04" in cov


def test_every_op_has_export_wording():
    from typing import get_args

    from aigen.ir import Expect
    from aigen.ui import UiExpect

    ops = set(get_args(Expect.model_fields["op"].annotation)) | set(get_args(UiExpect.model_fields["op"].annotation))
    assert ops <= set(export.OPS), f"导出缺少这些断言的中文说法：{ops - set(export.OPS)}"


def test_file_upload_renders_and_reads_file():
    d = bad(lambda c: c["request"].update(json=None, form={"note": "x"}, files={"file": "data/a.txt"}))
    code = render_suite(Suite.model_validate(d))
    compile(code, "generated.py", "exec")
    assert "data={'note': 'x'}, files={'file': upload('data/a.txt')}" in code
    assert runtime.upload("data/a.txt") == ("a.txt", b"hello")


@pytest.mark.parametrize(
    "body_type, expected, body, ok",
    [
        ("text", [{"target": "body", "op": "matches", "value": r"^OK \d+$", "source": SRC}], "OK 42", True),
        ("text", [{"target": "body", "op": "contains", "value": "OK", "source": SRC}], "FAIL", False),
        ("bytes", [{"target": "body", "op": "sha256", "value": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824", "source": SRC}], b"hello", True),
        ("bytes", [{"target": "body", "op": "len_gt", "value": 10, "source": SRC}], b"hello", False),
        ("xml", [{"target": "body.user.name", "op": "eq", "value": "Jane", "source": SRC}], "<r><user><name>Jane</name></user></r>", True),
        ("xml", [{"target": "body.user.age", "op": "exists", "source": SRC}], "<r><user><name>Jane</name></user></r>", False),
    ],
    ids=["文本正则", "文本包含不成立", "文件摘要", "文件太小", "XML取值", "XML缺元素"],
)
def test_non_json_bodies(body_type, expected, body, ok):
    import xml.etree.ElementTree as ET

    d = bad(lambda c: c.update(body_type=body_type, expected=expected + [{"target": "status", "op": "eq", "value": 200, "source": SRC}]))
    code = render_suite(Suite.model_validate(d))
    compile(code, "generated.py", "exec")
    parse = {"text": "resp.text", "bytes": "resp.content", "xml": "xml_body(resp)"}[body_type]
    assert f"body = {parse}" in code
    lines = [x.strip() for x in code.splitlines() if x.strip().startswith("assert") and "status_code" not in x]
    assert mutate.passes(lines, ET.fromstring(body) if body_type == "xml" else body) is ok


def test_matches_contract_with_ref_and_nullable():
    d = bad(lambda c: c["expected"].__setitem__(1, {"target": "body", "op": "matches_contract", "value": "契约 GET /me responses.200", "source": SRC}))
    code = render_suite(Suite.model_validate(d))
    assert "assert_contract(body, '契约', 'GET', '/me', '200')" in code
    runtime.assert_contract({"id": "u1", "age": None}, "契约", "GET", "/me", "200")
    with pytest.raises(AssertionError, match="'id' is a required property"):
        runtime.assert_contract({"age": 3}, "契约", "GET", "/me", "200")
    with pytest.raises(AssertionError, match="id：1 is not of type 'string'"):
        runtime.assert_contract({"id": 1}, "契约", "GET", "/me", "200")


def test_page_object_inherits_parent_methods():
    d = ui(lambda c: c["steps"].insert(0, {"call": "LoginPage.open", "args": ["/login"]}))
    assert "login_page.open('/login')" in render_suite(UiSuite.model_validate(d))


DRAFT_OK = """```yaml
module: drafted
cases:
  - case_id: TC-DRAFT-01
    title: 登录后看本人信息
    req_ref: "需求 REQ-01"
    design_method: equivalence
    precondition: member
    request: {method: GET, path: /me}
    expected:
      - {target: body.id, op: exists, source: "契约 GET /me responses.200"}
      - {target: status, op: eq, value: 200, source: "契约 GET /me responses.200"}
```"""
DRAFT_BAD = DRAFT_OK.replace(', source: "契约 GET /me responses.200"}\n      - {target: status', "}\n      - {target: status")


def fake_llm(monkeypatch, replies: list):
    """假模型：依次返回 replies（字符串 = 回复内容，整数 = HTTP 状态码），记录每次请求。"""
    sent = []

    class Resp:
        def __init__(self, item):
            self.status_code = item if isinstance(item, int) else 200
            self.text = "err"
            self.item = item

        def json(self):
            return {"choices": [{"message": {"content": self.item}}]}

    def post(url, json=None, **kw):
        sent.append((url, json, kw["headers"]))
        return Resp(replies[len(sent) - 1])

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr("time.sleep", lambda s: None)
    for k, v in {"AIGEN_LLM_BASE_URL": "http://llm.invalid/v1", "AIGEN_LLM_API_KEY": "sk-test", "AIGEN_LLM_MODEL": "m"}.items():
        monkeypatch.setenv(k, v)
    return sent


def test_draft_fix_loop_redaction_and_output(demo_project, monkeypatch):
    from aigen import draft

    sent = fake_llm(monkeypatch, [DRAFT_BAD, DRAFT_OK])
    out = demo_project / "cases" / "drafted.yaml"
    try:
        assert draft.main(["drafted", "--ac", "REQ-01"]) == 0
        assert out.exists() and "TC-DRAFT-01" in out.read_text(encoding="utf-8")
        assert len(sent) == 2 and sent[0][0] == "http://llm.invalid/v1/chat/completions"
        payload = json.dumps(sent[0][1], ensure_ascii=False)
        assert "member@demo.test" not in payload and "测试账号 ${DEMO_USER}" in payload  # PRD 里的账号被脱敏
        assert "购物车能创建和查询" not in sent[0][1]["messages"][1]["content"]  # --ac 只发选中的验收（REQ-02 那行没发）
        assert "source" in sent[1][1]["messages"][-1]["content"]  # 第二轮回灌了 validate 错误
    finally:
        out.unlink(missing_ok=True)


def test_draft_gives_up_after_three_rounds(demo_project, monkeypatch):
    from aigen import draft

    sent = fake_llm(monkeypatch, [DRAFT_BAD] * 3)
    assert draft.main(["drafted"]) == 1
    assert len(sent) == 3 and not (demo_project / "cases" / "drafted.yaml").exists()
    assert (demo_project / "reports" / "aigen_draft_failed_drafted.yaml").exists()


def test_draft_does_not_retry_on_401_but_retries_429(demo_project, monkeypatch):
    from aigen import draft

    sent = fake_llm(monkeypatch, [401])
    assert draft.main(["drafted"]) == 1 and len(sent) == 1
    sent = fake_llm(monkeypatch, [429, 500, DRAFT_OK])
    try:
        assert draft.main(["drafted"]) == 0 and len(sent) == 3
    finally:
        (demo_project / "cases" / "drafted.yaml").unlink(missing_ok=True)


def test_redact_replaces_constant_values():
    from aigen.draft import redact

    assert redact("账号 member@demo.test 密码 Secret#1 地址 http://demo.invalid/api/") == "账号 ${DEMO_USER} 密码 ${DEMO_PWD} 地址 ${DEMO_API}"


def test_oauth2_client_credentials_and_refresh(monkeypatch):
    from aigen.runtime import OAuth2

    p = profile()
    patched = p.model_copy(update={"api": p.api.model_copy(update={"login": None, "oauth2": OAuth2(token_url="/oauth/token", grant="client_credentials")})})
    monkeypatch.setattr(runtime, "profile", lambda: patched)
    calls = []

    class Fake:
        status_code, text = 200, ""

        def __init__(self, method, url, data):
            self.request = type("R", (), {"method": method})()
            self.url, self.data = url, data

        def json(self):
            return {"access_token": f"T{len(calls)}", "token_type": "bearer", "expires_in": 1}

    monkeypatch.setattr(requests.Session, "request", lambda self, m, url, **kw: calls.append((url, kw.get("data"))) or Fake(m, url, kw.get("data")))
    api = Client("member")
    assert calls[0] == ("http://demo.invalid/api/oauth/token", {"grant_type": "client_credentials", "client_id": "member@demo.test", "client_secret": "Secret#1"})
    assert api.session.headers["Authorization"] == "Bearer T1"
    api.request("GET", "/me")  # expires_in=1，已在 30 秒刷新窗口内 → 先重新取 token
    assert [u for u, _ in calls].count("http://demo.invalid/api/oauth/token") == 2


def test_shipped_template_and_examples_parse():
    here = runtime.Path(runtime.__file__).parent
    runtime.Profile.model_validate(yaml.safe_load((here / "aigen.yaml.example").read_text(encoding="utf-8")))
    for p in (here / "examples").glob("*.yaml"):
        assert yaml.safe_load(p.read_text(encoding="utf-8"))["cases"], p.name
