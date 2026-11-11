"""Unit tests for OAuth and session management (api/auth.py).

All tests are strictly offline and make zero external network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from api.auth import (
    SessionManager,
    User,
    build_github_auth_url,
    build_google_auth_url,
    clear_session_cookie,
    get_current_user_from_request,
    set_session_cookie,
)
from fastapi import Request, Response


def test_session_manager_sign_and_verify() -> None:
    sm = SessionManager("test-secret-key-12345")
    user = User(
        user_id="user_gh_1234",
        email="engineer@example.com",
        name="Hardware Hacker",
        avatar_url="https://avatars.example.com/u/1234",
        provider="github",
        role="user",
    )

    token = sm.create_token(user, expires_in_s=3600)
    assert isinstance(token, str)
    assert "." in token

    verified = sm.verify_token(token)
    assert verified is not None
    assert verified.user_id == user.user_id
    assert verified.email == user.email
    assert verified.name == user.name
    assert verified.avatar_url == user.avatar_url
    assert verified.provider == user.provider


def test_session_manager_rejects_tampered_token() -> None:
    sm = SessionManager("test-secret-key-12345")
    user = User(
        user_id="user_1",
        email="a@b.com",
        name="A",
        avatar_url="",
        provider="mock",
    )

    token = sm.create_token(user, expires_in_s=3600)
    payload, sig = token.split(".", 1)
    tampered = f"{payload}x.{sig}"

    assert sm.verify_token(tampered) is None
    assert sm.verify_token("invalid-token") is None
    assert sm.verify_token("") is None


def test_session_manager_rejects_expired_token() -> None:
    sm = SessionManager("test-secret-key-12345")
    user = User(
        user_id="user_1",
        email="a@b.com",
        name="A",
        avatar_url="",
        provider="mock",
    )

    # Token expired 10 seconds ago
    token = sm.create_token(user, expires_in_s=-10)
    assert sm.verify_token(token) is None


def test_request_user_extraction_from_cookie_and_bearer() -> None:
    sm = SessionManager("test-secret-key-12345")
    user = User(
        user_id="user_2",
        email="dev@example.com",
        name="Dev User",
        avatar_url="",
        provider="google",
    )
    token = sm.create_token(user, expires_in_s=3600)

    # 1. From cookie
    req_cookie = MagicMock(spec=Request)
    req_cookie.cookies = {"sparkplan_session": token}
    req_cookie.headers = {}
    assert get_current_user_from_request(req_cookie, sm) == user

    # 2. From Authorization header
    req_bearer = MagicMock(spec=Request)
    req_bearer.cookies = {}
    req_bearer.headers = {"Authorization": f"Bearer {token}"}
    assert get_current_user_from_request(req_bearer, sm) == user

    # 3. Unauthenticated
    req_empty = MagicMock(spec=Request)
    req_empty.cookies = {}
    req_empty.headers = {}
    assert get_current_user_from_request(req_empty, sm) is None


def test_oauth_auth_urls() -> None:
    gh_url = build_github_auth_url(
        client_id="gh_id_123",
        redirect_uri="https://api.sparkplan.io/auth/callback/github",
        state="xyz_state",
    )
    assert "https://github.com/login/oauth/authorize?" in gh_url
    assert "client_id=gh_id_123" in gh_url
    assert "state=xyz_state" in gh_url

    google_url = build_google_auth_url(
        client_id="goog_id_456",
        redirect_uri="https://api.sparkplan.io/auth/callback/google",
        state="abc_state",
    )
    assert "https://accounts.google.com/o/oauth2/v2/auth?" in google_url
    assert "client_id=goog_id_456" in google_url
    assert "state=abc_state" in google_url


def test_set_and_clear_session_cookies() -> None:
    resp = Response()
    set_session_cookie(resp, "test-token-value")
    # Response headers set-cookie
    set_cookie = resp.headers.get("set-cookie", "")
    assert "sparkplan_session=test-token-value" in set_cookie
    assert "HttpOnly" in set_cookie

    resp_clear = Response()
    clear_session_cookie(resp_clear)
    clear_cookie = resp_clear.headers.get("set-cookie", "")
    assert "sparkplan_session=" in clear_cookie


def test_auth_api_endpoints() -> None:
    from api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()
    client = TestClient(app)

    # 1. Providers endpoint
    providers_resp = client.get("/auth/providers")
    assert providers_resp.status_code == 200
    providers = providers_resp.json()
    assert "github" in providers
    assert "google" in providers
    assert providers["mock"] is True

    # 2. Initially unauthenticated
    me_anon = client.get("/auth/me")
    assert me_anon.status_code == 200
    assert me_anon.json() is None

    # 3. Mock login
    login_resp = client.post("/auth/mock-login")
    assert login_resp.status_code == 200
    user_info = login_resp.json()
    assert user_info["user_id"] == "mock_dev_user"
    assert user_info["email"] == "developer@sparkplan.local"

    # 4. Check /auth/me retains cookie session
    me_auth = client.get("/auth/me")
    assert me_auth.status_code == 200
    assert me_auth.json()["user_id"] == "mock_dev_user"

    # 5. Logout
    logout_resp = client.post("/auth/logout")
    assert logout_resp.status_code == 200

    # 6. /auth/me is unauthenticated again
    me_after = client.get("/auth/me")
    assert me_after.status_code == 200
    assert me_after.json() is None
