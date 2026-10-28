# AI 测试工作流套件

> **版本** 2.1 | 许可证 MIT

给 Cursor / Claude Code 用的 AI 测试工作流，外加一个把 **PRD + 接口契约** 变成 **YAML 用例**、再生成 **pytest 代码** 的框架 `aigen`。

- **技能 `ai-testing`**：九条工作流（提示词、用例表、测试数据、排错、pytest 治理、Swagger 转代码、断言审查、可重复性、UI 页面对象与定位器自愈），让 Agent 按固定步骤干活、不编造。
- **框架 `aigen`**：AI 只写 YAML 用例；程序校验出处是否真实存在于契约 / PRD；人评审后由模板生成 pytest（不经过 AI）；还能检查用例与代码一致、验收覆盖、断言能否区分成功与失败，并导出用例表。

---

## 目录

```
├── install.ps1              ← 安装脚本（Windows PowerShell）
├── install.bat              ← 双击安装
├── user-rules.txt           ← 可选：粘贴到 Cursor Settings → Rules，全局生效
├── skills/ai-testing/SKILL.md   ← 九条工作流（核心）
├── rules/ai-testing-workflow.mdc ← 项目规则：提醒 Agent 走技能
├── reference/知识框架速查.md ← 原则与检查清单（随技能一起安装）
├── aigen/                   ← 用例生成框架（-WithAigen 装进项目）
│   ├── draft_rules.md       ← AI 起草 YAML 的规则
│   ├── aigen.yaml.example   ← 项目配置模板（换项目只改它）
│   ├── .env.example         ← 本机配置模板（批量起草的模型地址、密钥）
│   ├── requirements.txt
│   └── examples/            ← 接口 / UI 用例格式示例
└── 任务路由.md               ← 任务 → 工作流对照表
```

---

## 安装

```powershell
git clone https://github.com/beiguohongdou/ai-testing.git D:\tools\ai-testing
cd D:\tools\ai-testing

.\install.ps1                                          # 只装全局技能
.\install.ps1 -ProjectRoot "D:\你的项目"                 # 再装项目规则
.\install.ps1 -ProjectRoot "D:\你的项目" -WithAigen      # 再把 aigen 框架装进项目
```

也可以双击 `install.bat` 按提示操作。装完重启 Cursor，或新开一个 Agent 对话。

安装位置：
- 技能 → `%USERPROFILE%\.agents\skills\ai-testing\`（Cursor）和 `%USERPROFILE%\.claude\skills\ai-testing\`（Claude Code）
- 规则 → `<项目>\.cursor\rules\ai-testing-workflow.mdc`
- 框架 → `<项目>\aigen\`（重复安装只覆盖框架代码，不动项目的 `aigen.yaml`、`.env`、`cases/`）

**Mac / Linux**：`install.ps1` 只支持 Windows。手动把 `skills/ai-testing` 复制到 `~/.agents/skills/`（Cursor）或 `~/.claude/skills/`（Claude Code），再把 `reference/` 复制到 `ai-testing/` 里面；需要框架时把 `aigen/` 复制到项目根。

更新：`git pull` 后重新运行同一条安装命令。

---

## 使用 aigen

需要 Python 3.10+。在项目根：

```powershell
pip install -r aigen\requirements.txt
copy aigen\aigen.yaml.example aigen.yaml     # 填材料路径、常量模块、登录方式、身份
python -m aigen selftest                      # 框架自检应全部通过
```

日常流程：

```
AI 按 aigen/draft_rules.md 写 cases/*.yaml
  → python -m aigen validate     格式 + 出处核对（引用的接口、状态码、验收编号必须真实存在）
  → 人评审 pending               确认前不生成代码
  → python -m aigen render       YAML → testcase/api_gen/、testcase/ui_gen/
  → python -m aigen trace        用例与代码一致、PRD 每条验收都有用例
  → pytest testcase/api_gen
  → python -m aigen mutate       断言区分力（报 [WEAK] 就加强断言）
  → python -m aigen export       用例表.md / 用例表.csv / 验收覆盖.md
```

- 起草可以在 IDE 里让 Agent 做（推荐），也可以用 `python -m aigen draft <module>` 批量调模型：复制 `aigen\.env.example` 为项目根 `.env` 填模型地址和密钥，并把 `.env` 加进 `.gitignore`。
- CI 用 `python -m aigen check`（validate + trace + selftest + 运行接口用例 + mutate；任一失败或有 `[WEAK]` 断言即退出码非 0）。UI 用例不在 check 里，CI 需单独跑 `pytest testcase/ui_gen`。
- 不用 aigen 的项目，CI 至少跑一遍 pytest 全量，并按 WF-7 扫描「只 assert status」的用例。
- 生成的代码不要手改；要改就改 YAML 再 render。

已知限制：UI 只支持 Playwright；UI 断言不做变异检查；签名类鉴权需自己写客户端类（`api.client_class`）；不支持流式响应、GraphQL、gRPC。

---

## 项目里要准备 / 按团队调整的东西

技能会在项目里读写下面这些约定，第一次用时按需建好：

| 约定 | 用途 | 对应工作流 |
|------|------|------------|
| 项目 `agents.md` 里的签字角色表 | 每道人工闸由谁签。技能里的表是默认分工（产品答业务口径、开发答契约、测试签其余各闸），**项目表优先** | 技能「人工闸与签字角色」 |
| `docs/待确认.md` | gap / pending 落盘：问谁、提出日期、答复、回写位置；答复后不删行 | WF-2 ⑦ |
| `docs/度量.md` | 每迭代一行：pending 关闭时长、fix loop 达上限比例、弱断言占比 | 技能「度量」 |
| `pytest.ini` 注册 `smoke` / `core` mark | 冒烟排除环境问题、按改动选回归范围 | WF-4 |

## 从 2.0 升级

- **`aigen check` 变严了**：现在会运行接口用例并做 `mutate`，所以 CI 必须能连到被测环境；用例失败或有 `[WEAK]` 断言都会让 check 失败。原来能过的 CI 升级后可能变红，先本地跑一遍 `python -m aigen check`。
- **产品缺陷不再用 skip**：改为 `xfail(strict=True)` + 缺陷单号（YAML 模式写 `known_issue`）；skip 只用于环境或前置条件不具备。已有的 skip 建议按此复查。
- **用例覆盖按风险分级**：替代原来固定的「每接口 1 正 3 反」。
- `rules/ai-testing-workflow.mdc` 和 `user-rules.txt` 精简为一句「执行技能 ai-testing」，内容以技能为准；粘贴过旧版 `user-rules.txt` 的，请到 Cursor Settings → Rules 替换。

## 验证安装

新开 Agent，输入：

> 按框架，帮我写个生成接口用例的四段式提示词

应输出四段式模板 + 评审清单自评。
