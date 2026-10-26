import pytest
from fastapi.testclient import TestClient

from shortener.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app(), follow_redirects=False)


def test_health(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_shorten_redirect_stats_delete_flow(client):
    r = client.post("/shorten", json={"url": "https://example.com/page"})
    assert r.status_code == 201
    code = r.json()["code"]
    redirect = client.get(f"/{code}")
    assert redirect.status_code == 302 and redirect.headers["location"] == "https://example.com/page"
    assert client.get(f"/stats/{code}").json()["total_clicks"] == 1
    assert client.delete(f"/links/{code}").status_code == 204
    assert client.get(f"/{code}").status_code == 404


def test_error_mapping(client):
    assert client.post("/shorten", json={"url": "ftp://x.com"}).status_code == 422
    client.post("/shorten", json={"url": "https://example.com", "custom_alias": "dup-1"})
    assert client.post("/shorten", json={"url": "https://example.org", "custom_alias": "dup-1"}).status_code == 409
    assert client.get("/stats/nope123").status_code == 404
