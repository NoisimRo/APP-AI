"""Regression tests for the security hardening (auth guards, headers, limits)."""

import asyncio
import os

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

os.environ.setdefault("SKIP_DB", "true")
os.environ.setdefault("ENVIRONMENT", "test")

from app.core import throttle  # noqa: E402
from app.core.config import Settings  # noqa: E402
from app.core.middleware import BodySizeLimitMiddleware, SECURITY_HEADERS  # noqa: E402
from app.main import app, resolve_static_file  # noqa: E402


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class TestSecretKeyGuard:
    def test_production_refuses_placeholder_secret(self):
        with pytest.raises(ValueError, match="SECRET_KEY"):
            Settings(environment="production", secret_key="change-me-in-production", _env_file=None)

    def test_production_refuses_short_secret(self):
        with pytest.raises(ValueError):
            Settings(environment="production", secret_key="short", _env_file=None)

    def test_production_accepts_strong_secret(self):
        s = Settings(environment="production", secret_key="x" * 64, _env_file=None)
        assert s.secret_key_is_secure

    def test_development_tolerates_placeholder(self):
        s = Settings(environment="development", secret_key="change-me-in-production", _env_file=None)
        assert not s.secret_key_is_secure

    def test_cors_origins_parsed(self):
        s = Settings(cors_origins=" http://a.test, http://b.test ,", _env_file=None)
        assert s.cors_origin_list == ["http://a.test", "http://b.test"]


# ---------------------------------------------------------------------------
# Throttle / client IP
# ---------------------------------------------------------------------------

def _request(headers: dict[str, str], client=("10.0.0.1", 1234)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": client, "method": "GET",
                    "path": "/", "query_string": b"", "scheme": "http", "server": ("t", 80)})


class TestClientIp:
    def test_uses_last_forwarded_hop(self):
        # First entry is attacker-supplied; the proxy appends the real one last.
        req = _request({"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})
        assert throttle.get_client_ip(req) == "203.0.113.9"

    def test_falls_back_to_socket_peer(self):
        assert throttle.get_client_ip(_request({})) == "10.0.0.1"


class TestThrottle:
    def test_blocks_after_limit(self):
        key = "test:blocks"
        results = [asyncio.run(throttle.hit(key, limit=3, window_seconds=60)) for _ in range(5)]
        assert results == [True, True, True, False, False]

    def test_enforce_raises_429(self):
        key = "test:enforce"
        for _ in range(2):
            asyncio.run(throttle.enforce(key, 2, 60, "stop"))
        with pytest.raises(HTTPException) as exc:
            asyncio.run(throttle.enforce(key, 2, 60, "stop"))
        assert exc.value.status_code == 429
        assert exc.value.headers["Retry-After"] == "60"


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

client = TestClient(app)


class TestSecurityHeaders:
    def test_headers_present_on_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        for name, value in SECURITY_HEADERS.items():
            assert resp.headers.get(name) == value

    def test_deep_health_never_echoes_exception_text(self):
        resp = client.get("/health/deep")
        body = resp.json()
        for component in body["components"].values():
            assert "error" not in component


class TestBodySizeLimit:
    def _tiny_app(self):
        inner = FastAPI()

        @inner.post("/echo")
        async def echo(request: Request):
            return {"size": len(await request.body())}

        return TestClient(BodySizeLimitMiddleware(inner, max_bytes=100))

    def test_rejects_declared_oversize(self):
        c = self._tiny_app()
        resp = c.post("/echo", content=b"x" * 101, headers={"Content-Length": "101"})
        assert resp.status_code == 413

    def test_accepts_within_limit(self):
        c = self._tiny_app()
        resp = c.post("/echo", content=b"x" * 50)
        assert resp.status_code == 200
        assert resp.json()["size"] == 50


class TestAdminOnlyImport:
    def test_import_requires_auth(self):
        from app.db.session import get_session

        async def _no_session():
            yield None

        app.dependency_overrides[get_session] = _no_session
        try:
            resp = client.post(
                "/api/v1/decisions/import",
                files={"file": ("BO2025_1_R1_CPV_X.txt", b"text", "text/plain")},
            )
        finally:
            app.dependency_overrides.pop(get_session, None)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# SPA static file resolution
# ---------------------------------------------------------------------------

class TestStaticResolution:
    def test_blocks_parent_traversal(self, tmp_path):
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("<html></html>")
        (tmp_path / "secret.txt").write_text("nope")
        assert resolve_static_file(static.resolve(), "../secret.txt") is None
        assert resolve_static_file(static.resolve(), "..") is None
        assert resolve_static_file(static.resolve(), "") is None

    def test_serves_files_inside_root(self, tmp_path):
        static = tmp_path / "static"
        (static / "assets").mkdir(parents=True)
        (static / "assets" / "app.js").write_text("1")
        assert resolve_static_file(static.resolve(), "assets/app.js") == (static / "assets" / "app.js").resolve()
        assert resolve_static_file(static.resolve(), "missing.js") is None


# ---------------------------------------------------------------------------
# Saved content: no anonymous shared pool
# ---------------------------------------------------------------------------

class TestSavedContentIsolation:
    def test_anonymous_matches_nothing(self):
        from sqlalchemy import false as sa_false
        from app.api.v1.saved import _ownership_filter, _check_ownership
        from app.models.decision import Conversatie

        assert str(_ownership_filter(Conversatie, None)) == str(sa_false())
        with pytest.raises(HTTPException) as exc:
            _check_ownership(object(), None)
        assert exc.value.status_code == 401

    def test_anonymous_scopes_match_nothing(self):
        from sqlalchemy import false as sa_false
        from app.api.v1.scopes import _scope_ownership_filter

        assert str(_scope_ownership_filter(None)) == str(sa_false())
