"""单接口数据驱动示例：只写 3 行绑定到 data/cart_add.yaml。"""
import pytest

from caseflow import load_cases, run_case


@pytest.mark.parametrize("case", load_cases("data/cart_add.yaml"))
def test_cart_add(case, api_as):
    try:
        run_case(case)
    finally:
        if case.expect.get("ok"):
            api_as("user").delete("/api/cart")
