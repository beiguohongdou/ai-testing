"""迷你商城页面对象：只用稳定定位器（data-testid / role），绝不写 time.sleep。"""
from __future__ import annotations

from playwright.sync_api import Page, expect


class LoginPage:
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url.rstrip("/")

    def open(self):
        self.page.goto(f"{self.base_url}/login")

    def login(self, user: str, password: str):
        self.page.get_by_test_id("username").fill(user)
        self.page.get_by_test_id("password").fill(password)
        self.page.get_by_test_id("login-btn").click()


class ShopPage:
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url.rstrip("/")

    def open(self):
        self.page.goto(f"{self.base_url}/shop")

    def add_item(self, product_id: str):
        self.page.get_by_test_id(f"add-{product_id}").click()
        expect(self.page.get_by_test_id("toast")).to_contain_text("已加入购物车")

    def go_cart(self):
        self.page.get_by_test_id("go-cart").click()


class CartPage:
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url.rstrip("/")

    def open(self):
        self.page.goto(f"{self.base_url}/cart")

    def empty_hint(self):
        return self.page.get_by_test_id("empty-cart")

    def total(self):
        return self.page.get_by_test_id("cart-total")

    def checkout_btn(self):
        return self.page.get_by_test_id("checkout-btn")

    def checkout(self):
        self.checkout_btn().click()

    def order_success(self):
        return self.page.get_by_test_id("order-success")

    def order_id(self):
        return self.page.get_by_test_id("order-id")
