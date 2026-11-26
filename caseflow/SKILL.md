---
name: ai-testing
description: caseflow v3 个人测试提效工作流：需求/接口文档/抓包 → 盲区挖掘与选择题拍板 → Playwright UI 与接口双轨自动化 → 防改断言硬门禁、防假绿变异扫描与导出用例表。
---

# AI 测试工作流（caseflow v3）

> **核心定位**：AI 干 90% 的拆解、造数、编码粗活，人只在关键节点花 2 分钟拍板，再用**真实环境实测**校准盲区。  
> 工具包 `caseflow` 通过 `pip install` 安装，不向业务项目复制框架源码。细则按需阅读同目录 `references/` 或参考项目 `examples/`。

## 前置：安全与脱敏红线

1. **禁贴清单**：真实生产库连接串、真实 API Key / Token、个人隐私（身份证/真实手机号）、生产环境真实地址。
2. **账号密钥占位**：测试账号密码绝不写进代码或 YAML，统一放项目 `.env`，YAML 和配置里只写 `${ENV_NAME}`。
3. **防误打生产**：`caseflow.yaml` 默认拦截域名含 `prod / production / live / online` 的非 GET 请求。

## 三道强制停机门禁（必须严格遵守）

1. **配置门禁**：动笔写第一条用例前，先核对 `caseflow.yaml` 是否已脱离脚手架默认值（`envelope`、`login`、`roles`）且 `.env` 已建好；未就绪时**先只改配置并列出 `.env` 变量清单停下**，禁止直接写用例。
2. **单模块门禁**：每轮最多只落地 **1 个模块**；写完必须跑通 **`pytest`**、**`caseflow guard`**（防擅自改断言/加 skip）和 **`caseflow mutate`**（或 `--changed`），更新 `PROJECT_MAP.md` 后停下来汇报。
3. **DOM 先行门禁**：写 UI 用例前，若没有目标页真实元素依据（`reports/dom/*.md`、`codegen` 输出或本轮实测 DOM），先停下来给出 `caseflow dom <url> -i` 命令让用户抓取，禁止凭空猜定位器。

## 主干工作流（Step 0 ～ Step 4）

1. **Step 0 · 项目地图（`PROJECT_MAP.md`）**：接手大项目先划分业务模块与上下游依赖矩阵（需要什么 Token/前置状态、用 `.auth/` 还是接口造数衔接）。每次做新模块**新开干净会话**，只 `@ PROJECT_MAP.md` + 当前模块材料，防上下文漂移。
2. **Step 1 · 材料三态适配**：输出首行声明手上材料形态（双源齐全 / 仅 PRD / 仅接口文档或抓包），**严禁凭空编造未写明的 URL、字段、错误码或业务规则**。
3. **Step 2 · 提纲、盲区清单与选择题拍板（唯一强人工卡点）**：
   - 先出 10～20 行精简提纲：`编号 | 模块 | 测试场景 | 预期方向 | [AUTO-UI / AUTO-API / 手工]`；
   - 顶部集中列 **【隐含盲区与待确认假设（Unknowns）】** 及选择题（如并发重放、越权状态码、库存校验时机、文档口径冲突），等用户拍板后再写代码。
4. **Step 3 · 双轨落地**（按需阅读对应指南与 `examples/` 真实范例）：
   - **UI 自动化（20% 主干流）**：先 `caseflow auth` 存登录态（`page_as` 隔离）→ `caseflow dom` 抓瘦身 DOM → 写 PO 与 Web-First 断言。详规见 [ui-guide.md](references/ui-guide.md)。
   - **接口自动化（80% 参数边界 + 业务流 + 深度测试）**：单接口多参数走 `data/*.yaml`；多步流/分支/不变量/差分对比写原生 Python。详规见 [api-guide.md](references/api-guide.md)。
5. **Step 4 · 排错、门禁校验与出表**（详规见 [debug-guide.md](references/debug-guide.md)）：
   - 严禁为凑绿灯擅自放宽或删掉 `assert` / `expect`；
   - 修错后跑 `caseflow guard` 防篡改校验 + `caseflow mutate`（大项目可用 `--changed`，老项目可用 `--observe`）防假绿；
   - 建议新开干净会话对 `git diff` + `reports/` 做一次独立复核，最后 `caseflow export` 导出最新用例表。
