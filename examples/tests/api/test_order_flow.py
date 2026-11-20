"""多步接口业务流示例：原生 pytest + 上下游数据传递 + finally 清理。"""
from caseflow import client


def test_order_lifecycle(api_as, sample_product):
    """TC-ORDER-01 加购→下单→查单→清理"""
    api = api_as("user")
    order_id = None
    try:
        api.delete("/api/cart").expect_ok()
        api.post("/api/cart", json={"product_id": sample_product["id"], "qty": 1}).expect_ok()

        created = api.post("/api/orders", json={}).expect_ok().expect("data.status", eq="CREATED")
        order_id = created.data["id"]

        api.get(f"/api/orders/{order_id}").expect_ok().expect("data.id", eq=order_id)
        api.get("/api/cart").expect_ok().expect("data.items", len=0)
    finally:
        if order_id:
            api.delete(f"/api/orders/{order_id}")
        api.delete("/api/cart")


def test_order_rejects_empty_cart(api_as):
    """TC-ORDER-02 空购物车不允许提交订单"""
    api = api_as("user")
    api.delete("/api/cart").expect_ok()
    api.post("/api/orders", json={}).expect_status(200).expect_code(10005).expect("msg", contains="为空").expect("data", type="null")
