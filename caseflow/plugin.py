"""pytest 插件：在项目 conftest.py 写 `pytest_plugins = ["caseflow.plugin"]` 启用。

提供：
- fixture `api`：匿名客户端；`api_as("admin")`：按角色取客户端（同一角色只登录一次）
- UI 用例（用了 Playwright 的 page fixture）失败时，自动把截图、前端报错、失败的接口请求写到 reports/ui/
"""
from __future__ import annotations

import re

import pytest

from caseflow.config import project_root
from caseflow.runtime import client


@pytest.fixture
def api():
    return client(None)


@pytest.fixture
def api_as():
    return client


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"_caseflow_rep_{rep.when}", rep)


def _dedup(items: list[str], limit: int = 30, max_len: int = 300) -> list[str]:
    counts: dict[str, int] = {}
    for raw in items:
        s = raw if len(raw) <= max_len else raw[:max_len] + "…"
        counts[s] = counts.get(s, 0) + 1
    out = [f"(×{n}) {text}" if n > 1 else text for text, n in counts.items()]
    return out[:limit]


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text)


def attach_ui_diagnostics(request, page, tag: str = ""):
    """为指定 Playwright page 绑定失败诊断采集，返回应在关闭页面前调用的 flush() 回调。"""
    console, failed = [], []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: console.append(f"[pageerror] {e}"))
    page.on("response", lambda r: failed.append(f"{r.status} {r.request.method} {r.url}") if r.status >= 400 else None)
    flushed = False

    def flush() -> None:
        nonlocal flushed
        if flushed:
            return
        rep = getattr(request.node, "_caseflow_rep_call", None)
        if rep is None or not rep.failed:
            return
        flushed = True
        out = project_root() / "reports" / "ui"
        out.mkdir(parents=True, exist_ok=True)
        suffix = f"_{_safe_name(tag)}" if tag else ""
        name = _safe_name(request.node.nodeid)[-140:] + suffix
        shot = out / f"{name}.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
        except Exception as e:  # 页面已关闭等
            shot = f"截图失败：{e}"
        lines = [f"# UI 失败诊断：{request.node.nodeid}", "", f"- 页面：{page.url}", f"- 截图：{shot}", "",
                 "## 失败的接口请求（HTTP ≥ 400）", *(f"- {x}" for x in _dedup(failed) or ["（无）"]), "",
                 "## 前端报错", *(f"- {x}" for x in _dedup(console) or ["（无）"]), "",
                 "> 有失败接口 → 多半是后端 / 数据问题；只有前端报错 → 多半是前端问题；都没有 → 查定位器和等待。"]
        (out / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")
        request.node.add_report_section("call", "caseflow", f"诊断已写到 {out / (name + '.md')}")

    return flush


@pytest.fixture(autouse=True)
def _caseflow_ui_diagnostics(request):
    if "page" not in request.fixturenames:
        yield
        return
    flush = attach_ui_diagnostics(request, request.getfixturevalue("page"))
    yield
    flush()
