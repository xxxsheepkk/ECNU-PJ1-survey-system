import re

from tests.helpers import register


def test_csrf_is_required_when_enabled(client, app):
    app.config["CSRF_ENABLED"] = True

    response = client.post(
        "/register",
        data={
            "username": "alice",
            "password": "secret123",
            "confirm_password": "secret123",
        },
    )
    assert response.status_code == 400
    assert "CSRF" in response.get_data(as_text=True)

    page = client.get("/register").get_data(as_text=True)
    match = re.search(r'name="_csrf" value="([^"]+)"', page)
    assert match is not None
    token = match.group(1)

    response = client.post(
        "/register",
        data={
            "_csrf": token,
            "username": "alice",
            "password": "secret123",
            "confirm_password": "secret123",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "注册成功" in response.get_data(as_text=True)
