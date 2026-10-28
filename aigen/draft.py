"""批量起草：调用大模型把 PRD + 契约写成用例 YAML，validate 不过就把错误回灌，最多 3 轮。

    python -m aigen draft <module> [--ac AC-01,AC-02] [--force]

模型配置写在项目根 .env（或系统环境变量，后者优先）；任何兼容 OpenAI chat/completions 接口的服务都行：
    AIGEN_LLM_BASE_URL   如 https://api.deepseek.com
    AIGEN_LLM_API_KEY
    AIGEN_LLM_MODEL      如 deepseek-chat
发送前脱敏：常量（账号、密码、地址等）的真实值替换成 ${常量名}；aigen.yaml 的 redact 规则再替换一遍。
产出写到 cases/<module>.yaml，**仍须人工评审后再 render**；3 轮仍不过写到 reports/ 并返回 1。
"""
import argparse
import re
import time
from pathlib import Path

import requests
import yaml
from pydantic import ValidationError

from aigen.ir import Suite, ac_ids, case_files, load, material_text, validate
from aigen.runtime import const, consts, contract_doc, env, has_const, profile, root

MAX_ROUNDS = 3
RULES = Path(__file__).with_name("draft_rules.md")


class LLMError(RuntimeError):
    pass


def redact(text: str) -> str:
    """常量真实值 → ${常量名}（长的先换，避免短值把长值切开），再套 aigen.yaml 的 redact 规则。"""
    names = {profile().api.base_url} | {n for pair in profile().api.roles.values() for n in pair}
    if consts() is not None:
        names |= {k for k in vars(consts()) if k.isupper()}
    pairs = sorted(((str(const(n)), n) for n in names if has_const(n) and isinstance(const(n), str) and len(str(const(n))) >= 4),
                   key=lambda p: -len(p[0]))
    for value, name in pairs:
        text = text.replace(value, "${" + name + "}")
    for rule in profile().redact:
        text = re.sub(rule["pattern"], rule["replace"], text)
    return text


def contract_summary(name: str) -> str:
    """契约太大不能整份发：每个接口只留方法、路径、摘要、参数、请求体字段、状态码。"""
    lines = []
    for path, ops in (contract_doc(name).get("paths") or {}).items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            params = [p.get("name", p.get("$ref", "").split("/")[-1]) for p in op.get("parameters", [])]
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
            ref = body.get("$ref", "").split("/")[-1]
            codes = sorted(str(k) for k in op.get("responses", {}))
            lines.append(f"{method.upper()} {path} | {op.get('summary', '')} | 参数 {params} | 请求体 {ref or list(body.get('properties', {}))} | responses {codes}")
    return "\n".join(lines)


def materials(acs: list[str]) -> str:
    out = []
    for m in profile().materials:
        if m.kind == "openapi":
            out.append(f"## 材料「{m.name}」（契约摘要；引用格式：{m.name} 方法 路径 responses.状态码）\n{contract_summary(m.name)}")
        elif m.kind == "prd":
            text = material_text(m.name)
            if acs:
                text = "\n".join(ln for ln in text.splitlines() if any(a in ln for a in acs) or ln.startswith("#"))
            out.append(f"## 材料「{m.name}」（需求；引用格式：{m.name} 验收编号）\n{text}")
    return redact("\n\n".join(out))


def example() -> str:
    files = case_files() or sorted(RULES.with_name("examples").glob("api_*.yaml"))
    if not files:
        return ""
    smallest = min(files, key=lambda p: p.stat().st_size)
    return f"## 格式示例（{smallest.name}）\n```yaml\n{smallest.read_text(encoding='utf-8')}\n```"


