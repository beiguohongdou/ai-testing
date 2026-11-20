# caseflow

> **版本** 3.0 | **Python** ≥ 3.10 | **许可证** MIT  
> 配套 AI 工作流技能母版：[`caseflow/SKILL.md`](caseflow/SKILL.md)

一个轻量级 **AI 赋能接口 + Playwright UI 自动化测试提效框架**：把零散的需求/接口文档/抓包，按可控工作流落成稳定可运行的自动化测试与 Excel 用例表。

### 为什么做 `caseflow`？

直接让大模型生成自动化测试通常会遇到三个痛点：
1. **爱瞎编**：凭空编造接口字段、错误码或不存在的页面定位器；
2. **假绿多**：只断言 `HTTP 200`，业务报错了测试照样全绿；
3. **烧 Token 且容易断链**：把整页 HTML 喂给 AI 动辄数万 Token，遇到登录态或必须先加购才能结算的场景直接卡死。

`caseflow` 用 **「AI 工作流约束 + 确定性工具箱」** 解决这三件事：
- **先梳理、再拍板、后落地**：接手项目先建 `PROJECT_MAP.md` 理清模块与上下游依赖；AI 先出 10～20 行带 `[AUTO]` 标记的提纲 + 选择题，人工 2 分钟拍板后再写代码；
- **双轨分离**：单接口多参数边界走极简 YAML 数据驱动（支持 `base_json` + `drop`、`xfail` 实测校准、改完直接 `pytest` 无需编译）；复杂多步流与 Playwright UI 写原生可调试 Python；
- **UI 提效底座**：`caseflow auth` 手动过验证码存多角色登录态（`page_as` 隔离）、`caseflow dom -i` 交互式抓瘦身 DOM（单页仅 **1～2.5k Token**）、失败自动留存全屏截图 + 去重后的控制台报错与失败接口；
- **接口提效底座**：进程内按角色复用登录、兼容国内 `HTTP 200 + body.code` 响应信封、生产域名防呆锁、失败自动拼装脱敏 `curl` 复现命令、可选变异测试 `caseflow mutate` 专抓假绿；
- **文档永不过期**：`caseflow export` 反向扫描真正在跑的 YAML 与代码，1 秒导出最新 Excel（CSV）/ Markdown 用例表。

```text
任意文档 / 抓包 ─▶ PROJECT_MAP 梳理依赖 ─▶ 提纲 + 选择题拍板 ─┬─▶ 单接口 YAML 数据驱动 ──┐
                                                             ├─▶ 多步流原生 pytest ─────┼─▶ mutate 防假绿 & export 出表
                                                             └─▶ 瘦身 DOM → PO + UI ────┘
```

---

## 一、 安装

### 1. 安装 `caseflow`（推荐在项目 `.venv` 内）

```bash
# 仅接口自动化
pip install "git+https://github.com/beiguohongdou/ai-testing.git"

# 接口 + Playwright UI 自动化（首次使用如未装过浏览器，再跑一次 playwright install chromium）
pip install "caseflow[ui] @ git+https://github.com/beiguohongdou/ai-testing.git"

# 本地开发调试（克隆本仓库后在根目录执行）
pip install -e .
```

验证安装：

```bash
caseflow --help
caseflow selftest
```

### 2. 加载 AI 工作流技能（全平台 / 跨 Agent 通用）

`caseflow init` **只生成纯净的测试骨架**，不会强制写入特定 IDE 的配置目录。按你使用的工具按需选择：

```bash
caseflow skill           # 安装到通用全局目录（~/.agents/skills 与 ~/.claude/skills，支持 Claude Code / Codex / Cursor）
caseflow skill --agents  # 额外在当前项目生成通用 AGENTS.md 入口（已有文件不覆盖）
caseflow skill --cursor  # 仅 Cursor 用户：额外在当前项目生成 .cursor/rules/ai-testing-workflow.mdc
caseflow skill --print   # 网页版大模型 / 无客户端：直接输出规则全文方便复制粘贴
```

---

## 二、 常用命令（8 项）

