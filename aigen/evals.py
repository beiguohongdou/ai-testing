"""闸评测：拿历史上确认过的坏样本 / 干净样本考闸，看它还拦不拦得住、会不会乱报。

    python -m aigen eval                       # 程序闸（validate / mutate）；结果确定，check 会带上
    python -m aigen eval --ai                  # AI 审核员：调模型审每个 review 样本并打分（模型配置同 draft）
    python -m aigen eval --ai --dry-run        # 只生成审查提示词到 reports/eval/prompts/，不调模型（交给任意 Agent 审）
    python -m aigen eval --ai --answers 目录    # 不调模型：读目录里的 <样本id>.json 答案打分
    --dir 目录                                  # 样本目录，默认项目根 evals/

样本：<样本目录>/<样本id>/sample.yaml，格式见 aigen/examples/evals/。
  kind: bad   = 闸应该拦住的真实问题（由懂这块的人确认过问题在哪）
  kind: clean = 已确认没问题的用例，闸不应报「严重」问题
AI 评测分数追加到 docs/闸评测.md；换模型、改 review_rules.md 或 REVIEW.md 后重跑，对比上一行。
"""
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError, model_validator

from aigen.ir import Strict, load
from aigen.mutate import checkable, passes, replay_extract, survivors
from aigen.runtime import env, root

RULES = Path(__file__).with_name("review_rules.md")


class Want(Strict):
    where: str = Field(min_length=1)
    cls: str = Field(alias="class", min_length=1)


class Sample(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["bad", "clean"]
    gate: Literal["review", "validate", "mutate"]
    target: str
    materials: list[str] = []
    record: str | None = None  # mutate：{case_id: [每一步的 {status, body}]}
    opposite: str | None = None  # mutate：同一接口相反结果的真实响应 [{status, body}]
    source: str = Field(min_length=1)
    confirmed_by: str = Field(min_length=1)
    confirmed_at: date
    expect: list[Want] = []
    note: str = ""

    @model_validator(mode="after")
    def _check(self):
        if self.kind == "bad" and self.gate == "review" and not self.expect:
            raise ValueError("review 坏样本必须写 expect：问题在哪（where）、属于哪类（class）")
        if self.gate == "mutate" and not self.record:
            raise ValueError("mutate 样本必须给 record（真实响应）")
        return self


def load_samples(base: Path) -> tuple[list[tuple[Sample, Path]], list[str]]:
    samples, errors = [], []
    for p in sorted(base.glob("*/sample.yaml")):
        try:
            s = Sample.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        except (ValidationError, yaml.YAMLError) as e:
            errors.append(f"{p.parent.name}: 样本格式不对：{e}")
            continue
        missing = [f for f in [s.target, *s.materials, s.record, s.opposite] if f and not (p.parent / f).exists()]
        if missing:
            errors.append(f"{s.id}: 文件不存在 {missing}")
            continue
        samples.append((s, p.parent))
    return samples, errors


# ---------- 程序闸 ----------

def machine_gate(s: Sample, d: Path) -> tuple[bool, str]:
    """返回（闸有没有报问题, 说明）。"""
    if s.gate == "validate":
        try:
            load(d / s.target)
        except ValidationError as e:
            return True, e.errors()[0]["msg"]
        except (ValueError, yaml.YAMLError) as e:
            return True, str(e).splitlines()[0]
        return False, "校验通过"
    records = json.loads((d / s.record).read_text(encoding="utf-8"))
    opposite = [(f"相反结果 {r['status']}", r["body"]) for r in
                (json.loads((d / s.opposite).read_text(encoding="utf-8")) if s.opposite else [])]
    weak = []
    for c in load(d / s.target).cases:
        variables = {}
        for i, (step, resp) in enumerate(zip(c.steps, records.get(c.case_id, []))):
            if step.body_type != "json":
                continue
            lines, _ = checkable(step)
            if lines and passes(lines, resp["body"], variables):
                weak += [f"{c.case_id} 步骤{i + 1} 在「{a}」上仍通过" for a in survivors(step, resp["body"], variables, opposite)[0]]
            variables = replay_extract(step, resp["body"], variables)
    return bool(weak), "；".join(weak[:3]) or "断言都能区分成功与失败"


def run_machine(samples) -> int:
    caught = missed = flagged = clean = 0
    for s, d in samples:
        hit, detail = machine_gate(s, d)
        if s.kind == "bad":
            caught, missed = caught + hit, missed + (not hit)
            print(f"[{'CAUGHT' if hit else 'MISS'}] {s.id}（{s.gate}）：{detail}")
        else:
            clean, flagged = clean + 1, flagged + hit
            print(f"[{'FALSE' if hit else 'OK'}] {s.id}（{s.gate}）：{detail}")
    print(f"eval: 坏样本拦住 {caught}/{caught + missed}，干净样本误报 {flagged}/{clean}")
    return 1 if missed or flagged else 0


# ---------- AI 审核员 ----------

def reviewer_rules() -> str:
    text = RULES.read_text(encoding="utf-8")
    extra = root() / "REVIEW.md"
    if extra.exists():
        text += "\n\n# 本项目 REVIEW.md（与上文冲突时以此为准）\n\n" + extra.read_text(encoding="utf-8")
    return text


def numbered(p: Path) -> str:
    return "\n".join(f"{i:>4}| {ln}" for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1))


