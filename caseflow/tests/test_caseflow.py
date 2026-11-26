"""caseflow 框架离线自检：不依赖外网，覆盖配置、运行时、数据驱动、UI DOM/诊断、变异检查、导出与脚手架。"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

from caseflow import config, data, export, guard, mutate, plugin, runtime, scaffold, skill, ui
from caseflow.config import ConfigError, env, setting
from caseflow.runtime import Client, Resp, client, diff_response, dig, fill, gen, on_fail, to_curl


# ---------------------------------------------------------------- 本地 Mock 服务

class _MockState:
    def __init__(self):
        self.login_calls = 0
        self.echo_calls = 0
        self.items: dict[str, dict] = {"1": {"id": "1", "name": "初始商品", "price": 10}}
        self.seq = 1


def _start_mock_server():
    state = _MockState()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(n).decode("utf-8") if n else "{}"
            return json.loads(raw or "{}")

        def _send(self, status: int, body, ctype: str = "application/json; charset=utf-8"):
            raw = (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            payload = self._read_json()
            if self.path == "/api/login":
                state.login_calls += 1
                if payload.get("username") == "alice" and payload.get("password") == "Secret#123":
                    return self._send(200, {"code": 0, "msg": "ok", "data": {"token": "tok-alice"}})
                return self._send(200, {"code": 10001, "msg": "账号或密码错误", "data": None})
            if self.path == "/api/items":
                if self.headers.get("Authorization") != "Bearer tok-alice":
                    return self._send(401, {"code": 401, "msg": "未登录", "data": None})
                name = payload.get("name", "")
                if not name:
                    return self._send(200, {"code": 10002, "msg": "名称不能为空", "data": None})
                state.seq += 1
                iid = str(state.seq)
                item = {"id": iid, "name": name, "price": payload.get("price", 0),
                        "sign": self.headers.get("X-Sign", "")}
                state.items[iid] = item
                return self._send(200, {"code": 0, "msg": "ok", "data": item})
            return self._send(404, {"code": 404, "msg": "not found"})

        def do_GET(self):
            if self.path == "/api/echo":
                state.echo_calls += 1
                return self._send(200, {
                    "code": 0, "msg": "ok",
                    "data": {
                        "1001": {"name": "数字键"},
                        "items": [{"id": 10}, {"id": 20}],
                        "tags": ["a", "b", "c"],
                        "flag": True,
                        "count": 5,
                        "note": "hello-world-2026",
                        "empty": None,
                    },
                })
            if self.path == "/api/fail-endpoint":
                return self._send(500, {"code": 500, "msg": "boom"})
            if self.path == "/ui-fail":
                html = """<!doctype html><html><head><meta charset="utf-8"><title>诊断页</title></head>
                <body>
                  <button data-testid="boom-btn" onclick="for (let i = 0; i < 5; i++) console.error('ui-boom'); fetch('/api/fail-endpoint');">点我报错</button>
                  <div id="status">ok</div>
                </body></html>"""
                return self._send(200, html, "text/html; charset=utf-8")
            if self.path.startswith("/api/items/"):
                iid = self.path.rsplit("/", 1)[-1]
                if iid in state.items:
                    return self._send(200, {"code": 0, "msg": "ok", "data": state.items[iid]})
                return self._send(200, {"code": 10404, "msg": "不存在", "data": None})
            return self._send(404, {"code": 404, "msg": "not found"})

        def do_DELETE(self):
            if self.path.startswith("/api/items/"):
                iid = self.path.rsplit("/", 1)[-1]
                state.items.pop(iid, None)
                return self._send(200, {"code": 0, "msg": "deleted", "data": None})
            return self._send(404, {"code": 404, "msg": "not found"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, state, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture(scope="module")
def mock_srv():
    srv, state, url = _start_mock_server()
    yield state, url
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def demo_proj(tmp_path, monkeypatch, mock_srv):
    state, url = mock_srv
    state.login_calls = 0
    (tmp_path / "caseflow.yaml").write_text(
        f"""