def chat(messages: list[dict]) -> str:
    base, key, model = (env(k) for k in ("AIGEN_LLM_BASE_URL", "AIGEN_LLM_API_KEY", "AIGEN_LLM_MODEL"))
    if not (base and key and model):
        missing = [k for k, v in zip(("AIGEN_LLM_BASE_URL", "AIGEN_LLM_API_KEY", "AIGEN_LLM_MODEL"), (base, key, model)) if not v]
        raise LLMError(f"没有配置 {missing}：在项目根 .env 里填写，或设成系统环境变量")
    for attempt in range(4):
        try:
            r = requests.post(f"{base.rstrip('/')}/chat/completions", timeout=180,
                              headers={"Authorization": f"Bearer {key}"},
                              json={"model": model, "messages": messages, "temperature": 0})
        except requests.RequestException as e:
            err = f"网络错误 {type(e).__name__}"
        else:
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            if r.status_code in (401, 403):
                raise LLMError(f"模型接口返回 {r.status_code}（密钥无效或无权限），不重试")
            if r.status_code != 429 and r.status_code < 500:
                raise LLMError(f"模型接口返回 {r.status_code}：{r.text[:200]}")
            err = f"模型接口返回 {r.status_code}"
        if attempt < 3:
            time.sleep(2 ** (attempt + 1))  # 429 / 5xx / 网络错误：退避后重试
    raise LLMError(f"{err}，重试 3 次仍失败")


def extract_yaml(text: str) -> str:
    blocks = re.findall(r"```(?:yaml|yml)?\s*\n(.*?)```", text, re.S)
    return blocks[-1] if blocks else text


def check(text: str, module: str) -> list[str]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [f"YAML 语法错误：{e}"]
    if not isinstance(data, dict):
        return ["顶层必须是 {module: ..., cases: [...]}"]
    if data.get("module") != module:
        return [f"module 必须是 {module}"]
    try:
        if data.get("kind") == "ui":
            from aigen.ui import UiSuite
            suite = UiSuite.model_validate(data)
        else:
            suite = Suite.model_validate(data)
    except ValidationError as e:
        return [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()]
    existing = {c.case_id for p in case_files() if p.stem != module for c in load(p).cases}
    return [f"case_id {c.case_id} 和已有用例重复" for c in suite.cases if c.case_id in existing]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m aigen draft")
    ap.add_argument("module")
    ap.add_argument("--ac", default="", help="只起草这些验收，逗号分隔")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 cases/<module>.yaml")
    args = ap.parse_args(argv)
    out = root() / "cases" / f"{args.module}.yaml"
    if out.exists() and not args.force:
        print(f"[FAIL] {out.name} 已存在；确认要覆盖加 --force")
        return 1
    acs = [a.strip() for a in args.ac.split(",") if a.strip()]
    if bad := [a for a in acs if not ac_ids(a)]:
        print(f"[FAIL] 验收编号格式不对：{bad}（aigen.yaml 的 ac_pattern 是 {profile().ac_pattern}）")
        return 1
    scope = f"只覆盖这些验收：{', '.join(acs)}" if acs else "覆盖需求里的全部验收"
    messages = [
        {"role": "system", "content": "你是测试工程师，严格遵守下面的起草规则，只依据给出的材料写用例，不编造。\n\n" + RULES.read_text(encoding="utf-8")},
        {"role": "user", "content": "\n\n".join(filter(None, [
            "## 项目配置 aigen.yaml\n```yaml\n" + redact((root() / "aigen.yaml").read_text(encoding="utf-8")) + "\n```",
            materials(acs),
            example(),
            f"## 任务\n起草 module: {args.module} 的接口用例 YAML，{scope}。不确定的写进 pending。只输出一个 ```yaml 代码块。",
        ]))},
    ]
    try:
        for round_no in range(1, MAX_ROUNDS + 1):
            reply = chat(messages)
            text = extract_yaml(reply)
            errors = check(text, args.module)
            print(f"第 {round_no} 轮：{'通过' if not errors else f'{len(errors)} 个错误'}")
            if not errors:
                out.parent.mkdir(exist_ok=True)
                out.write_text(text, encoding="utf-8")
                validate([out])
                print(f"[OK] 已写入 {out.relative_to(root())}；请人工评审（尤其 pending 和出处）后再 python -m aigen render")
                return 0
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": "validate 没通过，只修正下面这些问题，其他内容不要改，仍只输出一个 ```yaml 代码块：\n"
                                                     + "\n".join(f"- {e}" for e in errors[:30])}]
    except LLMError as e:
        print(f"[FAIL] {e}")
        return 1
    failed = root() / "reports" / f"aigen_draft_failed_{args.module}.yaml"
    failed.parent.mkdir(exist_ok=True)
    failed.write_text(text + "\n# validate 错误：\n" + "\n".join(f"# {e}" for e in errors), encoding="utf-8")
    print(f"[FAIL] {MAX_ROUNDS} 轮仍未通过，草稿和错误写到 {failed.relative_to(root())}，需要人工处理")
    return 1
