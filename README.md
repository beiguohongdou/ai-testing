# caseflow（AI 测试提效框架 v3 个人实战版）

> **版本** 3.0 | 许可证 MIT  
> 配套技能：`caseflow/SKILL.md`（通过 `caseflow skill` 安装或打印） | 旧版 v2.2 归档在 Git Tag `v2.2-archive`

一个专门帮个人测试工程师落地的轻量框架：**需求/接口文档/抓包 → 测试用例提纲拍板 → Playwright UI + 接口双轨自动化 → 反向导出 Excel 用例表**。

v3 彻底砍掉了繁琐的团队审批签字、强正则出处阻断和全量 YAML 编译流程，改为：
- **标准可安装 Python 包**（`pip install` 到任意虚拟环境，不往业务项目复制框架源码）
- **UI 自动化（主力）**：半自动过验证码存登录态、交互式/单页瘦身 DOM 抓元素（省 95% Token）、接口造上游数据注入 UI、失败自动抓截图 + 控制台报错 + 报错接口
- **接口自动化**：单接口多数据走极简 YAML 参数化（改完即跑，无需编译）、多步业务流写原生 Python、按角色 Session 复用登录、原生支持国内 `HTTP 200 + body.code`、失败自动拼装脱敏 `curl` 复现命令、可选变异检查防假绿

---

## 一、 安装

### 1. 安装 `caseflow` Python 包

```powershell
# 方式 A：直接从 GitHub 安装（任何电脑 / 虚拟环境通用，不依赖本地路径）
pip install "git+https://github.com/beiguohongdou/ai-testing.git"
# 如果需要 Playwright UI 支持：
pip install "caseflow[ui] @ git+https://github.com/beiguohongdou/ai-testing.git"

# 方式 B：本地开发调试（在仓库根目录执行）
pip install -e .
```

安装完成后验证：

```powershell
caseflow --help
caseflow selftest
```

### 2. 按你的 Agent 形态加载 AI 工作流技能（全平台通用）

`caseflow init` 默认**只生成纯净的测试骨架**，不会强制往项目里建 `.cursor/`。根据你使用的工具按需选择一条命令：

```powershell
caseflow skill           # 主流 CLI / IDE Agent（装进 ~/.agents/skills 和 ~/.claude/skills，支持 Claude Code / Codex / Cursor）
caseflow skill --agents  # 额外在当前项目生成通用 AGENTS.md 入口（不覆盖已有文件）
caseflow skill --cursor  # 仅 Cursor 用户需要：额外在当前项目生成 .cursor/rules/ai-testing-workflow.mdc
caseflow skill --print   # 网页版大模型 / 无客户端：直接打印工作流规则全文方便复制粘贴
```

---

## 二、 7 个常用命令

| 命令 | 作用 |
|---|---|
| `caseflow init [目录]` | 在业务项目初始化纯净骨架（`caseflow.yaml`、`.env.example`、`conftest.py`、`PROJECT_MAP.md`、防泄密 `.gitignore`） |
| `caseflow skill` | 跨平台安装工作流技能到通用目录（支持 `--agents` / `--cursor` / `--print`） |
| `caseflow auth <登录URL>` | 弹出浏览器让你手动过验证码登录后按回车，自动保存 `.auth/user.json` 免登录运行 UI 用例 |
| `caseflow dom <URL> [-o 页.md]` | 提取瘦身交互元素树与无障碍树（约 1~2.5k Token），喂给 AI 写 Page Object |
| `caseflow dom <登录URL> -i` | **交互式抓多页**：浏览器里手动点完上游步骤（如加购后进结算页），回终端输页面名回车抓取，输入 `q` 退出 |
| `caseflow export` | 反向扫描 `data/*.yaml` 和 `tests/**/test_*.py`，生成 `reports/用例表.csv`（Excel 直接打开）和 `用例表.md` |
| `caseflow mutate [路径]` | 故意把响应改空或翻转状态码/业务码重跑接口用例，揪出「只测 200」的假绿用例 |
| `caseflow selftest` | 运行框架自身离线自检测试 |

---

## 三、 快速体验内置靶场示例（无需公司环境）

仓库自带 [examples/](examples/) 离线迷你商城靶场（模拟非标 PRD + 国内 `code: 0` 接口 + 登录/加购/结算上游依赖）：

```powershell
cd examples
pytest -v                 # 自动拉起本地商城服务，跑通接口数据驱动 + 多步流 + UI 结算用例
caseflow export           # 查看生成的 examples/reports/用例表.csv 和 用例表.md
caseflow mutate tests/api # 验证接口断言区分力
```

---

## 四、 新项目落地 5 分钟上手流程

1. **初始化项目**：在新项目目录执行 `caseflow init`，复制 `.env.example` 为 `.env` 填入测试地址与账号，按项目情况修改 `caseflow.yaml`（如国内项目保持 `envelope: {code: code, ok: 0, msg: msg, data: data}`）。
2. **梳理模块与上下游依赖**：让 AI 读已有文档/页面，把模块划分、上下游依赖矩阵写进 `PROJECT_MAP.md`。
3. **提纲 + 选择题拍板**：让 AI 先出 10～20 行带 `[AUTO]` 标记的测试点提纲并把模糊点做成选择题；你花 2 分钟拍板后再生成用例和代码。
4. **双轨落地**：
   - **UI**：`caseflow auth --name user` 存多角色登录态（用 `page_as("user")` 隔离干净的 `page`）→ `caseflow dom -i` 或 `playwright codegen` 获取目标页元素 → 生成 PO 与 Web-First 断言的原生 pytest（前置造数与清脏数据优先调接口 `client()`）。
   - **接口**：单接口多参数写进 `data/*.yaml`（支持 `base_json` + `drop` 测必填缺失、`xfail` 探活与 `xfail_strict` 标已知缺陷）用 `load_cases` + `run_case` 跑；多步业务流直接写原生 pytest 调用 `client("user")` 与 `gen.*()`。
5. **验收与出表**：每完成一个模块跑一次全量 `pytest` + `caseflow mutate`，随时用 `caseflow export` 导出最新用例表交差。

---

## 五、 从旧版 v2.2 找回历史代码

如需查阅重构前的 `aigen`（含 YAML DSL 编译器、`approvals.json` 签字和 `evals` 闸评测）：

```powershell
git checkout v2.2-archive
```