api:
  base_url: ${{API_BASE_URL}}
  envelope: {{code: code, ok: 0, msg: msg, data: data}}
  login:
    method: POST
    path: /api/login
    user_field: username
    password_field: password
    token_path: data.token
  roles:
    user: {{user: "${{USER_NAME}}", password: "${{USER_PASSWORD}}"}}
ui:
  base_url: {url}
  auth_dir: .auth
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f'API_BASE_URL={url}\nUSER_NAME=alice\nUSER_PASSWORD="Secret#123"\nFROM_DOTENV=dot\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CASEFLOW_PROJECT", str(tmp_path))
    monkeypatch.delenv("API_BASE_URL", raising=False)
    monkeypatch.delenv("FROM_DOTENV", raising=False)
    config.reset()
    runtime.reset_clients()
    yield tmp_path, state, url
    config.reset()
    runtime.reset_clients()


# ---------------------------------------------------------------- config

def test_config_env_priority_and_errors(demo_proj, monkeypatch):
    root, _, _ = demo_proj
    assert env("FROM_DOTENV") == "dot"
    monkeypatch.setenv("FROM_DOTENV", "sys")
    assert env("FROM_DOTENV") == "sys"
    assert setting("api", "timeout") == 15  # 默认值合并

    (root / "caseflow.yaml").write_text("api: {}\nbad_key: 1\n", encoding="utf-8")
    config.reset()
    with pytest.raises(ConfigError, match="bad_key"):
        config.config()

    (root / "caseflow.yaml").write_text("api:\n  base_url: ${NO_SUCH_VAR}\n", encoding="utf-8")
    config.reset()
    with pytest.raises(ConfigError, match="NO_SUCH_VAR"):
        setting("api", "base_url")


# ---------------------------------------------------------------- runtime

def test_client_caches_login_per_role_and_supports_fresh(demo_proj):
    _, state, _ = demo_proj
    c1 = client("user")
    c2 = client("user")
    assert c1 is c2
    assert state.login_calls == 1
    c3 = client("user", fresh=True)
    assert c3 is not c1
    assert state.login_calls == 2


def test_envelope_and_all_expect_operators(demo_proj):
    api = client(None)
    r = api.get("/api/echo").expect_ok().expect_code(0)
    assert r.code == 0 and r.msg == "ok" and isinstance(r.data, dict)
    assert dig(r.body, "data.1001.name") == "数字键"
    assert dig(r.body, "data.items.0.id") == 10

    r.expect("data.1001.name", eq="数字键", ne="其他", contains="字", not_contains="abc", type="str")
    r.expect("data.count", gt=4, ge=5, lt=6, le=5, type="int")
    r.expect("data.flag", eq=True, type="bool")
    r.expect("data.tags", len=3, len_gt=2, len_ge=3, contains="b", not_contains="z", type="list")
    r.expect("data.note", regex=r"^hello-\w+-2026$")
    r.expect("data.empty", type="null", exists=True)
    r.expect("data.not_here", exists=False)
    r.expect("code", **{"in": [0, 1], "not_in": [2, 3]})

    with pytest.raises(AssertionError, match=r"(?s)\[请求\].*\[响应\].*\[复现\] curl"):
        r.expect("data.count", eq=999)


