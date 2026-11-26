"""Playwright UI 提效工具：半自动保存登录态、DOM 瘦身（喂给 AI 写 Page Object）、登录态注入、失败诊断。

    caseflow auth https://test.example.com/login           # 手动登录一次（验证码也手动过），保存登录态
    caseflow dom  https://test.example.com/orders          # 输出单页交互元素骨架，贴给 AI 写 PO
    caseflow dom  https://test.example.com -i              # 交互式连抓多页（先走完加购等上游操作，再回车抓结算页）
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from caseflow.config import project_root, setting

# 只收集能交互 / 能断言的元素；输出给 AI 的每一行都是一个候选定位器
_COLLECT_JS = r"""
(root) => {
  const sel = 'a,button,input,select,textarea,[role],[data-testid],[data-test],[data-qa],[contenteditable="true"],label,h1,h2,h3,table,form,dialog';
  const out = [];
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const text = (el) => (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 60);
  for (const el of root.querySelectorAll(sel)) {
    if (!visible(el)) continue;
    const tag = el.tagName.toLowerCase();
    const a = {};
    for (const k of ['id','name','type','role','placeholder','aria-label','title','href','data-testid','data-test','data-qa','for']) {
      const v = el.getAttribute(k);
      if (v && !(k === 'id' && /\d{4,}|^[a-f0-9-]{16,}$/.test(v))) a[k] = v.slice(0, 80);
    }
    if (tag === 'select') a.options = [...el.options].slice(0, 8).map(o => o.text.trim()).join('|');
    if (tag === 'table') a.headers = [...el.querySelectorAll('th')].slice(0, 10).map(th => th.innerText.trim()).join('|');
    if (el.disabled) a.disabled = 'true';
    const t = ['table','form'].includes(tag) ? '' : text(el);
    out.push({tag, attrs: a, text: t});
    if (out.length >= 400) break;
  }
  return out;
}
"""


def slim_dom(page, selector: str = "body") -> str:
    """把当前页面压缩成「交互元素清单 + 无障碍树」，通常 1~3k token，足够 AI 写出稳定定位器。"""
    root = page.locator(selector).first
    items = root.evaluate(_COLLECT_JS)
    lines = [f"# 页面：{page.title()}", f"URL：{page.url}", "", "## 交互元素（tag | 属性 | 文本）"]
    for it in items:
        attrs = " ".join(f'{k}="{v}"' for k, v in it["attrs"].items())
        lines.append(f"- {it['tag']} | {attrs} | {it['text']}".rstrip(" |"))
    try:
        aria = root.aria_snapshot()
    except Exception:  # 老版本 Playwright 没有 aria_snapshot
        aria = ""
    if aria:
        lines += ["", "## 无障碍树（get_by_role / get_by_label 的依据）", "```yaml", aria[:8000], "```"]
    lines += ["", "> 定位器优先级：get_by_test_id > get_by_role(name=) > get_by_label > get_by_placeholder > 稳定 id/name；",
              "> 避开动态 id、nth()、样式类名；以上内容由 caseflow dom 生成，元素以真实页面为准。"]
    return "\n".join(lines)


def _launch(p, headless: bool):
    channel = setting("ui", "channel")
    if channel:
        return p.chromium.launch(headless=headless, channel=channel)
    try:
        return p.chromium.launch(headless=headless)
    except Exception as e:
        if "Executable doesn't exist" not in str(e):
            raise
        for fallback in ("chrome", "msedge"):
            try:
                return p.chromium.launch(headless=headless, channel=fallback)
            except Exception:
                continue
        raise e


def auth_path(name: str = "user") -> Path:
    return project_root() / setting("ui", "auth_dir", default=".auth") / f"{name}.json"


def capture_auth(url: str, name: str = "user") -> Path:
    """打开有界面的浏览器，你手动登录（含验证码），回到终端按回车，保存登录态到 .auth/<name>.json。"""
    from playwright.sync_api import sync_playwright

    out = auth_path(name)
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = _launch(p, headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url)
        input("在浏览器里完成登录（验证码也手动过），看到登录后的页面后回到这里按回车…")
        context.storage_state(path=str(out))
        browser.close()
    return out


def extract_dom(url: str | None = None, auth: str | None = None, selector: str = "body",
                html_file: str | None = None, wait: str | None = None) -> str:
    """打开页面（可带登录态）并输出瘦身 DOM。html_file 用于离线页面。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p, headless=True)
        state = auth or (str(auth_path()) if auth_path().is_file() else None)
        context = browser.new_context(**({"storage_state": state} if state and url else {}))
        page = context.new_page()
        if html_file:
            page.set_content(Path(html_file).read_text(encoding="utf-8"))
        else:
            page.goto(url, wait_until="networkidle")
        if wait:
            page.locator(wait).first.wait_for()
        text = slim_dom(page, selector)
        browser.close()
    return text


