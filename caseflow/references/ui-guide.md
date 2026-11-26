# 轨道 A：Playwright UI 自动化指引

> **分工原则**：UI 只扛 20% 核心主干路与页面渲染/交互；80% 的参数边界、多组合异常交给接口轨道。  
> 完整代码范例见 `examples/pages/shop_page.py` 和 `examples/tests/ui/test_checkout_ui.py`。

## 1. 打通登录与上游数据依赖

- **多角色登录隔离**：`caseflow auth <登录URL> --name <角色>`（默认 `user`）把登录态存为 `.auth/<角色>.json`。新脚手架默认让 `page` 保持干净未登录态，需要免登录的业务用例使用 `page_as("user")` / `page_as("admin")`，与登录/退出用例互不污染。
- **跨模块上游业务数据**：前置造数（如注册一次性临时账号）和清脏数据（清空购物车/收藏）**优先直接调用接口 `client()`**，不要在 UI 里用 `if locator.count():` 碰运气清理（存在异步加载竞态，又慢又不稳）。

## 2. 获取页面元素的两种方式与成本选型

| 方式 | 怎么操作 | Token 消耗 | 推荐模型 | 适用场景 |
|---|---|---|---|---|
| **方案 1：脚本抓瘦身 DOM（主力，最省钱稳定）** | 单页直接 `caseflow dom <url> -o page.md`；**需先走完上游操作/弹窗的**用 `caseflow dom <login_url> -i`（浏览器里手动点到目标状态后回车抓取，可顺手输入本页检查点备忘），或用 `playwright codegen` 录一段草稿 | **极低**（每页 50～150 行交互骨架，约 **1,000～2,500 Token**） | 任意主流代码/对话模型均可高准确率完成 | 日常 90% 的页面元素获取与 PO 编写 |
| **方案 2：Browser MCP / Agent 自主点击探索** | 让带浏览器工具的 Agent 自己点开页面、读 Accessibility Snapshot 或截图 | **偏高**（多轮截图 + 树回传，单页流程常 **1.5万～5万+ Token**） | 强 Tool-Calling / 多模态模型 | 无人值守探索陌生系统，且预算充足时 |

## 3. 生成 PO 与原生 Playwright 用例的铁律

- **PO 一页一文件**：`pages/` 下按页面拆文件（如 `pages/login_page.py`、`pages/register_page.py`），每个文件只放 1 个 Page Object 类；PO 构造只接收 `page: Page`，直接用相对路径 `page.goto("/path")`（利用 `browser_context_args` 的 `base_url`），不在用例里手传 `base_url`。
- **一函数一场景 + 按业务类分组**：用 `class TestXxx:` 按模块分组，每条 `test_*` 只测 1 个明确场景（6~15 行一眼可读）；禁止在一条用例里塞多角色串测，禁止在用例体里手写 `browser.new_context()` 或 `setting(...)`。
- **清理统一进 `fixture` teardown（`yield`）**：删除临时账号、恢复账号资料、清空购物车等统一放 `yield` fixture 后置（显式用 `# setup` / `# teardown` 注释标明），用例体内不写 `try ... finally`。按作用域分层放置：全局基础设施与跨 UI/API 共享 fixture 放根目录 `conftest.py`，仅 UI 使用的放 `tests/ui/conftest.py`，仅 API 使用的放 `tests/api/conftest.py`，偏底层的纯工具函数放 `utils/`。
- **先做映射表，再写代码**：拿到瘦身 DOM 后，先对照测试步骤确认「每一步操作/断言对应 DOM 里的哪个 `data-testid` / `role` / `label`」，找不到就直接提示「页面缺该元素（可能缺上游状态）」，**严禁凭想象编造定位器**。
- **定位器优先级**：`get_by_test_id` > `get_by_role(..., name=...)` > `get_by_label` > `get_by_placeholder` > 稳定 `id`/`name`；避开动态长数字 id、`.nth()` 和样式类名。
- **反脆弱铁律**：
  - **严禁 `time.sleep()`**。
  - **异步回填表单防覆盖**：遇到进入页面后会自动拉取默认值回填的表单（如结算页默认地址），必须先等回填接口响应完成（`page.expect_response(...)`）或默认值出现后再 `fill()`，避免刚填完被晚到的回填覆盖清空。
  - **必须用 Web-First 断言**：`expect(locator).to_be_visible()` / `to_have_text()` / `to_have_count()`，禁止用 `assert locator.inner_text() == ...`（不会自动等待，极易偶发挂）。
  - **写入类用例禁止盲目自动重跑**：涉及下单、扣款、创建工单等数据写入的 UI 用例，禁止无脑配置自动重试（`--reruns`）；写入结果不明时应停在当前环节核查，优先将接口返回的业务标识带到页面精确核对。
  - 用例 docstring 第一行写 `TC-编号 标题`（供 `caseflow export` 扫描出表）。