def test_before_request_hook_and_on_fail(demo_proj, monkeypatch):
    root, _, _ = demo_proj
    (root / "my_hooks.py").write_text(
        "def sign(method, url, kw):\n"
        "    h = dict(kw.get('headers') or {})\n"
        "    h['X-Sign'] = f'{method}-signed'\n"
        "    kw['headers'] = h\n"
        "    return kw\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(root))
    raw_cfg = (root / "caseflow.yaml").read_text(encoding="utf-8")
    (root / "caseflow.yaml").write_text(raw_cfg.replace("api:\n", "api:\n  hook: my_hooks:sign\n"), encoding="utf-8")
    config.reset()
    runtime.reset_clients()

    api = client("user")
    r = api.post("/api/items", json={"name": "hooked", "price": 5}).expect_ok()
    r.expect("data.sign", eq="POST-signed")

    with pytest.raises(AssertionError, match=r"\[复现\] curl"):
        with on_fail(r):
            assert r.data["name"] == "wrong"


def test_prod_host_safety_lock_and_curl_redaction(demo_proj, monkeypatch):
    called = []
    monkeypatch.setattr(
        requests.Session, "request",
        lambda self, m, u, **kw: called.append((m, u)) or type("R", (), {
            "status_code": 200, "text": "{}", "headers": {}, "request": None, "json": lambda self: {},
        })(),
    )
    api = Client(None, base_url="https://api.prod.example.com")
    with pytest.raises(ConfigError, match="拒绝向疑似生产环境"):
        api.post("/orders", json={"x": 1})
    api.get("/orders")
    assert called == [("GET", "https://api.prod.example.com/orders")]

    monkeypatch.setenv("CASEFLOW_ALLOW_PROD", "1")
    api.post("/orders", json={"x": 1})
    assert len(called) == 2

    req = requests.Request(
        "POST", "http://demo.test/login",
        headers={"Authorization": "Bearer secret-tok", "X-Other": "1"},
        json={"username": "alice", "password": "Secret#123"},
    ).prepare()
    masked = to_curl(req)
    assert "secret-tok" not in masked and "Secret#123" not in masked and "<REDACTED>" in masked
    monkeypatch.setenv("CASEFLOW_CURL_RAW", "1")
    raw_curl = to_curl(req)
    assert "secret-tok" in raw_curl and "Secret#123" in raw_curl


def test_generators_and_fill_types(demo_proj):
    assert re.fullmatch(r"[0-9a-f]{10}", gen.unique(10))
    assert re.fullmatch(r"[0-9a-f-]{36}", gen.uuid())
    assert re.fullmatch(r"1[3-9]\d{9}", gen.phone())
    assert isinstance(gen.timestamp("ms"), int) and gen.timestamp("ms") > gen.timestamp("s") * 100
    assert gen.today("-1y", date(2024, 2, 29)) == "2023-02-28"
    assert gen.today("+1m", date(2026, 1, 31)) == "2026-02-28"
    assert len(gen.now()) == 19
    assert len(gen.text(25)) == 25 and gen.text(10) == "0123456789"
    assert re.fullmatch(r"[a-z]{12}", gen.alpha(12))
    assert re.fullmatch(r"\d{6}", gen.digits(6))

    filled = fill({"ts": "${timestamp:s}", "u": "user_${v}", "n": "${len:5}"}, {"v": "abc"})
    assert isinstance(filled["ts"], int)
    assert filled["u"] == "user_abc" and filled["n"] == "01234"


# ---------------------------------------------------------------- data

def test_data_driven_run_and_validation_rules(demo_proj):
    root, _, _ = demo_proj
    (root / "data").mkdir()
    yml = root / "data" / "items.yaml"
    yml.write_text(
        """
api: POST /api/items
role: user
cases:
  - id: TC-01
    title: 正常创建商品
    vars: {name: "item_${unique}"}
    json: {name: "${name}", price: 12}
    expect:
      ok: true
      body:
        data.name: "${name}"
        data.id: {exists: true}
  - id: TC-02
    title: 名称为空报错
    json: {name: "", price: 12}
    expect:
      code: 10002
      body: {msg: {contains: 名称}}
  - id: TC-03
    title: 待校准用例
    json: {name: "x", price: -1}
    expect: {ok: true}
    xfail: 待实测
  - id: TC-04
    title: 跳过用例
    json: {name: "y"}
    expect: {ok: true}
    skip: 暂不执行
""",
        encoding="utf-8",
    )
    params = data.load_cases("data/items.yaml")
    assert len(params) == 4
    assert params[0].id == "TC-01 正常创建商品"
    assert any(m.name == "xfail" for m in params[2].marks)
    assert any(m.name == "skip" for m in params[3].marks)

    r1 = data.run_case(params[0].values[0])
    assert r1.data["name"].startswith("item_")
    data.run_case(params[1].values[0])

    # 假绿拦截：只写 status 且没填 no_body_reason
    bad_fake = root / "data" / "bad_fake.yaml"
    bad_fake.write_text("api: GET /api/echo\ncases:\n  - id: B1\n    title: 假绿\n    expect: {status: 200}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="假绿风险"):
        data.read_cases(bad_fake)

    # no_body_reason 可放行纯 status
    ok_nobody = root / "data" / "ok_nobody.yaml"
    ok_nobody.write_text(
        "api: GET /api/echo\ncases:\n  - id: B2\n    title: 允许\n    no_body_reason: 无响应体\n    expect: {status: 200}\n",
        encoding="utf-8",
    )
    assert len(data.read_cases(ok_nobody)) == 1

    # 明文子串密码拦截
    bad_secret = root / "data" / "bad_secret.yaml"
    bad_secret.write_text(
        "api: POST /api/items\ncases:\n  - id: S1\n    title: 泄密\n"
        '    headers: {Authorization: "Bearer Secret#123"}\n    expect: {ok: true}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="明文"):
        data.read_cases(bad_secret)

    # 重复 id 与未知字段拦截
    bad_dup = root / "data" / "bad_dup.yaml"
    bad_dup.write_text(
        "api: GET /api/echo\ncases:\n  - id: D1\n    title: a\n    expect: {ok: true}\n"
        "  - id: D1\n    title: b\n    oops: 1\n    expect: {ok: true}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="oops|重复"):
        data.read_cases(bad_dup)

    # base_json + drop + xfail_strict
    base_yml = root / "data" / "base_drop.yaml"
    base_yml.write_text(
        """
api: POST /api/items
role: user
base_json:
  name: "base_${unique}"
  price: 20
cases:
  - id: TC-BASE-01
    title: 覆盖字段
    json: {price: 30}
    expect: {ok: true, body: {data.price: 30}}
  - id: TC-BASE-02
    title: drop删除字段
    drop: [name]
    expect: {code: 10002, body: {msg: {contains: 名称}}}
  - id: TC-BASE-03
    title: 严格已知缺陷
    xfail_strict: 缺陷单-123
    expect: {ok: true}
""",
        encoding="utf-8",
    )
    b_params = data.load_cases(base_yml)
    assert b_params[0].values[0].json["price"] == 30 and "name" in b_params[0].values[0].json
    assert "name" not in b_params[1].values[0].json and b_params[1].values[0].json["price"] == 20
    data.run_case(b_params[0].values[0])
    data.run_case(b_params[1].values[0])
    xf_mark = next(m for m in b_params[2].marks if m.name == "xfail")
    assert xf_mark.kwargs["strict"] is True

    # base_json 明文密码拦截 & xfail/xfail_strict 互斥
    bad_base = root / "data" / "bad_base.yaml"
    bad_base.write_text(
        'api: POST /api/items\nbase_json: {pwd: "Secret#123"}\ncases:\n  - id: BS1\n    title: a\n    expect: {ok: true}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="明文"):
        data.read_cases(bad_base)

    bad_xf = root / "data" / "bad_xf.yaml"
    bad_xf.write_text(
        "api: GET /api/echo\ncases:\n  - id: XF1\n    title: 互斥\n    xfail: a\n    xfail_strict: b\n    expect: {ok: true}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="二选一"):
        data.read_cases(bad_xf)


# ---------------------------------------------------------------- UI & plugin

def test_extract_dom_filters_hidden_and_dynamic_ids(tmp_path):
    html = tmp_path / "sample.html"
    html.write_text(
        """<!doctype html><html><head><title>下单页</title></head><body>
        <h1>确认订单</h1>
        <input id="username" placeholder="收货人" />
        <button id="btn-987654" data-testid="submit-order">提交订单</button>
        <button style="display:none">隐藏按钮</button>
        </body></html>""",
        encoding="utf-8",
    )
    out = ui.extract_dom(html_file=str(html))
    assert "确认订单" in out
    assert 'data-testid="submit-order"' in out
    assert "btn-987654" not in out
    assert "隐藏按钮" not in out
    assert "无障碍树" in out


def _browser_args() -> list[str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            p.chromium.launch(headless=True).close()
            return []
        except Exception as e:
            if "Executable doesn't exist" not in str(e):
                raise
        for ch in ("chrome", "msedge"):
            try:
                p.chromium.launch(headless=True, channel=ch).close()
                return ["--browser-channel", ch]
            except Exception:
                continue
    return []


def test_ui_plugin_saves_failure_diagnostics(demo_proj, mock_srv):
    root, _, url = demo_proj
    _, _ = mock_srv
    (root / "conftest.py").write_text('pytest_plugins = ["caseflow.plugin"]\n', encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_ui_diag.py").write_text(
        f"""from playwright.sync_api import expect

def test_fail_diag(page):
    page.goto({url!r} + "/ui-fail")
    page.get_by_test_id("boom-btn").click()
    page.wait_for_timeout(150)
    expect(page.locator("#status")).to_have_text("never-match", timeout=300)
""",
        encoding="utf-8",
    )
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_ui_diag.py", "-q", "-p", "no:cacheprovider",
         "--rootdir", str(root), *_browser_args()],
        cwd=root, env={**os.environ, "CASEFLOW_PROJECT": str(root)}, capture_output=True, text=True,
    )
    assert res.returncode != 0
    diag_dir = root / "reports" / "ui"
    mds = list(diag_dir.glob("*.md"))
    pngs = list(diag_dir.glob("*.png"))
    assert len(mds) == 1 and len(pngs) == 1
    content = mds[0].read_text(encoding="utf-8")
    assert "500 GET" in content and "/api/fail-endpoint" in content
    assert "(×5) [error] ui-boom" in content and content.count("ui-boom") == 1


# ---------------------------------------------------------------- mutate

def test_mutate_detects_weak_and_passes_strong(demo_proj, capsys):
    root, _, _ = demo_proj
    (root / "data").mkdir(exist_ok=True)
    (root / "tests" / "api").mkdir(parents=True, exist_ok=True)
    (root / "data" / "mut_cases.yaml").write_text(
        """
api: POST /api/items
role: user
cases:
  - id: TC-STRONG-01
    title: 强断言
    json: {name: "strong_${unique}", price: 10}
    expect:
      ok: true
      status: 200
      body: {data.id: {exists: true}}
  - id: TC-WEAK-01
    title: 假绿只看status
    no_body_reason: 故意写弱用于自检
    json: {name: "weak_${unique}", price: 10}
    expect: {status: 200}
""",
        encoding="utf-8",
    )
    (root / "tests" / "api" / "test_strong.py").write_text(
        """from caseflow import client

def test_strong_flow():
    \"\"\"TC-STRONG-02 强断言流\"\"\"
    api = client("user")
    api.post("/api/items", json={"name": "s2", "price": 1}).expect_ok().expect("data.id", exists=True)
""",
        encoding="utf-8",
    )
    (root / "tests" / "api" / "test_mixed.py").write_text(
        """import pytest
from caseflow import load_cases, run_case

@pytest.mark.parametrize("case", load_cases("data/mut_cases.yaml"))
def test_mut(case):
    run_case(case)
""",
        encoding="utf-8",
    )
    assert mutate.main(["tests/api/test_strong.py"]) == 0
    capsys.readouterr()
    assert mutate.main(["tests/api"]) == 1
    out = capsys.readouterr().out
    assert "[WEAK]" in out and "TC-WEAK-01" in out
    assert "TC-STRONG-01" not in out and "TC-STRONG-02" not in out

    # 全 xfail 文件应返回 0 并提示 [SKIP]
    (root / "tests" / "api" / "test_only_xfail.py").write_text(
        "import pytest\n\n@pytest.mark.xfail(strict=True, reason='known')\ndef test_kf():\n    assert False\n",
        encoding="utf-8",
    )
    capsys.readouterr()
    assert mutate.main(["tests/api/test_only_xfail.py"]) == 0
    assert "[SKIP]" in capsys.readouterr().out


# ---------------------------------------------------------------- scaffold & export

def test_scaffold_init_and_export(tmp_path, monkeypatch):
    assert scaffold.init(str(tmp_path)) == 0
    for expected in ("caseflow.yaml", ".env.example", ".gitignore", "conftest.py", "pytest.ini", "PROJECT_MAP.md"):
        assert (tmp_path / expected).is_file()
    pm = (tmp_path / "PROJECT_MAP.md").read_text(encoding="utf-8")
    assert "业务模块划分" in pm and "模块上下游依赖矩阵" in pm

    # 改写一个已有文件，确认再次 init 不会覆盖
    (tmp_path / "PROJECT_MAP.md").write_text("# custom\n", encoding="utf-8")
    assert scaffold.init(str(tmp_path)) == 0
    assert (tmp_path / "PROJECT_MAP.md").read_text(encoding="utf-8") == "# custom\n"

    # 同名测试文件放到不同子目录，全量收集不应报 import file mismatch
    (tmp_path / "tests" / "api" / "test_same.py").write_text(
        'def test_api_same():\n    """TC-API-01 连通性"""\n    assert True\n', encoding="utf-8"
    )
    (tmp_path / "tests" / "ui" / "test_same.py").write_text(
        'def test_ui_same(page):\n    """TC-UI-01 首页展示"""\n    pass\n', encoding="utf-8"
    )
    (tmp_path / "data" / "login.yaml").write_text(
        "api: POST /api/login\ncases:\n  - id: TC-LOGIN-01\n    title: 正常登录\n    expect: {ok: true}\n"
        "  - id: TC-LOGIN-02\n    title: 已知缺陷\n    xfail_strict: BUG-9\n    expect: {ok: true}\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "api" / "test_login.py").write_text(
        'import pytest\nfrom caseflow import load_cases, run_case\n'
        '@pytest.mark.parametrize("case", load_cases("data/login.yaml"))\n'
        'def test_login(case):\n    run_case(case)\n',
        encoding="utf-8",
    )

    col = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert col.returncode == 0, col.stderr
    assert "4 tests collected" in col.stdout

    monkeypatch.setenv("CASEFLOW_PROJECT", str(tmp_path))
    config.reset()
    assert export.main() == 0
    csv_path = tmp_path / "reports" / "用例表.csv"
    md_path = tmp_path / "reports" / "用例表.md"
    assert csv_path.is_file() and md_path.is_file()
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ids = [r["编号"] for r in rows]
    assert ids == ["TC-LOGIN-01", "TC-LOGIN-02", "TC-API-01", "TC-UI-01"]
    assert rows[1]["状态"] == "已知缺陷(xfail)"
    assert rows[3]["类型"] == "UI"


def test_skill_install_and_print(tmp_path, capsys):
    fake_home = tmp_path / "home"
    proj = tmp_path / "proj"
    proj.mkdir()

    # --print 只打印不写任何文件
    assert skill.install_skill(print_only=True, home=fake_home, target=str(proj)) == 0
    assert "caseflow v3" in capsys.readouterr().out
    assert not fake_home.exists() and not any(proj.iterdir())

    # 默认只安装通用全局技能，绝不往项目里塞 .cursor 或 AGENTS.md
    assert skill.install_skill(home=fake_home, target=str(proj)) == 0
    assert (fake_home / ".agents" / "skills" / "ai-testing" / "SKILL.md").is_file()
    assert (fake_home / ".agents" / "skills" / "ai-testing" / "references" / "ui-guide.md").is_file()
    assert (fake_home / ".claude" / "skills" / "ai-testing" / "SKILL.md").is_file()
    assert (fake_home / ".claude" / "skills" / "ai-testing" / "references" / "api-guide.md").is_file()
    assert not (proj / ".cursor").exists() and not (proj / "AGENTS.md").exists()

    # --cursor 和 --agents 按需写入，且已有 AGENTS.md 不覆盖
    assert skill.install_skill(cursor=True, agents=True, home=fake_home, target=str(proj)) == 0
    assert (proj / ".cursor" / "rules" / "ai-testing-workflow.mdc").is_file()
    assert (proj / "AGENTS.md").is_file()
    (proj / "AGENTS.md").write_text("# my agents\n", encoding="utf-8")
    capsys.readouterr()
    assert skill.install_skill(agents=True, home=fake_home, target=str(proj)) == 0
    assert "[SKIP]" in capsys.readouterr().out
    assert (proj / "AGENTS.md").read_text(encoding="utf-8") == "# my agents\n"


def test_guard_scan_diff_and_allow(demo_proj, monkeypatch, capsys):
    root, _, _ = demo_proj
    sample_diff = """diff --git a/tests/api/test_demo.py b/tests/api/test_demo.py
--- a/tests/api/test_demo.py
+++ b/tests/api/test_demo.py
@@ -1,4 +1,5 @@
-    assert resp.data["total"] == 100
+    @pytest.mark.skip(reason="flaky")
+    # assert comment only
diff --git a/data/demo.yaml b/data/demo.yaml
--- a/data/demo.yaml
+++ b/data/demo.yaml
@@ -1,3 +1,4 @@
-    expect: {code: 0}
+    xfail: 临时跳过
"""
    findings = guard.scan_diff(sample_diff)
    assert len(findings) == 4
    assert any("[DEL-ASSERT]" in x for x in findings)
    assert any("[ADD-SKIP]" in x for x in findings)
    assert any("[MOD-EXPECT]" in x for x in findings)
    assert any("[ADD-XFAIL]" in x for x in findings)

    monkeypatch.setattr(guard, "_git_diff", lambda r, b: (0, sample_diff, ""))
    assert guard.main() == 1
    assert "[BLOCKED]" in capsys.readouterr().out
    assert guard.main(allow=True) == 0
    assert "[ALLOW]" in capsys.readouterr().out


def test_mutate_observe_mode_and_diff_response(demo_proj):
    root, state, _ = demo_proj
    state.echo_calls = 0
    (root / "tests" / "api").mkdir(parents=True)
    (root / "tests" / "api" / "test_weak.py").write_text(
        'from caseflow import client\n'
        'def test_only_status():\n'
        '    client(None).get("/api/echo").expect_status(200)\n',
        encoding="utf-8",
    )
    assert mutate.main(["tests/api/test_weak.py"]) == 1
    assert state.echo_calls == 1  # 基线打 1 次真实请求，4 个变异体全部本地 tape 回放
    assert mutate.main(["tests/api/test_weak.py"], observe=True) == 0
    assert state.echo_calls == 2  # 第二次跑基线打 1 次，变异回放不增发

    def _fake_resp(status: int, payload: dict) -> Resp:
        raw = type("Raw", (), {"status_code": status, "text": json.dumps(payload), "json": lambda self: payload})()
        return Resp(raw)

    r1 = _fake_resp(200, {"code": 0, "timestamp": 111, "data": {"id": 1}})
    r2 = _fake_resp(200, {"code": 0, "timestamp": 222, "data": {"id": 1}})
    r3 = _fake_resp(200, {"code": 0, "timestamp": 222, "data": {"id": 2}})
    assert diff_response(r1, r2) == []
    with pytest.raises(AssertionError, match="data.id"):
        diff_response(r1, r3)

    assert ui._format_dom_doc("# Page", "") == "# Page"
    assert ui._format_dom_doc("# Page", "点提交后弹出订单号").startswith("> **检查点备忘**：点提交后弹出订单号\n\n# Page")


def test_dotenv_allow_hosts_secret_and_page_as_diagnostics(demo_proj, monkeypatch):
    root, _, _ = demo_proj
    (root / ".env").write_text(
        'export EXPORTED=ok # trailing comment\n'
        'QUOTED_HASH="Sec#123"\n'
        "UNMATCHED_QUOTE='abc\n"
        'USER_NAME=admin\n'
        'USER_PASSWORD="Secret#123"\n',
        encoding="utf-8",
    )
    config.reset()
    assert env("EXPORTED") == "ok"
    assert env("QUOTED_HASH") == "Sec#123"
    assert env("UNMATCHED_QUOTE") == "'abc"

    # 用户名叫 admin 不应误伤 YAML 里出现 role: "admin"，但密码明文仍应拦截
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "role_ok.yaml").write_text(
        'api: POST /api/items\nrole: user\ncases:\n'
        '  - id: TC-1\n    title: 正常字段包含admin\n    json: {name: "admin"}\n    expect: {ok: true}\n',
        encoding="utf-8",
    )
    assert len(data.read_cases("data/role_ok.yaml")) == 1
    (root / "data" / "role_bad.yaml").write_text(
        'api: POST /api/items\nrole: user\ncases:\n'
        '  - id: TC-2\n    title: 泄露密码\n    json: {name: "Secret#123"}\n    expect: {ok: true}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="出现账号/密码明文"):
        data.read_cases("data/role_bad.yaml")

    # safety.allow_hosts 白名单生效：不含 prod 字样的域名也会被拦
    raw_cfg = (root / "caseflow.yaml").read_text(encoding="utf-8")
    (root / "caseflow.yaml").write_text(
        raw_cfg + '\nsafety:\n  allow_hosts: ["127.0.0.1", "*.test.local"]\n',
        encoding="utf-8",
    )
    config.reset()
    called = []
    monkeypatch.setattr(
        requests.Session, "request",
        lambda self, m, u, **kw: called.append((m, u)) or type("R", (), {
            "status_code": 200, "text": "{}", "headers": {}, "request": None, "json": lambda self: {},
        })(),
    )
    blocked_api = Client(None, base_url="https://api.company.com")
    with pytest.raises(ConfigError, match="拒绝向疑似生产环境"):
        blocked_api.post("/orders", json={"x": 1})
    Client(None, base_url="https://api.test.local").post("/orders", json={"x": 1})
    assert len(called) == 1

    # 非默认 page fixture（如 page_as）失败时也能生成 UI 诊断包
    events = {}
    sections = []

    class _FakePage:
        url = "http://localhost/checkout"

        def on(self, ev, cb):
            events[ev] = cb

        def screenshot(self, path, full_page=True):
            Path(path).write_bytes(b"png")

    class _FakeNode:
        nodeid = "tests/ui/test_page_as.py::test_demo"
        _caseflow_rep_call = type("Rep", (), {"failed": True})()

        def add_report_section(self, *args):
            sections.append(args)

    req = type("Req", (), {"node": _FakeNode()})()
    flush = plugin.attach_ui_diagnostics(req, _FakePage(), tag="user_1")
    events["console"](type("Msg", (), {"type": "error", "text": "boom"})())
    events["response"](type("Res", (), {"status": 500, "url": "http://x/api", "request": type("Q", (), {"method": "POST"})()})())
    flush()
    flush()  # 重复调用不重复写
    md_file = root / "reports" / "ui" / "tests_ui_test_page_as_py_test_demo_user_1.md"
    assert md_file.is_file()
    content = md_file.read_text(encoding="utf-8")
    assert "500 POST http://x/api" in content and "[error] boom" in content
    assert len(sections) == 1
