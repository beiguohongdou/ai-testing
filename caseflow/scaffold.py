"""caseflow init：在当前（或指定）目录生成测试项目骨架。已存在的文件不覆盖。"""
from __future__ import annotations

from pathlib import Path

CONFIG = """\
# caseflow 项目配置。换公司 / 换项目，主要改这一个文件 + .env
# 任何值都可以写 ${环境变量名}，真实值放 .env（不进 git）

api:
  base_url: ${API_BASE_URL}
  timeout: 15
  headers: {}                       # 每个请求都带的头，如 {X-Tenant: demo}
  # 国内常见「HTTP 一律 200，看 body.code」：配上后可用 expect_ok() / expect_code() / YAML 里 ok、code
  envelope: {code: code, ok: 0, msg: msg, data: data}
  login:                            # 按角色登录；不需要登录可删掉
    method: POST
    path: /api/login
    body: json                      # 表单登录写 form
    user_field: username
    password_field: password
    token_path: data.token          # 响应里 token 的位置；靠 Cookie 保持登录就删掉这行
    header: Authorization
    prefix: "Bearer "
  roles:
    user: {user: "${USER_NAME}", password: "${USER_PASSWORD}"}
    # admin: {user: "${ADMIN_NAME}", password: "${ADMIN_PASSWORD}"}
  # hook: my_hooks:sign             # 请求签名 / 加密：def sign(method, url, kwargs): ...; return kwargs

ui:
  base_url: ${UI_BASE_URL}
  auth_dir: .auth                   # caseflow auth 保存的登录态
  # channel: chrome                 # 用本机 Chrome；不填用 Playwright 自带 Chromium

safety:
  block_host_words: [prod, production, live, online]   # 主机名含这些词时拒绝发写请求
"""

ENV_EXAMPLE = """\
# 复制为 .env 后填写（.env 不进 git）
API_BASE_URL=http://localhost:8000
UI_BASE_URL=http://localhost:8080
USER_NAME=
USER_PASSWORD=
"""

GITIGNORE = """\
.env
.auth/
reports/
test-results/
__pycache__/
.pytest_cache/
*.pyc
"""

CONFTEST = '''\
import pytest

from caseflow.config import setting
from caseflow.ui import auth_path

pytest_plugins = ["caseflow.plugin"]


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """默认只带 base_url（保持干净未登录态，避免影响登录 / 退出用例）。"""
    if base := setting("ui", "base_url"):
        return {**browser_context_args, "base_url": base}
    return browser_context_args


@pytest.fixture
def page_as(browser, browser_context_args):
    """按角色取已登录页面（读取 caseflow auth 保存的 .auth/<role>.json），与干净 page 隔离：

        def test_order(page_as):
            page = page_as("user")
    """
    contexts = []

    def _open(role: str = "user"):
        state = auth_path(role)
        kw = {**browser_context_args, **({"storage_state": str(state)} if state.is_file() else {})}
        ctx = browser.new_context(**kw)
        contexts.append(ctx)
        return ctx.new_page()

    yield _open
    for ctx in contexts:
        ctx.close()
'''

PYTEST_INI = """\
[pytest]
testpaths = tests
addopts = -ra --import-mode=importlib
# 装了 pytest-playwright 后可改用下面这行（UI 失败自动截图 + 录 Trace，用 playwright show-trace 回放；本机 Chrome 加 --browser-channel chrome）：
# addopts = -ra --import-mode=importlib --screenshot only-on-failure --tracing retain-on-failure --output reports/playwright
markers =
    smoke: 冒烟，几分钟内跑完，先排除环境问题
    core: 高风险核心用例
# 用例 id 显示中文
disable_test_id_escaping_and_forfeit_all_rights_to_community_support = True
"""

PROJECT_MAP = """\
# PROJECT_MAP（给 AI 看的项目地图，每加一个模块更新一次）

> 新开 AI 会话时 @ 本文件 + 当前需求，避免 AI 重复造轮子、写断上下游依赖。

## 被测系统
- 名称 / 地址：待填
- 接口风格：HTTP 200 + body.code？（见 caseflow.yaml api.envelope）
- 登录方式：待填（有验证码 → `caseflow auth` 手动存登录态）

## 业务模块划分
| 模块 | 职责 | 页面 URL | 接口前缀 | 优先级 |
|------|------|----------|----------|--------|

## 模块上下游依赖矩阵
| 下游模块 | 依赖上游 | 需要的状态 / 数据契约 | 复用方式（登录态 / 接口造数 fixture / UI 前置） |
|----------|----------|----------------------|------------------------------------------------|

## 公共前置 Fixtures（conftest.py）
| fixture 名 | 产出 | 供哪些模块用 |
|------------|------|--------------|

## 已有 Page Object（pages/）
| 类 | 文件 | 页面 | 主要方法 |
|----|------|------|----------|

## 已有数据文件（data/）与用例
| 文件 / 测试 | 覆盖什么 | 状态 |
|-------------|----------|------|

## 约定
- 单接口多组数据 → data/*.yaml + run_case；多步业务流 / UI → tests/ 下原生 pytest
- 测试函数 docstring 第一行写「编号 标题」，caseflow export 据此出用例表
"""

FILES = {
    "caseflow.yaml": CONFIG,
    ".env.example": ENV_EXAMPLE,
    ".gitignore": GITIGNORE,
    "conftest.py": CONFTEST,
    "pytest.ini": PYTEST_INI,
    "PROJECT_MAP.md": PROJECT_MAP,
    "pages/__init__.py": "",
    "data/.gitkeep": "",
    "docs/.gitkeep": "",
    "tests/__init__.py": "",
    "tests/api/__init__.py": "",
    "tests/ui/__init__.py": "",
}


def init(target: str = ".") -> int:
    root = Path(target).resolve()
    for rel, content in FILES.items():
        p = root / rel
        if p.exists():
            print(f"[SKIP] {rel} 已存在")
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        print(f"[OK]   {rel}")
    print(f"\n下一步：复制 .env.example 为 .env 填地址和账号；改 caseflow.yaml 的登录方式；然后 pytest")
    return 0
