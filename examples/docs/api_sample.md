# 迷你商城接口说明（模拟国内常见简陋接口文档）

> 约定：所有业务接口均返回 HTTP `200`（未登录返回 `401`），JSON 结构为 `{"code": 0, "msg": "ok", "data": ...}`；`code == 0` 表示成功。

## 1. 登录 `POST /api/login`
- 请求体：`{"username": "string", "password": "string"}`
- 成功响应：`{"code": 0, "msg": "ok", "data": {"token": "tok-..."}}`
- 失败响应：`{"code": 10001, "msg": "账号或密码错误", "data": null}`

## 2. 查询商品列表 `GET /api/products`
- 请求头：无需鉴权
- 成功响应：`{"code": 0, "msg": "ok", "data": [{"id": "p1", "name": "机械键盘", "price": 299, "stock": 10}]}`

## 3. 加入购物车 `POST /api/cart`
- 请求头：`Authorization: Bearer <token>`
- 请求体：`{"product_id": "string", "qty": int}`
- 成功响应：`{"code": 0, "msg": "ok", "data": {"items": [...], "total": 299}}`
- 错误码：
  - `10001`：数量必须大于 0
  - `10404`：商品不存在
  - （注：文档漏写了“库存不足”的错误码，可在测试里先用 `xfail` 实测校准）

## 4. 清空购物车 `DELETE /api/cart`
- 请求头：`Authorization: Bearer <token>`

## 5. 提交订单 `POST /api/orders`
- 请求头：`Authorization: Bearer <token>`
- 请求体：`{}`（自动结算当前购物车）
- 成功响应：`{"code": 0, "msg": "ok", "data": {"id": "ord-1", "total": 299, "status": "CREATED"}}`
- 错误码：`10005`（购物车为空）

## 6. 查询 / 清理订单
- `GET /api/orders/{id}`：查询订单详情
- `DELETE /api/orders/{id}`：清理测试订单
