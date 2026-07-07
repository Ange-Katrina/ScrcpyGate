#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.alas_embed import runtime_url_candidates


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

    def test_full_url_with_port_normalizes_root_path(self):
        self.assertEqual(
            runtime_url_candidates("http://192.168.5.18:22267/"),
            ["http://192.168.5.18:22267"],
        )

    def test_url_with_username_or_password_raises_value_error(self):
        with self.assertRaises(ValueError):
            runtime_url_candidates("http://user:pass@192.168.5.18:22267")


if __name__ == "__main__":
    unittest.main()
