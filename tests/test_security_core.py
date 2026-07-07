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


class SecurityCoreTests(unittest.TestCase):
    def tearDown(self):
        for key in ("PUBLIC_BASE_URL", "ALLOWED_ORIGINS", "ALLOWED_HOSTS", "TRUST_PROXY", "TRUSTED_PROXY_IPS"):
            os.environ.pop(key, None)
        sys.modules.pop("app.security", None)

    def test_invalid_origin_is_rejected(self):
        security = load_security()

        self.assertTrue(security.origin_allowed(None))
        self.assertTrue(security.origin_allowed(""))
        self.assertFalse(security.origin_allowed("not a url"))
        self.assertFalse(security.origin_allowed("javascript:alert(1)"))

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


if __name__ == "__main__":
    unittest.main()
