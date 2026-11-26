# Step 4 · 排错、防篡改门禁、防假绿与用例导出

## 1. 排错 4 条规矩

1. **先排环境**：大面积超时或 `ConnectionRefused / 502 / 503` → 先跑 `pytest -m smoke`，冒烟不过立刻停止，不乱改用例代码。
2. **禁止偷偷改预期作弊（红线）**：AI 修复报错时，**严禁为了测试变绿擅自放宽或删掉 `expect` / `assert` 断言**！实际输出与预期不一致时，只准列出来让用户判定：
   - 需求口径变了 → 经用户同意后更新用例预期；
   - 被测系统真 Bug → 标 `@pytest.mark.xfail(strict=True, reason="缺陷号/现象")` 留痕，直接把报错附带的 `[复现] curl` 或 `reports/ui/*.md` 诊断文件丢给开发。
3. **小步最小修改**：只改报错相关的函数/数据行，改完立刻重跑该文件验证。
4. **UI 失败先看诊断包**：`caseflow.plugin` 已自动把失败截图、控制台报错和 HTTP ≥ 400 接口请求写到 `reports/ui/`，先看接口再看控制台最后查定位器。

## 2. 防篡改、防假绿与独立复核

- **防改断言硬门禁**：`caseflow guard` 会扫描 `git diff` 中对 `tests/` 和 `data/` 的改动，拦截擅自删减断言、放宽预期或私加 `skip`/`xfail` 的行为；人工确认过的需求变更可加 `--allow` 放行。
- **防假绿检查**：
  - `caseflow mutate tests/api`：故意改坏响应体或翻转状态码/业务码，揪出「只检查 HTTP 200」的假绿用例。
  - `caseflow mutate --changed`：仅对 `git diff` 涉及的接口测试文件跑变异检查（大项目提速）。
  - `caseflow mutate --observe`：只输出 `[WEAK]` 报告不阻断（返回 0），适合存量老项目渐进治理。
- **独立上下文复核**：单模块写完并跑通后，建议新开一个干净会话（或只读 Subagent），只传入 `git diff` + `reports/` 结果，专门核查“是否有假绿断言、是否漏测核心异常、定位器是否脆弱”。

## 3. 反向导出用例表

- 运行 `caseflow export`，自动扫描真正在跑的 `data/*.yaml` 和 `tests/**/test_*.py`，在 `reports/` 生成最新 `用例表.csv`（Excel 直接打开）和 `用例表.md`。
