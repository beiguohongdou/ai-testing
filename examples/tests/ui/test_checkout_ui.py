"""UI 自动化示例：演示接口造上游数据 + 注入登录态 → UI 直接进结算页，解决上游断链。"""
from playwright.sync_api import expect

from pages.shop_page import CartPage


def test_empty_cart_hides_checkout(logged_in_page, shop_server, api_as):
    """TC-UI-01 空购物车不显示结算按钮"""
    api_as("user").delete("/api/cart").expect_ok()
    cart = CartPage(logged_in_page, shop_server)
    cart.open()
    expect(cart.empty_hint()).to_contain_text("购物车为空")
    expect(cart.checkout_btn()).to_have_count(0)


def test_checkout_with_api_seeded_cart(logged_in_page, shop_server, cart_with_item, api_as):
    """TC-UI-02 接口预置加购后，UI 直接完成结算下单"""
    cart = CartPage(logged_in_page, shop_server)
    cart.open()
    expect(cart.total()).to_have_text(str(cart_with_item["price"]))
    cart.checkout()
    expect(cart.order_success()).to_have_text("下单成功")
    order_id = cart.order_id().inner_text()
    assert order_id.startswith("ord-")
    api_as("user").delete(f"/api/orders/{order_id}")
