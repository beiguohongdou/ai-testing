"""迷你商城本地靶场（纯 Python 标准库）：同时提供 JSON API 与真实交互 HTML 页面。

可单独启动用于体验 `caseflow dom -i`：
    python mock_shop.py 8765
跑 pytest 时会由 conftest.py 在后台自动拉起。
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ShopState:
    def __init__(self):
        self.users = {"demo_user": "Demo#1234"}
        self.tokens = {"tok-demo-user": "demo_user"}
        self.products = [
            {"id": "p1", "name": "机械键盘", "price": 299, "stock": 10},
            {"id": "p2", "name": "无线鼠标", "price": 99, "stock": 5},
        ]
        self.carts: dict[str, list[dict]] = {}
        self.orders: dict[str, dict] = {}
        self.seq = 0


_LOGIN_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>登录 - 迷你商城</title></head>
<body>
  <h1>商城登录</h1>
  <label>用户名 <input id="username" data-testid="username" placeholder="请输入用户名" /></label>
  <label>密码 <input id="password" data-testid="password" type="password" placeholder="请输入密码" /></label>
  <button data-testid="login-btn" onclick="doLogin()">登录</button>
  <div data-testid="login-error" style="color:red"></div>
  <script>
    async function doLogin() {
      const r = await fetch('/api/login', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: document.getElementById('username').value, password: document.getElementById('password').value})
      });
      const j = await r.json();
      if (j.code === 0) { localStorage.setItem('token', j.data.token); location.href = '/shop'; }
      else { document.querySelector('[data-testid="login-error"]').textContent = j.msg; }
    }
  </script>
</body></html>"""

_SHOP_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>商品列表 - 迷你商城</title></head>
<body>
  <h1>商品列表</h1>
  <a href="/cart" data-testid="go-cart">去购物车</a>
  <div id="products"></div>
  <div data-testid="toast"></div>
  <script>
    async function load() {
      const r = await fetch('/api/products');
      const j = await r.json();
      document.getElementById('products').innerHTML = j.data.map(p =>
        `<div><span>${p.name} ¥${p.price}</span> <button data-testid="add-${p.id}" onclick="add('${p.id}')">加入购物车</button></div>`
      ).join('');
    }
    async function add(pid) {
      const tok = localStorage.getItem('token') || '';
      const r = await fetch('/api/cart', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok},
        body: JSON.stringify({product_id: pid, qty: 1})
      });
      const j = await r.json();
      document.querySelector('[data-testid="toast"]').textContent = j.code === 0 ? '已加入购物车' : j.msg;
    }
    load();
  </script>
</body></html>"""

_CART_HTML = """<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>购物车与结算 - 迷你商城</title></head>
<body>
  <h1>我的购物车</h1>
  <a href="/shop" data-testid="back-shop">继续逛逛</a>
  <div id="area">加载中...</div>
  <script>
    async function loadCart() {
      const tok = localStorage.getItem('token') || '';
      const r = await fetch('/api/cart', {headers: {'Authorization': 'Bearer ' + tok}});
      const j = await r.json();
      const area = document.getElementById('area');
      if (j.code !== 0) { area.innerHTML = `<p data-testid="empty-cart">${j.msg}</p>`; return; }
      if (!j.data.items.length) {
        area.innerHTML = `<p data-testid="empty-cart">购物车为空</p>`;
        return;
      }
      const rows = j.data.items.map(i => `<li>${i.name} × ${i.qty} = ¥${i.subtotal}</li>`).join('');
      area.innerHTML = `<ul>${rows}</ul><p>合计：<strong data-testid="cart-total">${j.data.total}</strong></p>
        <button data-testid="checkout-btn" onclick="checkout()">去结算</button>`;
    }
    async function checkout() {
      const tok = localStorage.getItem('token') || '';
      const r = await fetch('/api/orders', {method: 'POST', headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + tok}, body: '{}'});
      const j = await r.json();
      if (j.code === 0) {
        document.getElementById('area').innerHTML =
          `<p data-testid="order-success">下单成功</p><span data-testid="order-id">${j.data.id}</span>`;
      }
    }
    loadCart();
  </script>
