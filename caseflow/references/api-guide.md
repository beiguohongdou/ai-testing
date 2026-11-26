# 轨道 B：接口自动化指引（数据驱动 + 多步流 + 深度测试）

> 完整代码范例见 `examples/data/cart_add.yaml`、`examples/tests/api/test_cart_data.py`、`examples/tests/api/test_order_flow.py`。

## 1. 选对形态（YAML 红线）

- **单接口多组场景（正例 / 边界 / 异常参数）→ 走 `data/*.yaml`**：
  - 条件：单接口 + 无分支逻辑 + 单条数据清晰简洁。支持顶层 `base_json` + 用例 `drop: [字段]` 测必填缺失，避免重复抄写大段字段。
  - 在 `tests/api/test_xxx.py` 只写 `@pytest.mark.parametrize("case", load_cases("data/xxx.yaml"))` + `run_case(case)`；改完 YAML 直接 `pytest`，**无需编译生成步骤**。
  - YAML 动态占位符：`${unique}`、`${phone}`、`${timestamp}` / `${timestamp:ms}`、`${today:-1d}`、`${now}`、`${len:255}`、`${alpha:8}`、`${digits:6}`，同条用例复用写进 `vars`。
- **多步业务流（建单→支付→查单→清理）/ 需要条件分支或加解密 → 直接写原生 Python**：
  - 按业务模块用 `class TestXxx:` 分组，每条 `test_*` 只聚焦一个清晰业务场景（docstring 第一行写 `编号 标题`）。
  - **无前后状态因果依赖禁止合并 `def`**：只有存在强状态串联的闭环流程（如建单→支付→查单、错密锁号→管理员解锁→恢复登录）才写在同一 `def` 内；互不依赖的只读接口、多维筛选条件、正反边界必须拆为独立 `test_*` 或使用 `@pytest.mark.parametrize`（若函数体里出现 `# 1) ... # 2) ...` 且彼此不传递状态变量，必须拆分）。
  - **按作用域分层放置 `conftest.py` 与 `utils/`**：
    - 根目录 `conftest.py`：放全局基础设施与跨 UI/API 共享的公共 fixture（如浏览器配置、`page_as`、账号凭据、跨层共享只读样本等，可按项目需要扩展）；
    - `tests/api/conftest.py` / `tests/ui/conftest.py`：放仅限单层使用的造数工厂、数据清理、状态恢复等 fixture；
    - `utils/`：放偏底层的无 fixture 语义纯工具函数（如 JWT 解码与自动续期、TOTP 计算、加解密/签名、日期/数据格式转换等）；
    - **清理统一进 `fixture` teardown（`yield`）**：在对应作用域的 `conftest.py` 编写清理/恢复型 `yield` fixture（显式用 `# setup` / `# teardown` 注释标明前置与后置；清理失败可忽略或打警告，不掩盖主流程失败），避免在用例体内手写 `try ... finally`。
  - 使用 `api = client("user")`（进程内按角色自动复用登录，不触发限流）。
  - Python 代码直接 `from caseflow import gen` 调用 `gen.unique()`、`gen.today("-18y+1d")`、`gen.phone()`、`gen.text(251)`，不必绕弯调用 `fill("${unique}", {})`。
  - 链式断言：`resp.expect_ok().expect("data.id", exists=True)` 或 `resp.expect_code(10001)`（兼容国内 HTTP 200 + `body.code` 信封）。

## 2. 文档漏写错误码 / 已知缺陷 → `xfail` 与 `xfail_strict`

- 遇到文档漏写错误码、待实测校准的用例：YAML 写 `xfail: 待实测校准`（`strict=False`）先打一枪测试环境，核对后再固化预期。
- 确认是**被测系统真缺陷**：YAML 写 `xfail_strict: 缺陷说明`（或 Python 加 `@pytest.mark.xfail(strict=True, reason=...)`），缺陷修复后自动变 XPASS 提醒移除标记。

## 3. 进阶深度测试（不变量 / 差分对比 / 越权基线）

针对核心交易、账务、权限模块，可按需补充以下高价值测试（范例见 `examples/tests/api/`）：
- **性质测试（Property-Based）**：用随机组合输入验证业务不变量（如：任意加购/删减序列后，订单总价始终等于明细单价×数量之和且非负）。
- **差分对比（Differential）**：用 `from caseflow.runtime import diff_response` 对比新旧接口或双环境响应差异，配置 `ignore_keys=("timestamp", "trace_id", "request_id")` 忽略动态字段。
- **跨角色越权检查（IDOR）**：用 `api_as("user")` 探测管理端或他人资源 ID 接口，验证返回 401/403/10403 而非越权成功。
