from app.models import User
from app.settings import settings


def test_local_workspace_opens_without_cookie_and_disables_login(environment, monkeypatch):
    client, sessions = environment
    with sessions() as db:
        user = User(username="workspace", password_hash="login-disabled", role="admin")
        db.add(user)
        db.commit()
        workspace_id = user.id
    monkeypatch.setattr(settings, "auth_mode", "local")
    client.cookies.clear()
    response = client.get("/api/v1/session")
    assert response.status_code == 200
    assert response.json()["id"] == workspace_id
    assert response.json()["auth_mode"] == "local"
    assert not response.cookies
    assert client.get("/api/v1/projects").json() == []
    created = client.post("/api/v1/projects", json={
        "name": "Community test", "brand": "Example", "product": "Test drink",
        "platform": "xiaohongshu", "phase": "launch", "mode": "trial",
    })
    assert created.status_code == 201
    assert client.get(f"/api/v1/projects/{created.json()['id']}").status_code == 200
    assert client.post("/api/v1/sessions", json={"username": "x", "password": "x"}).status_code == 404
    assert client.post("/api/v1/projects", headers={"Origin": "https://untrusted.example"}, json={}).status_code == 403


def test_local_workspace_requires_explicit_database_initialization(environment, monkeypatch):
    client, _ = environment
    monkeypatch.setattr(settings, "auth_mode", "local")
    client.cookies.clear()
    response = client.get("/api/v1/session")
    assert response.status_code == 503 and response.json()["code"] == "SETUP_REQUIRED"
