# 闸评测样本（示例）

用历史上**人确认过**的问题考闸：坏样本看它还拦不拦得住，干净样本看它会不会乱报。换模型、改 `review_rules.md` 或项目 `REVIEW.md` 之后，先跑一遍再用。

项目里的真实样本放在项目根 `evals/<样本id>/`，**不进公开仓库**（会带业务材料，放之前先脱敏）。本目录 3 个样本只演示格式：

| 样本 | kind | gate | 演示什么 |
|------|------|------|----------|
| EV-001 | bad | review | pytest 只断言状态码（假绿） |
| EV-002 | bad | review | YAML 出处引用了契约里没有的状态码（编造出处） |
| EV-003 | clean | review | 合格用例，审核员不该报严重问题 |

## sample.yaml 字段

| 字段 | 必填 | 说明 |
|------|------|------|
| id | 是 | 和目录名一致，字母数字和 `-_` |
| kind | 是 | `bad`：闸应拦住；`clean`：已确认没问题 |
| gate | 是 | `review`：AI 审核员；`validate` / `mutate`：程序闸 |
| target | 是 | 被审对象（用例 YAML 或 pytest 文件），相对样本目录 |
| materials | review 建议给 | 审核员能看到的材料节选（契约 / PRD），已脱敏 |
| record | mutate 必填 | 真实响应：`{case_id: [每一步的 {status, body}]}` |
| opposite | mutate 可选 | 同一接口相反结果的真实响应 `[{status, body}]`，被拒用例靠它查假绿 |
| source | 是 | 出处：缺陷单号、复盘记录、哪次评审漏的 |
| confirmed_by / confirmed_at | 是 | 谁确认了问题在哪、哪天 |
| expect | review 坏样本必填 | 应被发现的问题：`where`（case_id、函数名或 `文件:行`）+ `class`（`review_rules.md` 里的分类） |

## 运行

```powershell
python -m aigen eval                                   # 程序闸样本；check 会自动带上
python -m aigen eval --ai                              # 调模型审 review 样本（配置同 draft：AIGEN_LLM_*）
python -m aigen eval --ai --dry-run                    # 只生成提示词，交给 Cursor / Claude Code 等 Agent 新会话去审
python -m aigen eval --ai --answers 答案目录             # 读 <样本id>.json 答案打分
python -m aigen eval --ai --dry-run --dir aigen/examples/evals   # 用本目录的示例试一遍
```

`--ai` 的分数追加到 `docs/闸评测.md`。打分规则：坏样本按 `expect` 逐条算——审核员有一条发现的 `class` 相同、且 `where` 包含 `expect.where`，就算发现；干净样本里出现 `严重` 级别的发现算误报。

**干净样本也要人认真确认。** 如果需求写了提示文案，用例却只断言契约里的错误码、又没标待确认，审核员报「预期无依据」是对的。把这种用例标成 clean，会把正确发现算成误报。

## 什么时候加样本

- 闸放过、后来被人确认的问题：WF-4 查出的假绿、线上缺陷、`mutate` 报 `[WEAK]` 但闸当时放行了
- 审核员报了、但人判定是误报的：把那份用例做成 `clean` 样本
- 评审通过并已签字（`cases/approvals.json` 的 `files` 里该文件有 `"by"`）的用例，可以抽一些做 `clean` 样本