def _format_dom_doc(text: str, note: str = "") -> str:
    note = note.strip()
    return f"> **检查点备忘**：{note}\n\n{text}" if note else text


def capture_dom_interactive(url: str, auth: str | None = None, selector: str = "body",
                            out_dir: str = "reports/dom") -> list[Path]:
    """打开有界面浏览器，手动走完上游业务操作（如登录 → 加购 → 打开弹窗）再抓取当前页瘦身 DOM，输入 q 结束。"""
    from playwright.sync_api import sync_playwright

    dest = Path(out_dir)
    if not dest.is_absolute():
        dest = project_root() / dest
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    with sync_playwright() as p:
        browser = _launch(p, headless=False)
        state = auth or (str(auth_path()) if auth_path().is_file() else None)
        context = browser.new_context(**({"storage_state": state} if state else {}))
        page = context.new_page()
        page.goto(url)
        print("浏览器已打开：请手动操作到目标页面/前置状态（例如先加购再进结算页），然后回终端抓取。")
        idx = 1
        while True:
            try:
                raw = input(f"输入页面名称保存 DOM（回车默认 page_{idx}，输入 q 退出）：").strip()
            except EOFError:
                break
            if raw.lower() in ("q", "quit", "exit"):
                break
            name = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", raw).strip("_") or f"page_{idx}"
            try:
                note = input("本页操作意图 / 看到什么算成功（可选，直接回车跳过）：").strip()
            except EOFError:
                note = ""
            try:
                text = slim_dom(page, selector)
            except Exception as e:
                print(f"[FAIL] 抓取失败（可切回目标页后再试）：{e}")
                continue
            doc = _format_dom_doc(text, note)
            target = dest / f"{name}.md"
            target.write_text(doc, encoding="utf-8")
            saved.append(target)
            print(f"[OK] 已保存 {target}（约 {len(doc) // 2} token）")
            idx += 1
        browser.close()
    return saved


def inject_storage(page, url: str, local_storage: dict[str, str] | None = None,
                   cookies: list[dict] | None = None) -> None:
    """把接口登录拿到的 Token / Cookie 直接写进浏览器，跳过 UI 登录页（接口→UI 衔接用）。"""
    if cookies:
        page.context.add_cookies(cookies)
    if local_storage:
        t, c = urlparse(url), urlparse(page.url)
        if (c.scheme, c.netloc) != (t.scheme, t.netloc):
            page.goto(url)
        page.evaluate(
            "(items) => { for (const [k, v] of Object.entries(items)) localStorage.setItem(k, v); }",
            local_storage,
        )


def context_args(name: str = "user") -> dict:
    """给 conftest 的 browser_context_args 用：带上 base_url，和已保存的登录态（有就用）。"""
    args = {}
    if base := setting("ui", "base_url"):
        args["base_url"] = base
    state = auth_path(name)
    if state.is_file():
        args["storage_state"] = str(state)
    return args
