"""python -m aigen <命令>

  validate [文件...]   校验用例 YAML（结构、出处、变量、身份）
  render   [文件...]   YAML → pytest 代码
  trace                用例与代码是否一致 + PRD 每条验收是否都有用例
  export               导出测试用例文档（用例表.md / 用例表.csv / 验收覆盖.md）
  mutate   [录制文件]   断言区分力检查（不给文件就先录制运行一遍接口用例）
  draft    <module> [--ac AC-01,AC-02] [--force]   调用大模型批量起草用例（环境变量 AIGEN_LLM_*）
  selftest             框架自检（不依赖任何项目）
  check                validate + trace + selftest + 接口用例运行 + mutate，CI 用（任一失败或 [WEAK] 即非 0）
"""
import subprocess
import sys
from pathlib import Path


def selftest() -> int:
    test = Path(__file__).parent / "test_aigen.py"
    return subprocess.run([sys.executable, "-m", "pytest", str(test), "-q", "-p", "no:cacheprovider", "-o", "addopts="]).returncode


def main(argv: list[str]) -> int:
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    files = [Path(a) for a in args] or None
    if cmd == "validate":
        from aigen.ir import validate
        return validate(files)
    if cmd == "render":
        from aigen.render import main as render
        return render(files)
    if cmd == "trace":
        from aigen.ir import trace
        return trace()
    if cmd == "export":
        from aigen.export import main as export
        return export()
    if cmd == "mutate":
        from aigen.mutate import main as mutate
        return mutate(args[0] if args else None)
    if cmd == "draft":
        from aigen.draft import main as draft
        return draft(args)
    if cmd == "selftest":
        return selftest()
    if cmd == "check":
        from aigen.ir import gen_root, trace, validate
        from aigen.mutate import main as mutate
        api = (gen_root() / "api_gen").is_dir()
        return max(validate(), trace(), selftest(), mutate() if api else 0)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
