"""跨平台 / 跨 Agent 安装或打印 ai-testing 工作流技能（SKILL.md 唯一母版）。"""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

CURSOR_RULE = """\
---
description: AI 测试工作流——测试类任务执行 ai-testing 技能
alwaysApply: true
---

收到软件测试相关任务（提示词、用例、测试数据、pytest 排错与审查、断言、可重复性、UI 自动化）时：先读并执行技能 `ai-testing`。路由、步骤、约束一律以技能为准，本文件不重复维护。
"""

AGENTS_MD = """\
# 测试项目 Agent 说明（caseflow v3）

收到软件测试相关任务时，请先阅读当前项目的 `PROJECT_MAP.md`（若存在），并严格遵守已安装的 `ai-testing` 技能工作流（可用 `caseflow skill --print` 查看全文）。

常用命令：
- `caseflow init`：初始化项目骨架
- `caseflow auth <登录URL>`：手动过验证码并保存 `.auth/user.json`
- `caseflow dom <URL> -i`：交互式抓取瘦身 DOM（省 Token 写 Page Object）
- `caseflow export`：反向扫描代码与 YAML 导出 `reports/用例表.csv`
- `caseflow mutate tests/api`：接口断言变异防假绿检查
"""


def read_skill() -> str:
    return files("caseflow").joinpath("SKILL.md").read_text(encoding="utf-8")


def install_skill(cursor: bool = False, agents: bool = False, print_only: bool = False,
                  target: str = ".", home: Path | None = None) -> int:
    """安装 ai-testing 技能到通用全局目录，或按需写入当前项目的 Cursor / AGENTS.md 入口。"""
    text = read_skill()
    if print_only:
        print(text)
        return 0

    base_home = home or Path.home()
    for rel in (Path(".agents/skills/ai-testing/SKILL.md"), Path(".claude/skills/ai-testing/SKILL.md")):
        dest = base_home / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        print(f"[OK] Skill -> {dest}")

    proj = Path(target).resolve()
    if cursor:
        rule = proj / ".cursor" / "rules" / "ai-testing-workflow.mdc"
        rule.parent.mkdir(parents=True, exist_ok=True)
        rule.write_text(CURSOR_RULE, encoding="utf-8")
        print(f"[OK] Cursor Rule -> {rule}")

    if agents:
        agents_file = proj / "AGENTS.md"
        if agents_file.exists():
            print(f"[SKIP] AGENTS.md 已存在（未覆盖）：{agents_file}")
        else:
            agents_file.parent.mkdir(parents=True, exist_ok=True)
            agents_file.write_text(AGENTS_MD, encoding="utf-8")
            print(f"[OK] AGENTS.md -> {agents_file}")

    return 0
