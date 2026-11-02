def test_login_wrong_password(api):
    r = api.post("/users/login", json={"email": "${CUSTOMER_EMAIL}", "password": "wrong-password"})
    assert r.status_code == 401
