"""单接口数据驱动：YAML 只放数据，执行逻辑固定在 run_case 里（改完 YAML 直接跑 pytest，不需要生成代码）。

data/items_create.yaml:

    api: POST /items              # 本文件所有用例默认的请求
    role: admin                   # 默认身份（caseflow.yaml 的 api.roles）；不写 = 匿名
    cases:
      - id: TC-ITEM-01
        title: 正常创建
        vars: {name: "item_${unique}"}        # 本条用例内共用的值
        json: {name: "${name}", price: 10}
        expect:
          ok: true                            # 业务成功（HTTP<400 且 envelope code=ok）
          body:
            data.name: "${name}"              # 简写 = 等于
            data.id: {exists: true}
      - id: TC-ITEM-02
        title: 名称为空
        json: {name: "", price: 10}
        expect: {code: 10001, body: {msg: {contains: 名称}}}
        xfail: 文档没写错误码，待实测校准       # 先跑起来看真实返回，确认后删掉这一行

tests/api/test_items.py:

    @pytest.mark.parametrize("case", load_cases("data/items_create.yaml"))
    def test_items_create(case):
        run_case(case)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

from caseflow.config import config, env, project_root
from caseflow.runtime import OPS, MISSING, Resp, check, client, dig, fill

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
CASE_KEYS = {"id", "title", "api", "role", "vars", "headers", "params", "json", "form", "drop", "expect",
             "xfail", "xfail_strict", "skip", "note", "source", "no_body_reason"}
EXPECT_KEYS = {"status", "ok", "code", "body"}
FILE_KEYS = {"api", "role", "headers", "base_json", "cases"}


@dataclass
class Case:
    id: str
    title: str
    method: str
    path: str
    role: str | None = None
    vars: dict = field(default_factory=dict)
    headers: dict | None = None
    params: dict | None = None
    json: Any = None
    form: dict | None = None
    expect: dict = field(default_factory=dict)
    note: str = ""
    source: str = ""
    file: str = ""

    def __str__(self) -> str:
        return f"{self.id} {self.title}"


_SECRET_KEYS = {"password", "pwd", "passwd", "secret", "token", "access_token", "refresh_token", "api_key"}


def _credential_values() -> list[str]:
    """api.roles 里配置的密码/密钥真实值（用来拦截 YAML 里的明文）。"""
    out = []
    for cred in (config()["api"].get("roles") or {}).values():
        for k, raw in (cred or {}).items():
            if str(k).lower() not in _SECRET_KEYS:
                continue
            name = str(raw).strip("${}") if str(raw).startswith("${") else None
            value = env(name) if name else str(raw)
            if value and len(value) >= 4:
                out.append(value)
    return out


def _strings(v: Any):
    if isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, str):
        yield v


def _validate(raw: dict, defaults: dict, where: str, secrets: list[str]) -> Case:
    errs = []
    unknown = set(raw) - CASE_KEYS
    if unknown:
        errs.append(f"不认识的字段 {sorted(unknown)}，可用：{sorted(CASE_KEYS)}")
    cid, title = raw.get("id"), raw.get("title")
    if not cid or not title:
        errs.append("必须有 id 和 title")
    api = raw.get("api", defaults.get("api"))
    method, _, path = str(api or "").strip().partition(" ")
    if method.upper() not in METHODS or not path.strip().startswith(("/", "http")):
        errs.append(f"api 须形如「POST /items」，实际 {api!r}")
    expect = raw.get("expect") or {}
    if not isinstance(expect, dict) or not expect:
        errs.append("必须有 expect")
    else:
        if bad := set(expect) - EXPECT_KEYS:
            errs.append(f"expect 不认识 {sorted(bad)}，可用：{sorted(EXPECT_KEYS)}")
        if not ({"ok", "code", "body"} & set(expect)) and not raw.get("no_body_reason"):
            errs.append("只断言 status 有假绿风险：补 ok / code / body，或写 no_body_reason 说明为什么不用")
        for path_, ops in (expect.get("body") or {}).items():
            if isinstance(ops, dict) and (bad := set(ops) - set(OPS)):
                errs.append(f"expect.body.{path_} 不认识 {sorted(bad)}，可用：{list(OPS)}")
    if raw.get("xfail") and raw.get("xfail_strict"):
        errs.append("xfail 与 xfail_strict 二选一")
    drop = raw.get("drop")
    if drop is not None and not (isinstance(drop, list) and all(isinstance(x, str) for x in drop)):
        errs.append("drop 必须是字段名列表")
        drop = None
    base_json = defaults.get("base_json")
    raw_json = raw.get("json")
    if raw.get("form") is None:
        if base_json is not None and (raw_json is None or isinstance(raw_json, dict)):
            json_val: Any = {**base_json, **(raw_json or {})}
        else:
            json_val = dict(raw_json) if isinstance(raw_json, dict) else raw_json
        if drop:
            if isinstance(json_val, dict):
                for k in drop:
                    json_val.pop(k, None)
            else:
                errs.append("drop 需要字典类型的 json 或 base_json")
    else:
        json_val = raw_json
        if drop:
            errs.append("drop 只能配合 json / base_json 使用")
    for s in _strings({"headers": raw.get("headers"), "params": raw.get("params"), "json": json_val,
                       "form": raw.get("form"), "vars": raw.get("vars")}):
        if any(secret in s for secret in secrets):
            errs.append("出现账号/密码明文，改用 ${环境变量名} 引用")
            break
    if errs:
        raise ValueError(f"{where}（{cid or '无 id'}）：" + "；".join(errs))
    headers = {**(defaults.get("headers") or {}), **(raw.get("headers") or {})} or None
    return Case(id=cid, title=title, method=method.upper(), path=path.strip(), role=raw.get("role", defaults.get("role")),
                vars=raw.get("vars") or {}, headers=headers, params=raw.get("params"), json=json_val,
                form=raw.get("form"), expect=expect, note=raw.get("note", ""), source=raw.get("source", ""), file=where)


def read_cases(path: str | Path) -> list[tuple[Case, dict]]:
    """读取并校验一个数据文件，返回 [(Case, 原始字典)]。不生成 pytest 参数，export 也用它。"""
    p = Path(path)
    if not p.is_absolute():
        p = project_root() / p
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rel = p.name
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ValueError(f"{rel}：顶层须有 cases 列表")
    if bad := set(data) - FILE_KEYS:
        raise ValueError(f"{rel}：顶层不认识 {sorted(bad)}，可用：{sorted(FILE_KEYS)}")
    secrets = _credential_values()
    if "base_json" in data and data["base_json"] is not None:
        if not isinstance(data["base_json"], dict):
            raise ValueError(f"{rel}：base_json 必须是字典")
        for s in _strings(data["base_json"]):
            if any(secret in s for secret in secrets):
                raise ValueError(f"{rel}：base_json 出现账号/密码明文，改用 ${{环境变量名}} 引用")
    out, seen = [], set()
    for raw in data["cases"]:
        if not isinstance(raw, dict):
            raise ValueError(f"{rel}：cases 里每一项须是字典")
        case = _validate(raw, data, rel, secrets)
        if case.id in seen:
            raise ValueError(f"{rel}：用例 id {case.id} 重复")
        seen.add(case.id)
        out.append((case, raw))
    return out


def load_cases(path: str | Path) -> list:
    """给 @pytest.mark.parametrize 用：每条用例一个 pytest.param，id 为「编号 标题」，xfail / skip 自动加标记。"""
    params = []
    for case, raw in read_cases(path):
        marks = []
        if raw.get("xfail_strict"):
            marks.append(pytest.mark.xfail(reason=str(raw["xfail_strict"]), strict=True))
        elif raw.get("xfail"):
            marks.append(pytest.mark.xfail(reason=str(raw["xfail"]), strict=False))
        if raw.get("skip"):
            marks.append(pytest.mark.skip(reason=str(raw["skip"])))
        params.append(pytest.param(case, id=str(case), marks=marks))
    return params


def run_case(case: Case, api=None) -> Resp:
    """发送请求并按 expect 检查（先 body / 业务码，后 HTTP 状态码）。返回响应，调用方还可以继续断言。"""
    variables = {}
    for k, v in case.vars.items():
        variables[k] = fill(v, variables)
    api = api or client(case.role)
    kw = {k: fill(v, variables) for k, v in (("headers", case.headers), ("params", case.params),
                                               ("json", case.json), ("data", case.form)) if v is not None}
    resp = api.request(case.method, fill(case.path, variables), **kw)
    exp = fill(case.expect, variables)
    errors = []
    for path, ops in (exp.get("body") or {}).items():
        ops = ops if isinstance(ops, dict) else {"eq": ops}
        errors += check(dig(resp.body, path, MISSING), ops, f"body.{path}")
    if "code" in exp and resp.code != exp["code"]:
        errors.append(f"业务码期望 {exp['code']!r}，实际 {resp.code!r}（msg={resp.msg!r}）")
    if exp.get("ok") is True:
        okv = (resp.envelope or {}).get("ok", 0)
        if resp.status_code >= 400 or (resp.envelope and resp.code != okv):
            errors.append(f"期望业务成功，实际 HTTP {resp.status_code}" + (f" code={resp.code!r}" if resp.envelope else ""))
    if "status" in exp and resp.status_code != exp["status"]:
        errors.append(f"HTTP 状态码期望 {exp['status']}，实际 {resp.status_code}")
    if errors:
        resp.fail(f"[{case}] " + "；".join(errors))
    return resp