def messages(s: Sample, d: Path) -> list[dict]:
    from aigen.draft import redact

    mats = "\n\n".join(f"## 材料 {m}\n{(d / m).read_text(encoding='utf-8')}" for m in s.materials) or "（没有提供材料）"
    user = f"{mats}\n\n## 被审对象 {Path(s.target).name}（左侧是行号）\n{numbered(d / s.target)}"
    return [{"role": "system", "content": reviewer_rules()}, {"role": "user", "content": redact(user)}]


def parse(text: str) -> list[dict]:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise ValueError("答案不是 review_rules.md 规定的 JSON")
    return [f for f in data["findings"] if isinstance(f, dict)]


def score(s: Sample, findings: list[dict]) -> tuple[list[Want], list[dict]]:
    """坏样本：返回没被发现的预期问题；干净样本：返回误报（严重级别的发现）。"""
    if s.kind == "bad":
        return [w for w in s.expect if not any(
            f.get("class") == w.cls and w.where in str(f.get("where", "")) for f in findings)], []
    return [], [f for f in findings if f.get("severity") == "严重"]


def record_score(source: str, found: int, expected: int, false_samples: int, clean: int, errors: int) -> Path:
    p = root() / "docs" / "闸评测.md"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# 闸评测记录\n\n由 `python -m aigen eval --ai` 追加。换模型、改 review_rules.md 或 REVIEW.md 后重跑，对比上一行。\n\n"
                     "| 日期 | 模型 / 答案来源 | 审查规则版本 | 坏样本问题发现 | 干净样本误报 | 答案格式错误 |\n"
                     "|------|------|------|------|------|------|\n", encoding="utf-8")
    version = hashlib.sha256(reviewer_rules().encode("utf-8")).hexdigest()[:8]
    with p.open("a", encoding="utf-8") as f:
        f.write(f"| {date.today()} | {source} | {version} | {found}/{expected} | {false_samples}/{clean} | {errors} |\n")
    return p


def run_ai(samples, dry_run: bool, answers: str | None) -> int:
    out = root() / "reports" / "eval"
    if dry_run:
        (out / "prompts").mkdir(parents=True, exist_ok=True)
        for s, d in samples:
            msgs = messages(s, d)
            (out / "prompts" / f"{s.id}.md").write_text(
                f"<!-- 系统提示词 -->\n{msgs[0]['content']}\n\n<!-- 用户消息 -->\n{msgs[1]['content']}\n", encoding="utf-8")
        print(f"已生成 {len(samples)} 份审查提示词：{out / 'prompts'}")
        print("每份交给一个新会话审，把回答的 JSON 存成 <样本id>.json 放进同一个目录，再运行 python -m aigen eval --ai --answers 该目录")
        return 0

    from aigen.draft import chat

    found = expected = false_samples = clean = errors = 0
    for s, d in samples:
        if answers:
            p = Path(answers) / f"{s.id}.json"
            if not p.exists():
                errors += 1
                print(f"[ERROR] {s.id}：缺答案文件 {p}")
                continue
            text = p.read_text(encoding="utf-8")
        else:
            text = chat(messages(s, d))
            (out / "answers").mkdir(parents=True, exist_ok=True)
            (out / "answers" / f"{s.id}.json").write_text(text, encoding="utf-8")
        try:
            findings = parse(text)
        except ValueError as e:
            errors += 1
            print(f"[ERROR] {s.id}：{e}")
            continue
        missed, false = score(s, findings)
        if s.kind == "bad":
            found, expected = found + len(s.expect) - len(missed), expected + len(s.expect)
            tag = "MISS" if missed else "CAUGHT"
            print(f"[{tag}] {s.id}：发现 {len(s.expect) - len(missed)}/{len(s.expect)}"
                  + "".join(f"；漏了 {w.where}（{w.cls}）" for w in missed))
        else:
            clean, false_samples = clean + 1, false_samples + bool(false)
            print(f"[{'FALSE' if false else 'OK'}] {s.id}：" + ("；".join(f"{f.get('where')}（{f.get('class')}）" for f in false) or "没有误报"))
    source = f"answers:{Path(answers).name}" if answers else env("AIGEN_LLM_MODEL") or "未知模型"
    # ponytail: 每个样本只问一次，模型有随机性时分数会抖；要稳定就多跑几次看趋势，或加 --repeat 取平均
    p = record_score(source, found, expected, false_samples, clean, errors)
    print(f"eval --ai: 坏样本问题发现 {found}/{expected}，干净样本误报 {false_samples}/{clean}，答案格式错误 {errors}；已记录到 {p}")
    return 1 if errors else 0


def main(argv: list[str]) -> int:
    def opt(name: str) -> str | None:
        return argv[argv.index(name) + 1] if name in argv[:-1] else None

    base = Path(opt("--dir")) if opt("--dir") else root() / "evals"
    samples, errors = load_samples(base)
    for e in errors:
        print(f"[FAIL] {e}")
    if errors:
        return 1
    ai = "--ai" in argv
    picked = [(s, d) for s, d in samples if (s.gate == "review") == ai]
    if not picked:
        print(f"{base} 下没有{'review' if ai else 'validate / mutate'} 样本；格式见 aigen/examples/evals/")
        return 0
    return run_ai(picked, "--dry-run" in argv, opt("--answers")) if ai else run_machine(picked)
