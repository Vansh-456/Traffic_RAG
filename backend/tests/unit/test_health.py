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


def test_security_headers_present():
    with TestClient(create_app()) as client:
        r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_production_refuses_default_admin_token(monkeypatch):
    import pytest

    from app.config import get_settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_TOKEN", "change-me-admin-token")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="ADMIN_TOKEN"), TestClient(create_app()):
            pass
    finally:
        monkeypatch.delenv("APP_ENV")
        get_settings.cache_clear()