</body></html>"""


def make_server(port: int = 0) -> tuple[ThreadingHTTPServer, ShopState]:
    state = ShopState()
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, status: int, data):
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _html(self, html: str):
            raw = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _user(self) -> str | None:
            auth = self.headers.get("Authorization", "")
            tok = auth.removeprefix("Bearer ").strip()
            return state.tokens.get(tok)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length", "0") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}

        def _cart_view(self, user: str) -> dict:
            items = state.carts.get(user, [])
            return {"items": items, "total": sum(x["subtotal"] for x in items)}

        def do_GET(self):
            if self.path == "/login":
                return self._html(_LOGIN_HTML)
            if self.path == "/shop":
                return self._html(_SHOP_HTML)
            if self.path == "/cart":
                return self._html(_CART_HTML)
            if self.path == "/api/products":
                return self._json(200, {"code": 0, "msg": "ok", "data": state.products})
            if self.path == "/api/cart":
                user = self._user()
                if not user:
                    return self._json(401, {"code": 401, "msg": "请先登录", "data": None})
                return self._json(200, {"code": 0, "msg": "ok", "data": self._cart_view(user)})
            if self.path.startswith("/api/orders/"):
                user = self._user()
                if not user:
                    return self._json(401, {"code": 401, "msg": "请先登录", "data": None})
                oid = self.path.rsplit("/", 1)[-1]
                order = state.orders.get(oid)
                if not order:
                    return self._json(200, {"code": 10404, "msg": "订单不存在", "data": None})
                return self._json(200, {"code": 0, "msg": "ok", "data": order})
            return self._json(404, {"code": 404, "msg": "not found"})

        def do_POST(self):
            body = self._body()
            if self.path == "/api/login":
                u, p = body.get("username", ""), body.get("password", "")
                if state.users.get(u) == p:
                    return self._json(200, {"code": 0, "msg": "ok", "data": {"token": "tok-demo-user"}})
                return self._json(200, {"code": 10001, "msg": "账号或密码错误", "data": None})
            user = self._user()
            if not user:
                return self._json(401, {"code": 401, "msg": "请先登录", "data": None})
            if self.path == "/api/cart":
                pid, qty = body.get("product_id"), body.get("qty", 0)
                prod = next((x for x in state.products if x["id"] == pid), None)
                if prod is None:
                    return self._json(200, {"code": 10404, "msg": "商品不存在", "data": None})
                if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0:
                    return self._json(200, {"code": 10001, "msg": "数量必须大于 0", "data": None})
                if qty > prod["stock"]:
                    return self._json(200, {"code": 10003, "msg": "库存不足", "data": None})
                with lock:
                    cart = state.carts.setdefault(user, [])
                    cart.append({"product_id": pid, "name": prod["name"], "qty": qty, "subtotal": prod["price"] * qty})
                return self._json(200, {"code": 0, "msg": "ok", "data": self._cart_view(user)})
            if self.path == "/api/orders":
                with lock:
                    items = list(state.carts.get(user, []))
                    if not items:
                        return self._json(200, {"code": 10005, "msg": "购物车为空", "data": None})
                    state.seq += 1
                    oid = f"ord-{state.seq}"
                    order = {"id": oid, "items": items, "total": sum(x["subtotal"] for x in items), "status": "CREATED"}
                    state.orders[oid] = order
                    state.carts[user] = []
                return self._json(200, {"code": 0, "msg": "ok", "data": order})
            return self._json(404, {"code": 404, "msg": "not found"})

        def do_DELETE(self):
            user = self._user()
            if not user:
                return self._json(401, {"code": 401, "msg": "请先登录", "data": None})
            if self.path == "/api/cart":
                state.carts[user] = []
                return self._json(200, {"code": 0, "msg": "cleared", "data": None})
            if self.path.startswith("/api/orders/"):
                oid = self.path.rsplit("/", 1)[-1]
                state.orders.pop(oid, None)
                return self._json(200, {"code": 0, "msg": "deleted", "data": None})
            return self._json(404, {"code": 404, "msg": "not found"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler), state


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv, _ = make_server(p)
    print(f"迷你商城已启动：http://127.0.0.1:{p}/login （账号 demo_user / Demo#1234，按 Ctrl+C 停止）")
    srv.serve_forever()