| 命令 | 作用 |
|---|---|
| `caseflow init [目录]` | 初始化纯净项目骨架（不覆盖已有文件） |
| `caseflow skill` | 跨平台安装或打印 AI 工作流技能（支持 `--agents` / `--cursor` / `--print`） |
| `caseflow auth <登录URL>` | 打开浏览器手动过验证码登录后按回车，保存 `.auth/<name>.json` 免登录跑 UI |
| `caseflow dom <URL> [-o 页.md]` | 提取单页瘦身交互元素与无障碍树（约 1~2.5k Token），喂给 AI 写 Page Object |
| `caseflow dom <起始URL> -i` | **交互式抓多页**：在浏览器里手动走完上游步骤（如加购进结算页），回终端输页面名回车保存，输入 `q` 退出 |
| `caseflow export` | 反向扫描 `data/*.yaml` 和 `tests/**/test_*.py`，生成 `reports/用例表.csv`（Excel 直接打开）和 `用例表.md` |
| `caseflow mutate [路径]` | 故意清空响应体或翻转状态码/业务码重跑接口用例，揪出「只测 200」的假绿用例 |
| `caseflow selftest` | 运行框架自身离线自检测试 |

---

## 三、 初始化后的项目结构

运行 `caseflow init` 后生成的标准目录（各司其职、不混放）：

```text
your_project/
├── PROJECT_MAP.md      # 给 AI 看的项目地图：模块划分、上下游依赖矩阵、公共 fixture
├── caseflow.yaml       # 项目配置：接口基址、登录方式、国内 code:0 信封、生产域名防呆锁
├── .env.example        # 环境变量模板（真实账号密码放本地 .env，已默认被 .gitignore 屏蔽）
├── conftest.py         # 挂载 caseflow.plugin（api / api_as 与 UI 失败诊断）及 page_as 多角色隔离
├── pytest.ini          # pytest 配置（开启 --import-mode=importlib 防跨目录同名冲突）
├── docs/               # 放置 PRD、接口文档、抓包材料
├── data/               # 单接口参数化 YAML 数据表
├── pages/              # Playwright Page Object 页面对象
└── tests/
    ├── api/            # 接口自动化用例（YAML 参数化 + 原生多步业务流）
    └── ui/             # Playwright UI 自动化用例
```

---

## 四、 快速体验内置靶场示例（无需外部环境）

克隆仓库后可直接体验 [examples/](examples/) 自带的离线迷你商城靶场（含非标 PRD、国内 `code: 0` 接口、以及「登录 + 加购 → 结算」上游衔接）：

```bash
git clone https://github.com/beiguohongdou/ai-testing.git
cd ai-testing/examples
pytest -v                 # 自动拉起本地商城服务，跑通接口数据驱动 + 多步流 + UI 结算用例（如用本机 Chrome 加 --browser-channel chrome）
caseflow export           # 查看生成的 examples/reports/用例表.csv 和 用例表.md
caseflow mutate tests/api # 验证接口断言区分力
```

---

## 五、 新项目 5 分钟上手流程

1. **初始化与填配置**：在项目根执行 `caseflow init`，复制 `.env.example` 为 `.env` 填入测试地址与账号，按实际后端风格调整 `caseflow.yaml`（标准 RESTful 靠 HTTP 状态码判断时把 `envelope` 设为 `null`）。
2. **梳理模块与依赖**：把文档放进 `docs/`，让 AI 先读文档填写 `PROJECT_MAP.md`（模块划分与上下游依赖矩阵）。
3. **提纲 + 选择题拍板**：按模块推进，先让 AI 输出 10～20 行带 `[AUTO]` 标记的测试点提纲并把模糊口径做成选择题，人工 2 分钟拍板后再落地代码。
4. **双轨落地**：
   - **UI**：`caseflow auth` 存多角色登录态（用 `page_as("user")` 隔离干净 `page`）→ `caseflow dom -i` 抓真实瘦身 DOM → 生成 PO 与 Web-First 断言（前置造数与清脏数据优先调接口 `client()`）。
   - **接口**：单接口多参数写进 `data/*.yaml`（支持 `base_json` + `drop` 测必填缺失、`xfail` 探活、`xfail_strict` 标已知缺陷）；多步业务流写原生 pytest 调用 `client()` 与 `gen.*()`。
5. **验收与出表**：每完成一个模块跑一次全量 `pytest` + `caseflow mutate`，随时用 `caseflow export` 导出最新用例表。
