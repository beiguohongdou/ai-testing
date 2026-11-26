"""接口运行时：HTTP 客户端（按角色复用登录）、响应断言、动态造数、失败时自动给出 curl。

    from caseflow import client
    api = client("admin")                       # 同一角色整个测试进程只登录一次
    resp = api.post("/items", json={"name": gen.unique()})
    resp.expect_ok().expect("data.name", contains="item")
"""
from __future__ import annotations

import calendar
import fnmatch
import importlib
import json
import os
import random
import re
import shlex
import string
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

import requests

from caseflow.config import ConfigError, config, env, fill_env, setting

MISSING = object()


# ---------------------------------------------------------------- 动态造数

class gen:
    """动态测试数据。YAML 里写 ${unique} ${phone} ${today:-1d} 等，对应这里的函数。"""

    @staticmethod
    def unique(n: int = 8) -> str:
        return uuid.uuid4().hex[:n]

    @staticmethod
    def uuid() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def phone() -> str:
        """中国大陆 11 位手机号格式（测试用，不保证真实存在）。"""
        return "1" + random.choice("3456789") + "".join(random.choices(string.digits, k=9))

    @staticmethod
    def timestamp(unit: str = "s") -> int:
        return int(time.time() * 1000) if unit == "ms" else int(time.time())

    @staticmethod
    def today(offset: str = "", base: date | None = None, fmt: str = "%Y-%m-%d") -> str:
        """offset 如 '-18y+1d'、'+1m'、'-7d'；月末 / 闰日自动取当月最后一天。"""
        d = base or date.today()
        for sign, n, unit in re.findall(r"([+-])(\d+)([ymd])", offset):
            n = int(n) if sign == "+" else -int(n)
            if unit == "d":
                d += timedelta(days=n)
            else:
                months = d.year * 12 + d.month - 1 + (n * 12 if unit == "y" else n)
                y, m = divmod(months, 12)
                d = d.replace(year=y, month=m + 1, day=min(d.day, calendar.monthrange(y, m + 1)[1]))
        return d.strftime(fmt)

    @staticmethod
    def now(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
        return datetime.now().strftime(fmt)

    @staticmethod
    def text(n: int) -> str:
        """恰好 n 个字符（数字循环），测长度边界用。"""
        return ("0123456789" * (n // 10 + 1))[:n]

    @staticmethod
    def alpha(n: int = 8) -> str:
        return "".join(random.choices(string.ascii_lowercase, k=n))

    @staticmethod
    def digits(n: int = 6) -> str:
        return "".join(random.choices(string.digits, k=n))


_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
_GENERATORS = {
    "unique": lambda a: gen.unique(int(a) if a else 8),
    "uuid": lambda a: gen.uuid(),
    "phone": lambda a: gen.phone(),
    "timestamp": lambda a: gen.timestamp(a or "s"),
    "today": lambda a: gen.today(a or ""),
    "now": lambda a: gen.now(a or "%Y-%m-%d %H:%M:%S"),
    "len": lambda a: gen.text(int(a)),
    "alpha": lambda a: gen.alpha(int(a) if a else 8),
    "digits": lambda a: gen.digits(int(a) if a else 6),
}


def _resolve(ref: str, variables: dict) -> Any:
    name, _, arg = ref.partition(":")
    if name in _GENERATORS:
        try:
            return _GENERATORS[name](arg)
        except ValueError:
            raise ValueError(f"${{{ref}}} 参数不对，示例：${{unique}} ${{unique:12}} ${{today:-1d}} ${{len:255}} ${{timestamp:ms}}") from None
    if re.fullmatch(r"[A-Z][A-Z0-9_]*", ref):
        value = env(ref)
        if value is None:
            raise ConfigError(f"${{{ref}}} 没有配置：写进 .env 或系统环境变量")
        return value
    if ref in variables:
        return variables[ref]
    raise ValueError(f"${{{ref}}} 未定义：大写=环境变量，小写=vars 里的变量，可用生成器 {sorted(_GENERATORS)}")


def fill(value: Any, variables: dict | None = None) -> Any:
    """递归替换 ${...}。整个字符串就是一个 ${...} 时保留原类型（如 int）。"""
    variables = variables or {}
    if isinstance(value, dict):
        return {k: fill(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, variables) for v in value]
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole:
            return _resolve(whole.group(1), variables)
        return _PLACEHOLDER.sub(lambda m: str(_resolve(m.group(1), variables)), value)
    return value


# ---------------------------------------------------------------- 取值与断言

_RAISE = object()


def dig(data: Any, path: str | list, default: Any = _RAISE) -> Any:
    """'data.items.0.id' → data['data']['items'][0]['id']。
    列表上的数字段当下标，字典上一律当键（{"1001": ...} 也能取）；键里有点号时传列表 ['a.b', 'c']。"""
    parts = path if isinstance(path, list) else ([] if path in ("", None) else str(path).split("."))
    cur = data
    for p in parts:
        try:
            if isinstance(cur, list):
                cur = cur[int(p)]
            elif isinstance(cur, dict):
                cur = cur[p]
            else:
                raise KeyError(p)
        except (KeyError, IndexError, ValueError, TypeError):
            if default is _RAISE:
                raise KeyError(f"取不到 {path!r}：在 {p!r} 处断开") from None
            return default
    return cur


_TYPES = {"str": str, "int": int, "float": (int, float), "number": (int, float), "bool": bool,
          "list": list, "dict": dict, "null": type(None)}
OPS = ("eq", "ne", "gt", "ge", "lt", "le", "in", "not_in", "contains", "not_contains",
       "regex", "len", "len_gt", "len_ge", "type", "exists")


def check(actual: Any, ops: dict, label: str = "值") -> list[str]:
    """按 ops 检查一个值，返回失败说明列表（空 = 全部通过）。actual 为 MISSING 表示字段不存在。"""
    errors = []
    unknown = set(ops) - set(OPS)
    if unknown:
        return [f"{label}: 不认识的比较方式 {sorted(unknown)}，可用：{list(OPS)}"]
    if "exists" in ops:
        if bool(ops["exists"]) != (actual is not MISSING):
            errors.append(f"{label}: 应{'存在' if ops['exists'] else '不存在'}")
        if len(ops) == 1 or actual is MISSING:
            return errors
    if actual is MISSING:
        return [f"{label}: 字段不存在"]
    for op, want in ops.items():
        try:
            ok = {
                "eq": lambda: actual == want,
                "ne": lambda: actual != want,
                "gt": lambda: actual > want,
                "ge": lambda: actual >= want,
                "lt": lambda: actual < want,
                "le": lambda: actual <= want,
                "in": lambda: actual in want,
                "not_in": lambda: actual not in want,
                "contains": lambda: want in actual,
                "not_contains": lambda: want not in actual,
                "regex": lambda: actual is not None and re.search(str(want), str(actual)) is not None,
                "len": lambda: len(actual) == want,
                "len_gt": lambda: len(actual) > want,
                "len_ge": lambda: len(actual) >= want,
                "type": lambda: isinstance(actual, _TYPES[want]) and not (want in ("int", "float", "number") and isinstance(actual, bool)),
                "exists": lambda: True,
            }[op]()
        except (TypeError, KeyError) as e:
            ok, want = False, f"{want}（比较出错：{type(e).__name__}）"
        if not ok:
            errors.append(f"{label}: 期望 {op} {want!r}，实际 {_short(actual)}")
    return errors


def _short(v: Any, n: int = 200) -> str:
    s = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else repr(v)
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------- curl 复现命令

def _redact_obj(obj: Any, fields: set[str]) -> Any:
    if isinstance(obj, dict):
        return {k: "<REDACTED>" if str(k).lower() in fields else _redact_obj(v, fields) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_obj(v, fields) for v in obj]
    return obj


def to_curl(req: requests.PreparedRequest | None, redact: bool = True) -> str:
    """把实际发出的请求转成 curl 命令；默认把 token / 密码打码（设 CASEFLOW_CURL_RAW=1 看原值）。"""
    if req is None:
        return "（无请求信息）"
    redact = redact and not env("CASEFLOW_CURL_RAW")
    headers_r = {h.lower() for h in setting("redact", "headers", default=[])}
    fields_r = {f.lower() for f in setting("redact", "fields", default=[])}
    parts = ["curl", "-X", req.method or "GET", shlex.quote(req.url or "")]
    for k, v in req.headers.items():
        if k.lower() in ("content-length", "accept-encoding", "connection", "user-agent"):
            continue
        parts += ["-H", shlex.quote(f"{k}: {'<REDACTED>' if redact and k.lower() in headers_r else v}")]
    body = req.body
    if body:
        if isinstance(body, bytes):
            try:
                body = body.decode("utf-8")
            except UnicodeDecodeError:
                body = "<二进制内容省略>"
        ctype = req.headers.get("Content-Type", "")
        if redact and "json" in ctype:
            try:
                body = json.dumps(_redact_obj(json.loads(body), fields_r), ensure_ascii=False)
            except ValueError:
                pass
        elif redact and "x-www-form-urlencoded" in ctype:
            body = urlencode([(k, "<REDACTED>" if k.lower() in fields_r else v) for k, v in parse_qsl(body)])
        elif "multipart" in ctype:
            body = "<multipart 上传内容省略>"
        parts += ["--data-raw", shlex.quote(body)]
    return " ".join(parts)


# ---------------------------------------------------------------- 响应

class Resp:
    """包一层 requests.Response：可链式断言；失败信息自带 请求 / 状态码 / 响应片段 / curl。"""

    def __init__(self, raw: requests.Response, envelope: dict | None = None):
        self.raw = raw
        self.envelope = envelope
        self._status = raw.status_code
        self._body = MISSING
        self.mutant: str | None = None

    # 让 resp.headers / resp.elapsed 等照常可用
    def __getattr__(self, name):
        return getattr(self.raw, name)

    @property
    def status_code(self) -> int:
        return self._status

    def json(self) -> Any:
        if self._body is MISSING:
            self._body = self.raw.json()
        return self._body

    @property
    def body(self) -> Any:
        """JSON 能解析就是对象，否则是文本。"""
        try:
            return self.json()
        except ValueError:
            return self.raw.text

    @property
    def text(self) -> str:
        return self.raw.text if self._body is MISSING else json.dumps(self._body, ensure_ascii=False)

    def get(self, path: str | list, default: Any = None) -> Any:
        return dig(self.body, path, default)

    def _env_field(self, key: str) -> Any:
        if not self.envelope:
            raise ConfigError("caseflow.yaml 没有配置 api.envelope，不能用 code / msg / data / expect_ok / expect_code")
        return dig(self.body, self.envelope.get(key, key), None)

    @property
    def code(self) -> Any:
        return self._env_field("code")

    @property
    def msg(self) -> Any:
        return self._env_field("msg")

    @property
    def data(self) -> Any:
        return self._env_field("data")

    # ---- 断言（都返回 self，可链式）

    def fail(self, reason: str) -> None:
        req = self.raw.request
        where = f"{req.method} {urlparse(req.url).path}" if req is not None else "请求"
        mutant = f"\n[变异体] {self.mutant}" if self.mutant else ""
        raise AssertionError(
            f"{reason}\n[请求] {where} → HTTP {self.status_code}\n[响应] {_short(self.body, 500)}\n[复现] {to_curl(req)}{mutant}"
        )

    def expect_status(self, status: int) -> "Resp":
        if self.status_code != status:
            self.fail(f"HTTP 状态码期望 {status}，实际 {self.status_code}")
        return self

    def expect_ok(self) -> "Resp":
        """成功：HTTP < 400，且（配置了 envelope 时）业务码等于 ok 值。"""
        if self.status_code >= 400:
            self.fail(f"期望成功，HTTP 状态码 {self.status_code}")
        if self.envelope:
            ok = self.envelope.get("ok", 0)
            if self.code != ok:
                self.fail(f"期望业务成功 code={ok!r}，实际 code={self.code!r} msg={self.msg!r}")
        return self

    def expect_code(self, code: Any) -> "Resp":
        if self.code != code:
            self.fail(f"业务码期望 {code!r}，实际 {self.code!r}（msg={self.msg!r}）")
        return self

    def expect(self, path: str | list = "", **ops) -> "Resp":
        """resp.expect("data.name", eq="x")、expect("data.id", exists=True)、expect("data.list", len_gt=0)"""
        if not ops:
            raise ValueError("expect 至少给一个比较，如 eq= / exists= / contains=")
        actual = dig(self.body, path, MISSING) if path else self.body
        errors = check(actual, ops, f"body.{path}" if path else "body")
        if errors:
            self.fail("；".join(errors))
        return self


@contextmanager
def on_fail(resp: Resp):
    """自己写 assert 时用：失败信息里自动带上请求、状态码、响应和 curl。"""
    try:
        yield resp
    except (AssertionError, KeyError, IndexError, TypeError, ValueError) as e:
        if isinstance(e, AssertionError) and "[复现]" in str(e):
            raise
        resp.fail(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- 变异（caseflow mutate 用）

def _mutate(resp: Resp, name: str) -> None:
    body = resp.body
    envelope = resp.envelope
    if name == "empty_body":
        resp._body = {} if isinstance(body, dict) else [] if isinstance(body, list) else ""
    elif name == "flip_status":
        resp._status = 500 if resp.status_code < 400 else 200
    elif name == "flip_code" and envelope and isinstance(body, dict):
        key, ok = envelope.get("code", "code"), envelope.get("ok", 0)
        body = dict(body)
        body[key] = 999999 if body.get(key) == ok else ok
        resp._body = body
    elif name == "null_data" and envelope and isinstance(body, dict):
        key = envelope.get("data", "data")
        if body.get(key) is None:
            return
        body = dict(body)
        body[key] = None
        resp._body = body
    else:
        return
    resp.mutant = name
    touched = os.environ.get("CASEFLOW_TOUCHED")
    test = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" ", 1)[0]
    if touched and test:
        with open(touched, "a", encoding="utf-8") as f:
            f.write(test + "\n")


# ---------------------------------------------------------------- 客户端

def _host_blocked(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    allow = setting("safety", "allow_hosts", default=[]) or []
    if allow:
        return not any(fnmatch.fnmatch(host, str(pat).lower()) for pat in allow)
    words = {w.lower() for w in setting("safety", "block_host_words", default=[])}
    return bool(words & set(re.split(r"[.\-]", host)))


_TAPE_SEQ: dict[str, int] = {}
_TAPE_CACHE: dict[tuple[str, int], dict[tuple[str, int], dict]] = {}


def _current_test_id() -> str:
    return os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" ", 1)[0] or "__global__"


def _next_tape_seq(test_id: str) -> int:
    seq = _TAPE_SEQ.get(test_id, 0) + 1
    _TAPE_SEQ[test_id] = seq
    return seq


def _load_tape(path: str) -> dict[tuple[str, int], dict]:
    p = os.path.abspath(path)
    try:
        mtime = os.stat(p).st_mtime_ns
    except OSError:
        return {}
    key = (p, mtime)
    if key not in _TAPE_CACHE:
        entries: dict[tuple[str, int], dict] = {}
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    entries[(item["test"], int(item["seq"]))] = item
        _TAPE_CACHE[key] = entries
    return _TAPE_CACHE[key]


def _record_tape(path: str, test_id: str, seq: int, method: str, url: str, raw: requests.Response) -> None:
    entry = {
        "test": test_id,
        "seq": seq,
        "method": method,
        "url": url,
        "status": raw.status_code,
        "text": raw.text,
        "headers": dict(raw.headers or {}),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class _FakeReq:
    def __init__(self, method: str, url: str, headers: dict, body: Any = None):
        self.method = method
        self.url = url
        self.headers = dict(headers)
        self.body = body


class _FakeRawResponse:
    def __init__(self, status_code: int, text: str, headers: dict, req: _FakeReq):
        self.status_code = status_code
        self.text = text
        self.headers = headers
        self.request = req

    def json(self) -> Any:
        return json.loads(self.text)


class Client:
    """requests.Session + 基础地址 + 公共头 + 超时 + 登录 + 签名钩子 + 防误打生产。"""

    def __init__(self, role: str | None = None, base_url: str | None = None):
        self.base = (base_url or setting("api", "base_url") or "").rstrip("/")
        if not self.base:
            raise ConfigError("caseflow.yaml 没配 api.base_url（可写 ${API_BASE_URL} 再在 .env 里填）")
        self.timeout = setting("api", "timeout", default=15)
        self.envelope = setting("api", "envelope")
        self.session = requests.Session()
        self.session.headers.update({k: str(v) for k, v in (setting("api", "headers", default={}) or {}).items()})
        hook = setting("api", "hook")
        self._hook = None
        if hook:
            mod, _, fn = hook.partition(":")
            self._hook = getattr(importlib.import_module(mod), fn)
        self.role = role
        if role:
            self.login(role)

    def url(self, path: str) -> str:
        return path if path.startswith(("http://", "https://")) else self.base + "/" + path.lstrip("/")

    def request(self, method: str, path: str, _kind: str = "step", **kw) -> Resp:
        method = method.upper()
        url = self.url(path)
        if method != "GET" and _host_blocked(url) and not env("CASEFLOW_ALLOW_PROD"):
            raise ConfigError(f"拒绝向疑似生产环境发 {method} 请求：{urlparse(url).hostname}（确需放行设 CASEFLOW_ALLOW_PROD=1）")
        kw.setdefault("timeout", self.timeout)
        if self._hook:
            kw = self._hook(method, url, kw) or kw
        record_path = os.environ.get("CASEFLOW_TAPE_RECORD")
        replay_path = os.environ.get("CASEFLOW_TAPE_REPLAY")
        raw = None
        if record_path or replay_path:
            test_id = _current_test_id()
            seq = _next_tape_seq(test_id)
            if replay_path:
                hit = _load_tape(replay_path).get((test_id, seq))
                if hit and hit.get("method") == method:
                    req_headers = {**self.session.headers, **(kw.get("headers") or {})}
                    req_body = json.dumps(kw["json"], ensure_ascii=False) if "json" in kw and kw["json"] is not None else None
                    raw = _FakeRawResponse(hit["status"], hit["text"], hit.get("headers") or {},
                                           _FakeReq(method, url, req_headers, req_body))
        if raw is None:
            raw = self.session.request(method, url, **kw)
            if record_path:
                _record_tape(record_path, test_id, seq, method, url, raw)
        resp = Resp(raw, self.envelope)
        mutant = os.environ.get("CASEFLOW_MUTANT")
        if mutant and _kind == "step":
            _mutate(resp, mutant)
        return resp

    def get(self, path, **kw) -> Resp:
        return self.request("GET", path, **kw)

    def post(self, path, **kw) -> Resp:
        return self.request("POST", path, **kw)

    def put(self, path, **kw) -> Resp:
        return self.request("PUT", path, **kw)

    def patch(self, path, **kw) -> Resp:
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw) -> Resp:
        return self.request("DELETE", path, **kw)

    def login(self, role: str) -> None:
        lg = setting("api", "login")
        roles = config()["api"].get("roles") or {}
        if not lg:
            raise ConfigError("caseflow.yaml 没配 api.login，不能按角色登录")
        if role not in roles:
            raise ConfigError(f"角色 {role!r} 不在 api.roles 里：{sorted(roles)}")
        cred = fill_env(roles[role])  # 只解析用到的角色，其他角色的账号没配也不影响
        payload = {lg.get("user_field", "username"): cred["user"], lg.get("password_field", "password"): cred["password"]}
        payload |= lg.get("extra") or {}
        kw = {"json": payload} if lg.get("body", "json") == "json" else {"data": payload}
        r = self.request(lg.get("method", "POST"), lg["path"], _kind="login", **kw)
        if r.status_code >= 400 or (self.envelope and r.code != self.envelope.get("ok", 0)):
            r.fail(f"角色 {role} 登录失败")
        if token_path := lg.get("token_path"):
            token = dig(r.body, token_path, None)
            if token is None:
                r.fail(f"登录响应里取不到 token（token_path={token_path}）")
            self.session.headers[lg.get("header", "Authorization")] = lg.get("prefix", "Bearer ") + str(token)


_CLIENTS: dict[tuple, Client] = {}


def client(role: str | None = None, fresh: bool = False) -> Client:
    """按角色复用客户端：同一角色在一次 pytest 运行里只登录一次。fresh=True 强制新开会话（测登出 / 改密码时用）。"""
    key = (role, setting("api", "base_url"))
    if fresh or key not in _CLIENTS:
        c = Client(role)
        if fresh:
            return c
        _CLIENTS[key] = c
    return _CLIENTS[key]


def reset_clients() -> None:
    _CLIENTS.clear()
    _TAPE_SEQ.clear()
    _TAPE_CACHE.clear()


def _strip_ignored(obj: Any, ignore: set[str]) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_ignored(v, ignore) for k, v in obj.items() if k not in ignore}
    if isinstance(obj, list):
        return [_strip_ignored(v, ignore) for v in obj]
    return obj


def _collect_diffs(a: Any, b: Any, path: str = "$") -> list[str]:
    if type(a) is not type(b):
        return [f"{path}: 类型不同 {type(a).__name__}({_short(a, 60)}) != {type(b).__name__}({_short(b, 60)})"]
    if isinstance(a, dict):
        diffs: list[str] = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                diffs.append(f"{path}.{k}: 左侧缺失，右侧={_short(b[k], 60)}")
            elif k not in b:
                diffs.append(f"{path}.{k}: 右侧缺失，左侧={_short(a[k], 60)}")
            else:
                diffs.extend(_collect_diffs(a[k], b[k], f"{path}.{k}"))
        return diffs
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: 列表长度不同 {len(a)} != {len(b)}"]
        diffs = []
        for idx, (va, vb) in enumerate(zip(a, b)):
            diffs.extend(_collect_diffs(va, vb, f"{path}[{idx}]"))
        return diffs
    if a != b:
        return [f"{path}: {_short(a, 80)} != {_short(b, 80)}"]
    return []


def diff_response(resp_a: Resp, resp_b: Resp, ignore_keys: tuple[str, ...] | list[str] = (
    "timestamp", "trace_id", "request_id"
), assert_same: bool = True) -> list[str]:
    """差分对比两个响应（忽略动态字段），用于新旧版本/双环境回归校验。"""
    diffs: list[str] = []
    if resp_a.status_code != resp_b.status_code:
        diffs.append(f"status_code: {resp_a.status_code} != {resp_b.status_code}")
    ignore = set(ignore_keys)
    diffs.extend(_collect_diffs(_strip_ignored(resp_a.body, ignore), _strip_ignored(resp_b.body, ignore)))
    if assert_same and diffs:
        raise AssertionError("差分响应不一致：\n- " + "\n- ".join(diffs[:20]))
    return diffs
