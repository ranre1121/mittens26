from fastapi.testclient import TestClient

from app import session
from app.api import create_app
from tests.conftest import API_KEY

CSRF = {"X-Requested-With": "kassa-control"}


def browser(make_settings, **overrides):
    """A client with no X-API-Key header, like the web interface."""
    client = TestClient(create_app(make_settings(**overrides)))
    client.__enter__()
    return client


def upload(client, video, headers=None):
    with open(video, "rb") as fh:
        return client.post(
            "/api/jobs", headers=headers or {}, files={"file": ("clip.mov", fh, "video/quicktime")}
        )


def test_interface_needs_login(make_settings):
    client = browser(make_settings)
    assert client.get("/api/session").status_code == 401
    assert client.get("/api/jobs").status_code == 401
    # Probes and the login endpoint stay reachable.
    assert client.get("/api/health").status_code == 200


def test_login_sets_cookie_and_unlocks_interface(make_settings, video):
    client = browser(make_settings)
    res = client.post("/api/session", json={"key": API_KEY})
    assert res.status_code == 200 and res.json()["method"] == "cookie"
    cookie = res.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
    assert "Max-Age=43200" in cookie  # 12 hours

    assert client.get("/api/session").json()["method"] == "cookie"
    assert client.get("/api/jobs").status_code == 200
    # Writes with the cookie need the CSRF header.
    assert upload(client, video).status_code == 403
    job_id = upload(client, video, CSRF).json()["job_id"]
    assert client.get(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/video").status_code == 200


def test_wrong_key_and_throttling(make_settings):
    client = browser(make_settings)
    assert client.post("/api/session", json={"key": "nope"}).status_code == 401
    for _ in range(session.LOGIN_ATTEMPTS - 1):
        client.post("/api/session", json={"key": "nope"})
    # Even the right key is refused once the window is used up.
    assert client.post("/api/session", json={"key": API_KEY}).status_code == 429


def test_jury_api_still_needs_the_header(make_settings):
    client = browser(make_settings)
    client.post("/api/session", json={"key": API_KEY})
    assert client.get("/api/v1/jobs/abc").status_code == 401
    assert client.get("/api/v1/jobs/abc", headers={"X-API-Key": API_KEY}).status_code == 404


def test_logout_and_tampered_or_expired_cookies(make_settings):
    client = browser(make_settings)
    client.post("/api/session", json={"key": API_KEY})
    assert client.delete("/api/session").status_code == 204
    client.cookies.clear()
    assert client.get("/api/jobs").status_code == 401

    key = session.signing_key("", (API_KEY,))
    token, _ = session.issue(key, 12)
    client.cookies.set(session.COOKIE_NAME, token[:-2] + "xx", path="/api")
    assert client.get("/api/jobs").status_code == 401

    expired, _ = session.issue(key, 1, now=0)
    client.cookies.set(session.COOKIE_NAME, expired, path="/api")
    assert client.get("/api/jobs").status_code == 401


def test_rotating_keys_ends_sessions(make_settings):
    client = browser(make_settings)
    client.post("/api/session", json={"key": API_KEY})
    token = client.cookies.get(session.COOKIE_NAME)
    rotated = browser(make_settings, api_keys=("new-key",))
    rotated.cookies.set(session.COOKIE_NAME, token, path="/api")
    assert rotated.get("/api/jobs").status_code == 401


def test_no_keys_means_local_only(make_settings):
    client = browser(make_settings, api_keys=())
    assert client.get("/api/session").json()["method"] == "local"
    assert client.post("/api/session", json={"key": "x"}).status_code == 400
