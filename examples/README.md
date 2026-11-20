# 迷你商城示例项目（完全离线可跑）

本目录模拟真实公司常见场景：
1. **非标文档**：`docs/prd_sample.md`（含未明确口径）、`docs/api_sample.md`（国内常见 HTTP 200 + `code: 0`、漏写部分错误码）；
2. **业务模块与上下游依赖**：`PROJECT_MAP.md` 示范如何划分模块、整理「登录 → 选品 → 加购 → 结算」的依赖矩阵；
3. **双轨自动化**：
   - `data/cart_add.yaml` + `tests/api/test_cart_data.py`：单接口数据驱动 + `xfail` 实测校准；
   - `tests/api/test_order_flow.py`：多步接口业务流 + `finally` 自动清理；
   - `pages/shop_page.py` + `tests/ui/test_checkout_ui.py`：Playwright UI 自动化（演示用**接口造加购数据 + 注入登录态**，直接测结算页，不靠 UI 一路硬点）。

## 一键运行体验

```bash
cd examples
pytest -v                        # 自动拉起内置迷你商城 mock_shop.py，跑通全部接口 + UI 用例
caseflow export                  # 反向扫描 data/ 与 tests/，生成 reports/用例表.csv 和 .md
caseflow mutate tests/api        # 故意改坏响应，检查接口断言是否防假绿
```

## 体验交互式抓 DOM（需要自己操作到带上游数据的页面时）

在一个终端启动示例靶场：

```bash
python mock_shop.py 8765
```

在另一个终端交互式抓取（先在弹出的浏览器里登录、加购，进购物车结算页后回终端输入页面名并回车，按 `q` 退出）：

```bash
caseflow dom http://127.0.0.1:8765/login -i
```
