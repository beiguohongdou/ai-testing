"""示例项目公共前置：自动拉起本地迷你商城 + 提供跨模块上下游依赖 fixtures。"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

# 无论从根目录还是 examples/ 运行 pytest，都能锁定 examples/ 为当前 caseflow 项目
os.environ.setdefault("CASEFLOW_PROJECT", str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from caseflow import config, runtime
from caseflow.ui import inject_storage
from mock_shop import make_server

pytest_plugins = ["caseflow.plugin"]


@pytest.fixture(scope="session", autouse=True)
def shop_server():
    """自动启动本地靶场并注入地址与测试账号。"""
    os.environ.setdefault("USER_NAME", "demo_user")
    os.environ.setdefault("USER_PASSWORD", "Demo#1234")
    srv, _ = make_server(0)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    os.environ["API_BASE_URL"] = url
    os.environ["UI_BASE_URL"] = url
    config.reset()
    runtime.reset_clients()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield url
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="session")
def sample_product(shop_server):
    """上游商品信息：供购物车与下单用例共用。"""
    resp = runtime.client(None).get("/api/products").expect_ok().expect("data", len_gt=0)
    return resp.data[0]


@pytest.fixture
def cart_with_item(shop_server, sample_product):
    """上游造数：先清空再接口加购 1 件商品，结束后自动清空（避免残留影响其他用例）。"""
    api = runtime.client("user")
    api.delete("/api/cart").expect_ok()
    api.post("/api/cart", json={"product_id": sample_product["id"], "qty": 1}).expect_ok()
    yield sample_product
    try:
        api.delete("/api/cart")
    except Exception:
        pass


@pytest.fixture
def logged_in_page(page, shop_server):
    """接口登录拿 Token 直接写进浏览器 localStorage，UI 用例免走登录页。"""
    api = runtime.client("user")
    token = api.session.headers["Authorization"].removeprefix("Bearer ").strip()
    inject_storage(page, shop_server, local_storage={"token": token})
    return page
