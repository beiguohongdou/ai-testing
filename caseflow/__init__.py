"""caseflow：个人测试提效框架（需求 / 接口文档 → 测试用例 → 接口 + UI 自动化）。"""
from caseflow.config import ConfigError, env, setting
from caseflow.data import Case, load_cases, read_cases, run_case
from caseflow.runtime import Client, Resp, client, diff_response, dig, fill, gen, on_fail, to_curl

__version__ = "3.0.0"
__all__ = ["Case", "Client", "ConfigError", "Resp", "client", "diff_response", "dig", "env", "fill", "gen",
           "load_cases", "on_fail", "read_cases", "run_case", "setting", "to_curl"]
