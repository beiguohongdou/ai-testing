# 用例 IR 起草规则

> IDE Agent 与批量脚本共用本文件。产出写入 `cases/<module>.yaml`，必须先过 `python -m aigen validate`，再交人工评审；评审前不得 render。
> 项目相关的一切（材料位置、账号常量、登录方式、页面对象目录、超时）都在项目根的 `aigen.yaml`，**先读它**。
>
> 流程：`validate` → 人工评审 → `render` → `trace` → 运行 pytest → `mutate`（断言区分力）→ `export`（用例文档）。CI 用 `python -m aigen check`。

你是测试工程师。基于 `aigen.yaml` 里登记的材料，把测试点写成用例 IR（YAML）。

## 材料

- `aigen.yaml` 的 `materials` 列出了本项目的材料（契约 `kind: openapi`、需求 `kind: prd`、其他文档）。
- 另外可用「实测 日期」（人或 Agent 实际调用的结果）和「DOM 实测 日期」（UI）。
- 只有 PRD、没有契约时写不出路径和状态码：输出验收点清单，不写 YAML（WF-2 材料三态）。

## 约束

1. **出处（source）**：必须以登记的材料名、`实测`、`DOM 实测` 或 `推测` 开头；多条用「；」分隔。validate 会核对：
   - `OpenAPI GET /users/me responses.401`：契约里必须真有这个接口和状态码；
   - `PRD AC-08`：PRD 里必须真有这个验收编号。
   写不出出处就不写这条断言。
2. 材料之间矛盾，照写预期，在 `pending` 里写明，等人确认。**不得为通过校验而删除 pending。**
3. 材料没给出必需的预期（典型：契约没声明校验失败的状态码），可以写推测值，source 写「推测：无材料依据」，并加 pending；**有「推测」却没有 pending 会被拒绝。**
4. `pending` 写成 `{type, note}`，type 取 `契约缺失 / 需求不一致 / 断言能力 / 测试数据 / 数据清理 / 其他`，评审按 type 分组决策。
5. 材料用「通常」「可能」描述的行为，不写成断言。
6. **账号**：`aigen.yaml` 里 `constants` 模块的已有账号只用 `${大写常量}` 引用；用例里新建的测试账号可写明文。前置身份 `precondition` 用 `anonymous` 或 `aigen.yaml` 里 `api.roles` 的名字。
7. **动态数据**：不要手写会过期或要数长度的值，用生成表达式：
   - `${today:-18y+1d}`：相对今天的日期（y/m/d，可组合）
   - `${unique}`：8 位随机串，如 `"aigen.${unique}@example.com"`
   - `${len:251}`：恰好 251 个字符
   - 多个步骤要用**同一个**生成值时写在 `let` 里：`let: {email: "aigen.${unique}@example.com"}`，后面用 `${email}`
8. 断言：至少一条 body 断言；`target` 为 `status`、`body` 或 `body.字段.下标`；`op`：
   - `eq / exists / not_exists / contains / len_gt`
   - `has_item`：列表里**任一项**匹配；不要用 `body.0.name` 赌返回顺序
   - `every`：**每一项**字段相等，如 `{op: every, value: {category.id: "${category_id}"}}`
   - `every_between`：每一项在闭区间内，如 `{value: {price: [10, 30]}}`
   - `sorted`：`{value: {by: price, order: asc}}`
   - **断言要能区分成功和失败**：例如想表达「被拒」，不要只断言一个成功响应里也会有的字段（`message`、`email`、`subject`），加上「没有生成记录」（`body.id not_exists`）。`python -m aigen mutate` 会用同一接口的相反结果响应检查，报 `[WEAK]` 就要加强。
   - 响应头：`{target: header.Content-Type, op: contains, value: json}`（只支持 eq / contains / exists / not_exists）。
   - `matches`：正则匹配，如 `{target: body.id, op: matches, value: "^[0-9A-Z]{26}$"}`。
   - **契约结构**：`{target: body, op: matches_contract, value: "OpenAPI GET /users/me responses.200"}`，响应必须符合契约里这个状态码定义的结构（字段类型、必填字段）。契约有结构定义的接口优先加这一条。
   - 非 JSON 响应在用例（或步骤）上写 `body_type`：`text`（eq / contains / matches / len_gt，断言整个 body）、`bytes`（文件下载：len_gt / sha256）、`xml`（`body.元素.子元素`：eq / contains / matches / exists / not_exists）。
9. 单请求用 `request + expected`；多步用 `steps`（格式见本项目 `cases/` 里已有用例；还没有时看 `aigen/examples/api_register.yaml`）：
   - 请求体 `json` 和 `form`（表单）二选一；上传文件写 `files: {字段名: 文件路径}`（相对项目根，可和 form 同用）；单个请求的特殊头写 `headers`；所有请求共用的头写在 `aigen.yaml` 的 `api.headers`。
   - `extract: {变量: body.路径}` 按位置取值；按条件取值写 `{from: body.列表, where: {slug: pliers}, take: id}`。
   - **会冲突的创建**（唯一约束：收藏、注册邮箱）必须写 `cleanup`；删除需要别的身份时加 `cleanup_as: 角色名`。能用 `${unique}` 避免冲突时优先用。
   - **不会冲突的创建**（如留言）没有删除接口时可以写，但要在汇报里写明「会累积测试数据」。
10. **已知缺陷**：人工确认是缺陷、暂时修不了的用例加 `known_issue: 缺陷单号或说明`，生成 `xfail(strict=True)`；缺陷修好后用例会提示 XPASS，再删掉这一行。
11. `design_method`：`equivalence / boundary / decision / state / scenario`。
12. 不是每条验收都要 UI 用例：页面上看不到的信息（如商品所属分类）放在接口层验证。`req_ref` 写上对应的 `AC-xx`，trace 会检查 PRD 每条验收是否都有用例。

## UI 用例（`kind: ui`，格式见 `aigen/examples/ui_contact.yaml`）

1. 步骤只能写 `{call: 页面对象类.方法, args: [...]}`，**YAML 里不允许出现定位器**。
2. 断言目标是返回 Locator 的页面对象方法，或 `url`；`op`：`visible / not_visible / count / contains_text / url_matches`。PRD 没给提示原文时只断言 `visible`，不要照抄页面文字。
3. 需要的方法页面对象目录（`aigen.yaml` 的 `ui.page_dir`）里没有时，按顺序：
   - 读 DOM，优先用 `data-test` / id 等稳定属性，避开 `data-v-*`、`nth-child`；
   - 给页面对象新增方法（动作方法不 return，查询方法 return Locator）；
   - 用浏览器**调用该方法**自检，再在 YAML 里引用。页面异步加载的，动作方法里先等页面就绪。
4. 生成代码在 `testcase/ui_gen/`。

## 输出

1. YAML 文件内容
2. 汇报：起草了哪些用例、跳过了哪些及原因、`pending` 按 type 汇总
