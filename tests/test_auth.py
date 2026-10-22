from tests.helpers import create_survey, login, logout, register


def test_register_login_and_logout(client, db):
    response = register(client, "alice", "secret123")
    assert "注册成功" in response.get_data(as_text=True)
    assert db.users.count_documents({"username": "alice"}) == 1

    logout(client)
    response = login(client, "alice", "wrong-password")
    assert "用户名或密码错误" in response.get_data(as_text=True)

    response = login(client, "alice", "secret123")
    assert "欢迎回来" in response.get_data(as_text=True)


def test_user_only_sees_own_surveys(client, db):
    register(client, "alice")
    survey = create_survey(client, db, title="Alice 的问卷")
    logout(client)

    register(client, "bob")
    dashboard = client.get("/").get_data(as_text=True)
    assert "Alice 的问卷" not in dashboard

    response = client.get(f"/surveys/{survey['_id']}")
    assert response.status_code == 404
    response = client.get(f"/surveys/{survey['_id']}/stats")
    assert response.status_code == 404
