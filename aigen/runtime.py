"""生成的测试在运行时依赖的全部东西：项目配置、HTTP 客户端、动态数据、失败信息、响应录制。"""
import sys

if sys.version_info < (3, 10):
    raise RuntimeError(f"aigen 需要 Python 3.10+，当前 {sys.version.split()[0]}")

import calendar
import importlib
import json
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Any, Literal

import requests
import yaml
from pydantic import BaseModel, ConfigDict, model_validator

_CACHED = []


def cached(fn):
    """带缓存且能被 reset() 清掉（自检切换临时项目时用）。"""
    c = cache(fn)
    _CACHED.append(c)
    return c


def reset() -> None:
    for c in _CACHED:
        c.cache_clear()


def root() -> Path:
    """项目根目录：环境变量 AIGEN_PROJECT，默认是 aigen/ 的上一级。"""
    return Path(os.environ.get("AIGEN_PROJECT") or Path(__file__).resolve().parent.parent)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(_Strict):
    method: str = "POST"
    path: str
    user_field: str
    password_field: str
    body: Literal["json", "form"] = "json"
    token_field: str | None = None  # 点路径，如 data.token；不填表示靠 Cookie 保持登录
    header: str = "Authorization"
    prefix: str = "Bearer "


class OAuth2(_Strict):
    """password：roles 给 [账号常量, 密码常量]；client_credentials：roles 给 [client_id 常量, client_secret 常量]。"""
    token_url: str  # 路径（拼在 base_url 后）或完整 URL
    grant: Literal["password", "client_credentials"] = "password"
    client_id: str | None = None  # 常量名，password 模式需要时填
    client_secret: str | None = None
    scope: str | None = None


class ApiProfile(_Strict):
    base_url: str  # 常量名
    timeout: float = 10
    headers: dict[str, str] = {}  # 每个请求都带的头，值可写 ${常量}，如 X-Api-Key
    login: Login | None = None
    oauth2: OAuth2 | None = None  # 和 login 二选一
    roles: dict[str, tuple[str, str]] = {}
    client_class: str | None = None  # 特殊鉴权（签名等）：继承 aigen.runtime.Client 的类，"包.模块:类名"

    @model_validator(mode="after")
    def _one_login(self):
        if self.login and self.oauth2:
            raise ValueError("api.login 和 api.oauth2 只能配一个")
        return self


class UiProfile(_Strict):
    page_dir: str = "page"
    fixture: str = "page"
    timeout: int = 5000


class Material(_Strict):
    name: str
    path: str
    kind: Literal["openapi", "prd", "other"] = "other"


class Profile(_Strict):
    constants: str | None = None  # 常量模块；同名环境变量优先
    encoding: str = "utf-8"  # 材料文件编码
    ac_pattern: str = r"AC-\d+"  # PRD 验收编号格式
    export_dir: str = "docs/用例"
    redact: list[dict[str, str]] = []  # 批量起草发给模型前的脱敏规则：[{pattern: 正则, replace: 替换}]
    api: ApiProfile
    ui: UiProfile = UiProfile()
    materials: list[Material] = []
    waive: dict[str, str] = {}


@cached
def profile() -> Profile:
    p = root() / "aigen.yaml"
    if not p.exists():
        raise FileNotFoundError(f"没有项目配置 {p}；从 aigen/aigen.yaml.example 复制到项目根再改")
    return Profile.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


@cached
def consts():
    name = profile().constants
    return importlib.import_module(name) if name else None


@cached
def dotenv() -> dict[str, str]:
    """项目根的 .env（KEY=VALUE，# 开头是注释），只放本机、不提交。"""
    p = root() / ".env"
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                out[key.strip()] = value.strip().strip("\"'")
    return out


def env(name: str) -> str | None:
    """系统环境变量优先，其次 .env；空值当作没配。"""
    return os.environ.get(name) or dotenv().get(name) or None


def has_const(name: str) -> bool:
    return env(name) is not None or (consts() is not None and hasattr(consts(), name))


def const(name: str) -> Any:
    """${大写常量}：环境变量 / .env 优先（CI 注入、本机存密码），其次常量模块。"""
    if (value := env(name)) is not None:
        return value
    if consts() is not None and hasattr(consts(), name):
        return getattr(consts(), name)
    raise KeyError(f"常量 {name} 不在环境变量、.env 里，也不在 {profile().constants}")


