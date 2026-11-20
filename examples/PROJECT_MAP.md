# PROJECT_MAP（迷你商城示例项目地图）

> 新开 AI 会话时 @ 本文件 + 当前需求，避免 AI 重复造轮子、写断上下游依赖。

## 被测系统
- 名称 / 地址：迷你商城靶场（`mock_shop.py`，本地自动拉起）
- 接口风格：HTTP 200 + `{"code": 0, "msg": "ok", "data": ...}`（见 `caseflow.yaml` `api.envelope`）
- 登录方式：`POST /api/login` 拿 Bearer Token；浏览器端存在 `localStorage['token']`

## 业务模块划分
| 模块 | 职责 | 页面 URL | 接口前缀 | 优先级 |
|------|------|----------|----------|--------|
| 登录鉴权 | 账号密码登录、发放 Token | `/login` | `/api/login` | P0 |
| 商品浏览 | 商品列表展示与选品 | `/shop` | `/api/products` | P1 |
| 购物车 | 加购、参数校验、清空 | `/cart` | `/api/cart` | P0 |
| 结算下单 | 从购物车提交订单、查单与清理 | `/cart` | `/api/orders` | P0 |

## 模块上下游依赖矩阵
| 下游模块 | 依赖上游 | 需要的状态 / 数据契约 | 复用方式（登录态 / 接口造数 fixture / UI 前置） |
|----------|----------|----------------------|------------------------------------------------|
| 购物车加购 | 登录鉴权 + 商品浏览 | 有效 `Authorization` Token + 存在的 `product_id` | 接口 `client("user")` 自动复用登录 + `sample_product` fixture |
| 结算下单(API) | 登录鉴权 + 购物车 | 购物车内至少有 1 件商品 | 用例内先调 `POST /api/cart`，`finally` 中 `DELETE /api/orders/{id}` |
| 结算下单(UI) | 登录鉴权 + 购物车有商品 | 浏览器 `localStorage.token` + 服务端购物车非空（空购物车不渲染结算按钮） | `logged_in_page` 注入 Token + `cart_with_item` 接口前置加购 |

## 公共前置 Fixtures（conftest.py）
| fixture 名 | 产出 | 供哪些模块用 |
|------------|------|--------------|
| `shop_server` | 自动拉起随机端口本地商城并注入配置 | 全部 API / UI 用例 |
| `sample_product` | 首个可用商品信息 `{"id", "name", "price"}` | 购物车、下单模块 |
| `cart_with_item` | 先清空再接口加购 1 件商品，结束后自动清空 | 结算下单 UI / API 模块 |
| `logged_in_page` | 已注入接口登录 Token 的 Playwright `Page` | 全部登录后 UI 模块 |

## 已有 Page Object（pages/）
| 类 | 文件 | 页面 | 主要方法 |
|----|------|------|----------|
| `LoginPage` | `pages/shop_page.py` | `/login` | `login(user, pwd)` |
| `ShopPage` | `pages/shop_page.py` | `/shop` | `open()`, `add_item(pid)`, `go_cart()` |
| `CartPage` | `pages/shop_page.py` | `/cart` | `open()`, `checkout()`, 断言定位器 `empty_hint()` / `total()` / `order_success()` |

## 已有数据文件（data/）与用例
| 文件 / 测试 | 覆盖什么 | 状态 |
|-------------|----------|------|
| `data/cart_add.yaml` + `tests/api/test_cart_data.py` | 加入购物车正常/边界/异常 | 已跑通（含 1 条 xfail 实测校准演示） |
| `tests/api/test_order_flow.py` | 加购→下单→查单→清理多步流 | 已跑通 |
| `tests/ui/test_checkout_ui.py` | 空购物车 vs 上游接口造数后 UI 结算 | 已跑通 |
