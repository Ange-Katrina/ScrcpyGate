import importlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_security():
    sys.modules.pop("app.security", None)
    return importlib.import_module("app.security")


class FakeClient:
    def __init__(self, host: str):
        self.host = host


class FakeRequest:
    def __init__(self, host: str = "203.0.113.10"):
        self.client = FakeClient(host)
        self.headers = {}


class SecurityCoreTests(unittest.TestCase):
    def tearDown(self):
        for key in (
            "PUBLIC_BASE_URL",
            "ALLOWED_ORIGINS",
            "ALLOWED_HOSTS",
            "ALLOW_NULL_ORIGIN",
            "TRUST_PROXY",
            "TRUSTED_PROXY_IPS",
            "LOGIN_RATE_LIMIT_ENABLED",
            "LOGIN_RATE_LIMIT_MAX",
            "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
            "LOGIN_LOCKOUT_SECONDS",
        ):
            os.environ.pop(key, None)
        sys.modules.pop("app.security", None)

    def test_invalid_origin_is_rejected(self):
        security = load_security()

        self.assertTrue(security.origin_allowed(None))
        self.assertTrue(security.origin_allowed(""))
        self.assertFalse(security.origin_allowed("not a url"))
        self.assertFalse(security.origin_allowed("null"))
        self.assertFalse(security.origin_allowed("javascript:alert(1)"))

    def test_null_origin_requires_explicit_opt_in(self):
        security = load_security()
        self.assertFalse(security.origin_allowed("null"))

        os.environ["ALLOW_NULL_ORIGIN"] = "true"
        security = load_security()
        self.assertTrue(security.origin_allowed("null"))

    def test_origin_can_match_public_base_or_request_host(self):
        os.environ["PUBLIC_BASE_URL"] = "https://example.com"
        security = load_security()

        self.assertTrue(security.origin_allowed("https://example.com"))
        self.assertTrue(security.origin_allowed("http://127.0.0.1:5000", "http://127.0.0.1:5000"))
        self.assertFalse(security.origin_allowed("https://evil.example"))

    def test_proxy_headers_require_trusted_proxy(self):
        security = load_security()
        headers = {"x-forwarded-for": "203.0.113.10"}

        self.assertFalse(security.proxy_headers_allowed(headers, "127.0.0.1", "/ws/events"))

        os.environ["TRUST_PROXY"] = "true"
        os.environ["TRUSTED_PROXY_IPS"] = "127.0.0.1"
        security = load_security()
        self.assertTrue(security.proxy_headers_allowed(headers, "127.0.0.1", "/ws/events"))
        self.assertFalse(security.proxy_headers_allowed(headers, "10.0.0.2", "/ws/events"))

    def test_client_ip_accepts_only_canonical_addresses_from_trusted_proxy(self):
        os.environ["TRUST_PROXY"] = "true"
        os.environ["TRUSTED_PROXY_IPS"] = "127.0.0.1"
        security = load_security()
        request = FakeRequest()
        request.client.host = "127.0.0.1"
        request.headers["x-forwarded-for"] = "203.0.113.10"
        self.assertEqual(security.client_ip(request), "203.0.113.10")

        request.headers["x-forwarded-for"] = "203.0.113.10\r\nFORGED"
        self.assertEqual(security.client_ip(request), "127.0.0.1")

    def test_login_rate_limit_blocks_repeated_failures(self):
        os.environ["LOGIN_RATE_LIMIT_MAX"] = "2"
        os.environ["LOGIN_RATE_LIMIT_WINDOW_SECONDS"] = "60"
        os.environ["LOGIN_LOCKOUT_SECONDS"] = "30"
        security = load_security()
        request = FakeRequest()

        self.assertFalse(security.login_rate_limit_status(request, "admin")["limited"])
        self.assertFalse(security.record_login_failure(request, "admin")["limited"])
        limited = security.record_login_failure(request, "admin")

        self.assertTrue(limited["limited"])
        status = security.login_rate_limit_status(request, "admin")
        self.assertTrue(status["limited"])
        self.assertGreater(status["retry_after"], 0)

    def test_login_rate_limit_can_be_disabled(self):
        os.environ["LOGIN_RATE_LIMIT_ENABLED"] = "false"
        os.environ["LOGIN_RATE_LIMIT_MAX"] = "1"
        security = load_security()
        request = FakeRequest()

        self.assertFalse(security.record_login_failure(request, "admin")["limited"])
        self.assertFalse(security.login_rate_limit_status(request, "admin")["limited"])


if __name__ == "__main__":
    unittest.main()
