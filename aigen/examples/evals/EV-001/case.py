def test_rejected(api):
    # 坏样本：只断言了状态码。同一个状态码也可能是错误页，闸应判成假绿。
    r = api.post("/resources", json={"name": "${NAME}"})
    assert r.status_code == 400
