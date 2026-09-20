from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_parse_empty():
    response = client.post("/api/parse", json={"input": "  "})
    assert response.status_code == 400
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "EMPTY_INPUT"


def test_parse_unsupported():
    response = client.post("/api/parse", json={"input": "https://www.youtube.com/watch?v=1"})
    assert response.status_code == 400
    assert response.json()["code"] == "UNSUPPORTED_PLATFORM"


def test_missing_token():
    response = client.get("/api/download/not-a-token")
    assert response.status_code == 404
    assert response.json()["message"] == "下载失败，请重新解析"
