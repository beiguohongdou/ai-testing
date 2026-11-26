"""高阶深度测试范例：业务不变量（性质测试思路）、差分对比与未授权基线检查。"""
import random

from caseflow import diff_response


def test_cart_total_invariant(api_as, sample_product):
    """TC-INV-01 随机加购/清空序列下，购物车总价始终等于单价×数量且非负（进阶可接入 hypothesis）"""
    api = api_as("user")
    rng = random.Random(42)
    try:
        api.delete("/api/cart").expect_ok()
        expected_qty = 0
        for _ in range(5):
            if rng.choice(["add", "add", "clear"]) == "add":
                delta = rng.randint(1, 3)
                api.post("/api/cart", json={"product_id": sample_product["id"], "qty": delta}).expect_ok()
                expected_qty += delta
            else:
                api.delete("/api/cart").expect_ok()
                expected_qty = 0

            cart = api.get("/api/cart").expect_ok().data
            actual_qty = sum(item["qty"] for item in cart["items"])
            assert actual_qty == expected_qty >= 0
            assert cart["total"] == expected_qty * sample_product["price"]
    finally:
        api.delete("/api/cart")


def test_cart_idempotent_read_diff(api_as):
    """TC-DIFF-01 差分对比示例：验证同一请求（或双环境/新旧版本）响应结构与核心字段完全一致"""
    api = api_as("user")
    r1 = api.get("/api/cart").expect_ok().expect("data.items", type="list")
    r2 = api.get("/api/cart").expect_ok()
    diff_response(r1, r2, ignore_keys=("timestamp", "trace_id", "request_id"))


def test_unauthorized_access_blocked(api):
    """TC-SEC-01 匿名态访问受保护资源必须被拒绝（可扩展为普通用户越权访问他人/管理员资源）"""
    api.post("/api/cart", json={"product_id": "1", "qty": 1}).expect_status(401).expect_code(401).expect("msg", exists=True)
    api.post("/api/orders", json={}).expect_status(401).expect_code(401).expect("msg", exists=True)
