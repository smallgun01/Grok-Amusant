"""Liveness checks distinguish explicit FAST-only from configured callback."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("healthcheck", Path(__file__).parents[1] / "oci-a1/healthcheck.py")
healthcheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(healthcheck)

class HealthcheckTests(unittest.TestCase):
    def test_fast_only_still_requires_device_listener(self):
        env = {"RELAY_BIND_IP": "127.0.0.1", "AMUSANT_SLOW_EXECUTOR": "grok_callback", "AMUSANT_GROK_CALLBACK_URL": "https://callback.example.invalid/callback/results"}
        with patch.dict("os.environ", env, clear=True), patch.object(healthcheck.socket, "create_connection", return_value=MagicMock()), patch.object(healthcheck.http.client, "HTTPConnection") as http:
            self.assertEqual(healthcheck.main(), 0)
            http.assert_not_called()
        with patch.dict("os.environ", env, clear=True), patch.object(healthcheck.socket, "create_connection", side_effect=OSError):
            self.assertEqual(healthcheck.main(), 1)

    def test_configured_callback_must_respond(self):
        env = {"RELAY_BIND_IP": "127.0.0.1", "RELAY_CALLBACK_BIND_IP": "127.0.0.2", "AMUSANT_SLOW_EXECUTOR": "grok_callback", "AMUSANT_GROK_CALLBACK_URL": "https://callback.example.com/callback/results"}
        with patch.dict("os.environ", env, clear=True), patch.object(healthcheck.socket, "create_connection", return_value=MagicMock()), patch.object(healthcheck.http.client, "HTTPConnection", side_effect=OSError):
            self.assertEqual(healthcheck.main(), 1)
