"""
Integration tests for HTTP hardening: headers, limits, CORS and proxy handling.
"""

import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from src.api.app import create_app
from src.config import TestConfig


def make_client(mock_db, **overrides):
    """Build a test client with config overrides."""
    with patch("src.api.app.get_database", return_value=mock_db):
        app = create_app(replace(TestConfig(), **overrides))
    app.config["TESTING"] = True
    return app.test_client()


class TestSecurityHeaders:

    def test_headers_on_dashboard(self, client):
        response = client.get("/")

        csp = response.headers["Content-Security-Policy"]
        assert "script-src 'self'" in csp
        assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_headers_on_api_errors(self, client):
        response = client.get("/api/v1/workflows/not-a-uuid")

        assert "Content-Security-Policy" in response.headers


class TestRequestLimits:

    def test_oversized_body_rejected(self, mock_db):
        client = make_client(mock_db, MAX_REQUEST_BYTES=1024)

        response = client.post(
            "/api/v1/workflows",
            data="x" * 2048,
            content_type="application/json",
        )

        assert response.status_code == 413

    def test_write_rate_limit(self, mock_db):
        client = make_client(mock_db, RATE_LIMIT_WRITES="2 per minute")

        codes = [
            client.post("/api/v1/workflows", json={}).status_code
            for _ in range(3)
        ]

        # The first two reach the handler (400: name missing); the third is throttled.
        assert codes == [400, 400, 429]

    def test_read_default_rate_limit(self, mock_db):
        client = make_client(mock_db, RATE_LIMIT_DEFAULT="2 per minute")

        with patch("src.api.routes.get_workflow_service") as svc:
            svc.return_value.list_workflows.return_value = []
            codes = [client.get("/api/v1/workflows").status_code for _ in range(3)]

        assert codes == [200, 200, 429]

    def test_health_exempt_from_rate_limit(self, mock_db):
        client = make_client(mock_db, RATE_LIMIT_DEFAULT="1 per minute")

        with patch("src.worker.TaskQueue") as queue:
            queue.return_value.health_check.return_value = True
            codes = [client.get("/health").status_code for _ in range(3)]

        assert codes == [200, 200, 200]

    def test_limits_are_per_client_behind_proxy(self, mock_db):
        client = make_client(
            mock_db, RATE_LIMIT_WRITES="1 per minute", TRUSTED_PROXY_HOPS=1
        )

        first = client.post("/api/v1/workflows", json={}, headers={"X-Forwarded-For": "203.0.113.1"})
        other = client.post("/api/v1/workflows", json={}, headers={"X-Forwarded-For": "203.0.113.2"})
        again = client.post("/api/v1/workflows", json={}, headers={"X-Forwarded-For": "203.0.113.1"})

        assert (first.status_code, other.status_code, again.status_code) == (400, 400, 429)

    def test_forwarded_header_ignored_without_trusted_proxy(self, mock_db):
        """Without a trusted proxy, a client cannot dodge limits by faking its IP."""
        client = make_client(mock_db, RATE_LIMIT_WRITES="1 per minute")

        client.post("/api/v1/workflows", json={}, headers={"X-Forwarded-For": "203.0.113.1"})
        spoofed = client.post("/api/v1/workflows", json={}, headers={"X-Forwarded-For": "203.0.113.9"})

        assert spoofed.status_code == 429


class TestPagination:

    @pytest.mark.parametrize("raw, expected", [
        ("99999999", 1000),
        ("-5", 1),
        ("0", 1),
        ("50", 50),
    ])
    def test_limit_clamped(self, client, raw, expected):
        with patch("src.api.routes.get_workflow_service") as svc:
            svc.return_value.list_workflows.return_value = []
            response = client.get(f"/api/v1/workflows?limit={raw}&offset=-3")

        kwargs = svc.return_value.list_workflows.call_args.kwargs
        assert kwargs["limit"] == expected
        assert kwargs["offset"] == 0
        assert json.loads(response.data)["limit"] == expected


class TestCors:

    def test_cors_disabled_when_no_origins(self, mock_db):
        client = make_client(mock_db, CORS_ORIGINS="")

        response = client.get("/", headers={"Origin": "https://evil.example"})

        assert "Access-Control-Allow-Origin" not in response.headers

    def test_cors_allows_listed_origin_only(self, mock_db):
        client = make_client(mock_db, CORS_ORIGINS="https://good.example")

        good = client.get("/", headers={"Origin": "https://good.example"})
        bad = client.get("/", headers={"Origin": "https://evil.example"})

        assert good.headers.get("Access-Control-Allow-Origin") == "https://good.example"
        assert "Access-Control-Allow-Origin" not in bad.headers


class TestApiKey:

    KEY = "k" * 64

    def test_write_without_key_rejected(self, mock_db):
        client = make_client(mock_db, API_KEY=self.KEY)

        response = client.post("/api/v1/workflows", json={"name": "x"})

        assert response.status_code == 401

    def test_write_with_wrong_key_rejected(self, mock_db):
        client = make_client(mock_db, API_KEY=self.KEY)

        response = client.post(
            "/api/v1/workflows", json={"name": "x"}, headers={"X-API-Key": "wrong"}
        )

        assert response.status_code == 401

    def test_write_with_key_reaches_handler(self, mock_db):
        client = make_client(mock_db, API_KEY=self.KEY)

        # Empty body: the handler itself answers 400, proving auth passed.
        response = client.post(
            "/api/v1/workflows", json={}, headers={"X-API-Key": self.KEY}
        )

        assert response.status_code == 400

    def test_reads_stay_public(self, mock_db):
        client = make_client(mock_db, API_KEY=self.KEY)

        with patch("src.api.routes.get_workflow_service") as svc:
            svc.return_value.list_workflows.return_value = []
            assert client.get("/api/v1/workflows").status_code == 200
        assert client.get("/").status_code == 200

    def test_key_guesses_are_rate_limited(self, mock_db):
        client = make_client(mock_db, API_KEY=self.KEY, RATE_LIMIT_WRITES="2 per minute")

        codes = [
            client.post("/api/v1/workflows", json={}, headers={"X-API-Key": "guess"}).status_code
            for _ in range(3)
        ]

        assert codes == [401, 401, 429]

    def test_no_key_leaves_writes_open_outside_production(self, mock_db):
        client = make_client(mock_db, API_KEY="")

        assert client.post("/api/v1/workflows", json={}).status_code == 400

    def test_production_refuses_to_start_without_key(self, mock_db):
        with patch("src.api.app.get_database", return_value=mock_db):
            with pytest.raises(RuntimeError, match="API_KEY"):
                create_app(replace(TestConfig(), FLASK_ENV="production", API_KEY=""))
