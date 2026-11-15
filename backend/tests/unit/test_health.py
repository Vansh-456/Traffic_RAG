from fastapi.testclient import TestClient

from app.main import create_app


def test_health_reports_features():
    with TestClient(create_app()) as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "llm" in body["features"]
    assert r.headers.get("x-request-id")


def test_metrics_endpoint():
    with TestClient(create_app()) as client:
        r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_request_seconds" in r.text
