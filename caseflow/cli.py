"""命令行：caseflow <命令>（也可 python -m caseflow <命令>）"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="caseflow", description="个人测试提效：文档 → 用例 → 接口 / UI 自动化")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="生成测试项目骨架（已存在的文件不覆盖）")
    p.add_argument("target", nargs="?", default=".")

    p = sub.add_parser("auth", help="打开浏览器手动登录一次（含验证码），保存登录态供 UI 用例复用")
    p.add_argument("url")
    p.add_argument("--name", default="user", help="登录态名字，保存到 .auth/<name>.json")

    p = sub.add_parser("dom", help="输出页面交互元素骨架（瘦身 DOM），贴给 AI 写 Page Object")
    p.add_argument("url", nargs="?")
    p.add_argument("--file", help="离线 HTML 文件（不访问网络）")
    p.add_argument("--auth", help="登录态文件，默认 .auth/user.json（存在时）")
    p.add_argument("--selector", default="body", help="只看页面的某一块，如 '#main'")
    p.add_argument("--wait", help="等这个元素出现后再抓，如 'table'")
    p.add_argument("-i", "--interactive", action="store_true", help="交互式连抓多页：手动操作到目标业务状态后回车抓取")
    p.add_argument("-o", "--out", help="输出文件（-i 模式下为输出目录，默认 reports/dom）")

    sub.add_parser("export", help="扫描 data/ 和 tests/ 导出 reports/用例表.csv / .md")

    p = sub.add_parser("mutate", help="把响应改坏再跑接口用例，找出断言太弱（假绿）的用例")
    p.add_argument("targets", nargs="*")
    p.add_argument("--observe", action="store_true", help="观察模式：仅输出弱断言报告，不返回非零退出码")
    p.add_argument("--changed", action="store_true", help="仅检查 git 变更涉及的接口测试与 YAML 关联文件")

    p = sub.add_parser("guard", help="检查 git diff，拦截擅自删改断言或私加 skip/xfail 的行为")
    p.add_argument("--base", default="HEAD", help="对比基准（默认 HEAD）")
    p.add_argument("--allow", action="store_true", help="人工确认后放行预期变更（仍输出清单）")

    p = sub.add_parser("skill", help="安装或打印 AI 测试工作流技能（跨平台 / 跨 Agent）")
    p.add_argument("target", nargs="?", default=".", help="配合 --cursor / --agents 使用的目标项目目录")
    p.add_argument("--cursor", action="store_true", help="同时在目标项目生成 .cursor/rules/ai-testing-workflow.mdc")
    p.add_argument("--agents", action="store_true", help="同时在目标项目生成通用 AGENTS.md 入口（不覆盖已有文件）")
    p.add_argument("--print", dest="print_only", action="store_true", help="仅输出规则全文到终端（适合网页版大模型复制，不写文件）")

    sub.add_parser("selftest", help="框架自检（离线）")

    a = ap.parse_args(argv)
    if a.cmd == "init":
        from caseflow.scaffold import init
        return init(a.target)
    if a.cmd == "auth":
        from caseflow.ui import capture_auth
        print(f"[OK] 登录态已保存：{capture_auth(a.url, a.name)}")
        return 0
    if a.cmd == "dom":
        if a.interactive:
            if not a.url:
                ap.error("dom -i 需要起始 url")
            from caseflow.ui import capture_dom_interactive
            saved = capture_dom_interactive(a.url, a.auth, a.selector, a.out or "reports/dom")
            print(f"[OK] 共保存 {len(saved)} 个页面 DOM")
            return 0
        if not (a.url or a.file):
            ap.error("dom 需要 url 或 --file")
        from caseflow.ui import extract_dom
        text = extract_dom(a.url, a.auth, a.selector, a.file, a.wait)
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
            print(f"[OK] 已写到 {a.out}（约 {len(text) // 2} token）")
        else:
            print(text)
        return 0
    if a.cmd == "export":
        from caseflow.export import main as export
        return export()
    if a.cmd == "mutate":
        from caseflow.mutate import main as mutate
        return mutate(a.targets, observe=a.observe, changed=a.changed)
    if a.cmd == "guard":
        from caseflow.guard import main as guard
        return guard(base=a.base, allow=a.allow)
    if a.cmd == "skill":
        from caseflow.skill import install_skill
        return install_skill(cursor=a.cursor, agents=a.agents, print_only=a.print_only, target=a.target)
    if a.cmd == "selftest":
        tests = Path(__file__).parent / "tests"
        return subprocess.run([sys.executable, "-m", "pytest", str(tests), "-q", "-p", "no:cacheprovider",
                               "-o", "addopts=", "--rootdir", str(tests)]).returncode
    return 2


if __name__ == "__main__":
    sys.exit(main())