def fill(text: str) -> str:
    """把字符串里的 ${常量} 换成值（aigen.yaml 的 headers 用）。"""
    return re.sub(r"\$\{([A-Z][A-Z0-9_]*)\}", lambda m: str(const(m.group(1))), text)


def dig(data: Any, path: str) -> Any:
    for p in path.split("."):
        data = data[int(p)] if p.isdigit() else data[p]
    return data


def ui_timeout() -> int:
    return profile().ui.timeout


def material_path(name: str) -> Path:
    m = next((m for m in profile().materials if m.name == name), None)
    if m is None:
        raise ValueError(f"aigen.yaml 没有登记材料 {name}")
    return root() / m.path


@cached
def contract_doc(name: str) -> dict:
    """OpenAPI 3 / Swagger 2，JSON 或 YAML 都行（JSON 是 YAML 的子集）。"""
    return yaml.safe_load(material_path(name).read_text(encoding=profile().encoding)) or {}


def _resolve(doc: dict, node: Any, depth: int = 0) -> Any:
    """展开 $ref；OpenAPI 3.0 的 nullable 转成 JSON Schema 写法。"""
    if depth > 30:  # 循环引用到此为止，不再校验更深的结构
        return {}
    if isinstance(node, list):
        return [_resolve(doc, x, depth + 1) for x in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = doc
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target[part]
        return _resolve(doc, target, depth + 1)
    out = {k: _resolve(doc, v, depth + 1) for k, v in node.items()}
    if out.pop("nullable", False) and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    return out


def response_schema(material: str, method: str, path: str, code: str) -> dict | None:
    doc = contract_doc(material)
    op = (doc.get("paths") or {}).get(path, {}).get(method.lower())
    if op is None:
        return None
    resp = {str(k): v for k, v in op.get("responses", {}).items()}.get(str(code))
    if resp is None:
        return None
    resp = _resolve(doc, resp)
    if "content" in resp:  # OpenAPI 3
        media = resp["content"].get("application/json") or next(iter(resp["content"].values()), {})
        return media.get("schema")
    return resp.get("schema")  # Swagger 2


def assert_contract(body: Any, material: str, method: str, path: str, code: str) -> None:
    """响应 body 必须符合契约里这个接口、这个状态码定义的结构。"""
    from jsonschema import Draft202012Validator

    schema = response_schema(material, method, path, code)
    if schema is None:
        raise AssertionError(f"契约里 {method} {path} responses.{code} 没有定义结构")
    err = next(iter(sorted(Draft202012Validator(schema).iter_errors(body), key=lambda e: list(e.path))), None)
    if err is not None:
        where = ".".join(str(p) for p in err.absolute_path) or "body"
        raise AssertionError(f"响应不符合契约 {method} {path} responses.{code}：{where}：{err.message}")


def matches(pattern: str, value: Any) -> bool:
    return value is not None and re.search(pattern, str(value)) is not None


def sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def upload(path: str) -> tuple[str, bytes]:
    """YAML 里 files: {字段: 路径}，路径相对项目根。"""
    p = root() / path
    return p.name, p.read_bytes()


def xml_body(resp: requests.Response) -> ET.Element:
    return ET.fromstring(resp.content)


def xml_text(el: ET.Element, path: str) -> str | None:
    found = el.find(path)
    return None if found is None else (found.text or "")


def _write(rec: dict) -> None:
    out = os.environ.get("AIGEN_RECORD")
    if not out:
        return
    rec["test"] = os.environ.get("PYTEST_CURRENT_TEST", "").split(" ")[0].split("::")[-1].split("[")[0]
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _record(resp: requests.Response, kind: str) -> None:
    """设置环境变量 AIGEN_RECORD=文件 时，把响应追加录制下来，供 python -m aigen mutate 检查断言区分力。"""
    if not os.environ.get("AIGEN_RECORD"):
        return
    try:
        body = resp.json()
    except ValueError:
        body = None
    _write({"kind": kind, "method": resp.request.method, "status": resp.status_code, "body": body})


def remember(name: str, value: Any) -> Any:
    """let 算出的值录制下来（mutate 离线重放断言时要用），原样返回。"""
    _write({"kind": "var", "name": name, "value": value})
    return value


class Client:
    """requests.Session + 基础地址 + 统一超时 + 固定请求头；role 不为空时先按 aigen.yaml 登录。
    特殊鉴权继承本类改写 login / request，并在 aigen.yaml 的 api.client_class 登记。"""

    def __init__(self, role: str | None = None):
        api = profile().api
        self.base = str(const(api.base_url)).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({k: fill(v) for k, v in api.headers.items()})
        self.role, self.expires_at = role, None
        if role:
            self.login(role)

    def request(self, method: str, path: str, _kind: str = "step", **kw) -> requests.Response:
        # OAuth2 token 快过期时先重新取（不在 401 时重试，否则会掩盖「token 失效应返回 401」这类用例）
        if _kind != "login" and self.expires_at and time.time() > self.expires_at - 30:
            self.login(self.role)
        url = path if path.startswith("http") else self.base + path
        resp = self.session.request(method, url, timeout=profile().api.timeout, **kw)
        _record(resp, _kind)
        return resp

    def login(self, role: str) -> None:
        if profile().api.oauth2:
            return self._oauth2(role)
        lg = profile().api.login
        if lg is None:
            raise ValueError("aigen.yaml 没有配置 api.login 或 api.oauth2，不能用 precondition / cleanup_as")
        user, pwd = (const(n) for n in profile().api.roles[role])
        payload = {lg.user_field: user, lg.password_field: pwd}
        kw = {"json": payload} if lg.body == "json" else {"data": payload}
        r = self.request(lg.method, lg.path, _kind="login", **kw)
        assert r.status_code == 200, f"前置登录失败 {role}: {r.status_code} {r.text[:200]}"
        if lg.token_field:
            self.session.headers[lg.header] = lg.prefix + str(dig(r.json(), lg.token_field))

    def _oauth2(self, role: str) -> None:
        oa = profile().api.oauth2
        first, second = (const(n) for n in profile().api.roles[role])
        data = {"grant_type": oa.grant}
        if oa.grant == "client_credentials":
            data |= {"client_id": first, "client_secret": second}
        else:
            data |= {"username": first, "password": second}
            data |= {k: const(v) for k, v in (("client_id", oa.client_id), ("client_secret", oa.client_secret)) if v}
        if oa.scope:
            data["scope"] = oa.scope
        r = self.request("POST", oa.token_url, _kind="login", data=data)
        assert r.status_code == 200, f"OAuth2 取 token 失败 {role}: {r.status_code} {r.text[:200]}"
        tok = r.json()
        self.session.headers["Authorization"] = f"{tok.get('token_type', 'Bearer').capitalize()} {tok['access_token']}"
        self.expires_at = time.time() + float(tok["expires_in"]) if tok.get("expires_in") else None


def client(role: str | None = None) -> Client:
    """生成代码统一用它拿客户端；aigen.yaml 配了 client_class 就用自定义类。"""
    path = profile().api.client_class
    if not path:
        return Client(role)
    mod, _, cls = path.partition(":")
    return getattr(importlib.import_module(mod), cls)(role)


try:
    import allure

    title = allure.title
except ImportError:  # 不用 Allure 的项目也能跑
    def title(text: str):
        return lambda f: f


class gen:
    """YAML 里 ${today:...} / ${unique} / ${len:N} 对应的生成函数。"""

    @staticmethod
    def today(offset: str = "+0d", base: date | None = None) -> str:
        """'-18y+1d' → 今天往前 18 年再往后 1 天，ISO 日期；月末/闰日自动取当月最后一天。"""
        d = base or date.today()
        for sign, n, unit in re.findall(r"([+-])(\d+)([ymd])", offset):
            n = int(n) if sign == "+" else -int(n)
            if unit == "d":
                d += timedelta(days=n)
            else:
                months = d.year * 12 + d.month - 1 + (n * 12 if unit == "y" else n)
                y, m = divmod(months, 12)
                d = d.replace(year=y, month=m + 1, day=min(d.day, calendar.monthrange(y, m + 1)[1]))
        return d.isoformat()

    @staticmethod
    def unique() -> str:
        return uuid.uuid4().hex[:8]

    @staticmethod
    def text(n: int) -> str:
        return ("0123456789" * (n // 10 + 1))[:n]


@contextmanager
def on_fail(resp: requests.Response):
    """body 断言 / 取值失败时，把状态码和响应片段带进报错（否则只看到 KeyError）。"""
    try:
        yield
    except (AssertionError, KeyError, IndexError, TypeError, ValueError) as e:
        raise AssertionError(f"{type(e).__name__}: {e} | status={resp.status_code} | body={resp.text[:300]}") from e
