# AI 测试 · 移植包

> **版本** 1.15 | 来源：`AI赋能测试/知识框架`（81 条；1.15：目录 `AI测试/`，技能 `ai-testing`）  
> **用途**：下载本文件夹到新电脑 → 运行安装 → 重启 Cursor → 直接使用

---

## 包里有什么

```
AI测试/
├── install.ps1              ← 安装脚本（必跑）
├── install.bat              ← 双击安装（Windows）
├── user-rules.txt           ← 复制到 Cursor 全局设置（推荐）
├── skills/
│   └── ai-testing/
│       └── SKILL.md         ← 九条工作流（核心）
├── rules/
│   └── ai-testing-workflow.mdc
├── reference/
│   └── 知识框架速查.md      ← 无 AI赋能测试 文件夹时也能查阅
├── 任务路由.md
└── README.md                ← 本文件
```

| 组件 | 作用 |
|------|------|
| **Skill** | Agent 自动执行：提示词、用例、数据、排错、pytest 治理、断言审查、可重复性 |
| **Rule** | 打开项目时提醒 Agent 走 Skill |
| **user-rules.txt** | 全局生效（不依赖项目），粘贴到 Cursor Settings → Rules |

`reference/` 建议一起拷（没有完整知识框架时靠它查阅）。`会话纪要/` 是本机讨论记录，**转移到别的电脑不必带**。

---

## 新电脑安装（三步）

> **大白话**：`install.ps1` = **拷贝安装器**。把 `AI测试/skills` 复制到用户目录下的全局 Skill 文件夹（`.agents` 给 Cursor，`.claude` 给 Claude Code / Hanako；Cursor 也会扫 `.claude`），把 `AI测试/rules` 复制到项目的 `.cursor/rules`。打开本仓库时 Agent 仍可读 `CLAUDE.md` / `AGENTS.md`。没跑过也没关系：本仓库 `.cursor/rules` 已在项目里；跑 install 是为了让**别的项目**和三端全局 Skill 跟母版一致。

### 第 1 步：复制文件夹

把 **`AI测试/` 整个文件夹** 拷到新电脑（U 盘、网盘、Git 均可）。  
位置任意，例如 `D:\tools\AI测试\`。

可选：同时复制 `AI赋能测试/` 作课程知识库查阅，**不复制也能用**。

### 第 2 步：安装 Skill + Rule

**方式 A** — 在公司项目根目录打开 PowerShell：

```powershell
& "D:\tools\AI测试\install.ps1" -ProjectRoot "D:\公司项目"
```

**方式 B** — 只装全局 Skill（暂不绑项目）：

```powershell
& "D:\tools\AI测试\install.ps1" -SkillsOnly
```

**方式 C** — 双击 `install.bat`，按提示输入项目路径（或直接回车仅装 Skill）

安装结果：
- Skill → `C:\Users\<你>\.agents\skills\ai-testing\`（Cursor）
- Skill → `C:\Users\<你>\.claude\skills\ai-testing\`（Claude / Hanako；Cursor 兼容）
- Rule → `<项目>\.cursor\rules\ai-testing-workflow.mdc`

### 第 3 步：全局 Rule（推荐，一次设置永久生效）

1. 打开 Cursor → **Settings** → **Rules**
2. 打开 `AI测试/user-rules.txt`，**全选复制**
3. 粘贴到 User Rules 区域，保存
4. **重启 Cursor** 或新开 Agent 对话

---

## 验证

新开 Agent，输入：

> 按框架，帮我写个生成接口用例的四段式提示词

应输出四段式模板 + 评审清单自评。

---

## 笔记更新后：如何同步移植包

> **不会自动更新**。`笔记资料/` 加了新课后，需按下面流程把变化同步到本移植包。

### 总流程

```
新笔记 → 笔记资料/index.md → 知识框架/*.md → AI测试/ → install.ps1 → 公司电脑重装
```

| 步骤 | 改哪里 | 谁来做 |
|------|--------|--------|
| 1 | `AI赋能测试/笔记资料/` 放入新笔记 | 你 |
| 2 | `笔记资料/index.md` 加一行课次对照 | 你或 Agent |
| 3 | `知识框架/` 提炼/新增条目，`index.md` 更新 | 你或 Agent |
| 4 | 判断要不要改 `AI测试/`（见下表） | 你或 Agent |
| 5 | 本机运行 `install.ps1` | 你 |
| 6 | 公司电脑：拷新版 `AI测试/` → 再跑 `install.ps1`；`user-rules.txt` 有变则重新粘贴 | 你 |

对 Agent 说：**「根据新笔记 `笔记资料/第XX节-xxx.md` 更新知识框架和 AI测试移植包」** 即可触发 2～4 步。

**三端共用维护协议**：`AI赋能测试/AGENTS.md`（路由）→ `AI赋能测试/知识框架/维护协议.md`（执行）。Claude/Hanako 读仓库根 `CLAUDE.md` 自动触发。

### 什么情况要改移植包

| 新笔记内容 | 改 AI测试/ 哪里 |
|------------|----------------|
| 新检查项、原则补充（不影响新任务类型） | `reference/知识框架速查.md` |
| 新任务类型（如 UI 自动化、性能测试） | `skills/ai-testing/SKILL.md` 加 **WF-N**；`任务路由.md` 加一行 |
| 全局约束变化（脱敏、禁止项、先查后做） | `user-rules.txt` + `rules/ai-testing-workflow.mdc` |
| 仅概念性内容、查阅用 | **只改知识框架**，移植包可不动 |

### 同步后自检

1. 本机执行：`& ".\AI测试\install.ps1"`（路径按实际修改）
2. 重启 Cursor，新开 Agent
3. 用与新课相关的一句话测试（例如新课讲性能测试，就问性能相关任务是否走新 WF）

### 版本记录（建议）

大改后在本文顶部更新版本号，例如 `版本 1.0 → 1.1`，方便辨认公司电脑上的包是否过期。

---

## 日常小改（不动笔记时）

1. 改 `AI测试/skills/ai-testing/SKILL.md` 或 `reference/`
2. 重新运行 `install.ps1`
3. 重启 Cursor

## 与 AI赋能测试 的关系

| 目录 | 必须带吗 | 说明 |
|------|----------|------|
| `AI测试/` | **是** | 本移植包，Agent 干活靠它 |
| `AI赋能测试/` | 否 | 课程笔记+知识框架；`reference/知识框架速查.md` 已含精华 |
