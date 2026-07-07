#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.alas_embed import resolve_base_url, runtime_url_candidates


class AlasEmbedTests(unittest.TestCase):
    def test_ip_without_port_uses_default_runtime_port(self):
        self.assertEqual(
            runtime_url_candidates("192.168.5.18"),
            ["http://192.168.5.18:22267"],
        )

    def test_domain_without_port_uses_common_fallback_ports(self):
        self.assertEqual(
            runtime_url_candidates("alas.example.test"),
            [
                "http://alas.example.test:80",
                "https://alas.example.test:443",
                "http://alas.example.test:22267",
            ],
        )

    def test_explicit_https_domain_without_port_only_uses_https_candidates(self):
        self.assertEqual(
            runtime_url_candidates("https://alas.example.test"),
            [
                "https://alas.example.test:443",
                "https://alas.example.test:22267",
            ],
        )

    def test_explicit_http_domain_without_port_only_uses_http_candidates(self):
        self.assertEqual(
            runtime_url_candidates("http://alas.example.test"),
            [
                "http://alas.example.test:80",
                "http://alas.example.test:22267",
            ],
        )

    def test_full_url_with_port_normalizes_root_path(self):
        self.assertEqual(
            runtime_url_candidates("http://192.168.5.18:22267/"),
            ["http://192.168.5.18:22267"],
        )

    def test_url_with_username_or_password_raises_value_error(self):
        with self.assertRaises(ValueError):
            runtime_url_candidates("http://user:pass@192.168.5.18:22267")

    def test_url_with_params_raises_value_error(self):
        with self.assertRaises(ValueError):
            runtime_url_candidates("http://example.com/;x")


class AlasEmbedResolveTests(unittest.TestCase):
    def test_resolve_base_url_returns_first_reachable_candidate(self):
        attempts = []

        def probe(url, timeout=2.0):
            attempts.append((url, timeout))
            return url == "http://alas.example.test:22267"

        self.assertEqual(
            resolve_base_url("alas.example.test", probe=probe),
            "http://alas.example.test:22267",
        )
        self.assertEqual(
            attempts,
            [
                ("http://alas.example.test:80", 2.0),
                ("https://alas.example.test:443", 2.0),
                ("http://alas.example.test:22267", 2.0),
            ],
        )

    def test_resolve_base_url_raises_when_all_candidates_fail(self):
        with self.assertRaisesRegex(ValueError, "ALAS Runtime unreachable"):
            resolve_base_url("alas.example.test", probe=lambda url, timeout=2.0: False)


if __name__ == "__main__":
    unittest.main()
