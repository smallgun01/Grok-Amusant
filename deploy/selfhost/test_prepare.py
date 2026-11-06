"""Offline regression for the private self-host setup generator."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("prepare.py")
spec = importlib.util.spec_from_file_location("prepare", SCRIPT)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class SelfHostPrepareTests(unittest.TestCase):
    def test_matching_private_credentials_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            firmware = root / "kai/firmware"
            (firmware / "src").mkdir(parents=True)
            (firmware / "src/secrets.example.h").write_text("#pragma once\n")
            paths = prepare.prepare(
                private_dir=root / "private", firmware_dir=firmware,
                device_host="device.owner.net", callback_host="callback.owner.net",
                owner_id="owner-1", gemini_api_key="fake-gemini-secret",
                grok_routine_token="fake-routine-token",
            )
            fw = paths["firmware"].read_text()
            env = paths["env"].read_text()
            self.assertIn('#define RELAY_TLS_PORT 443', fw)
            self.assertIn(paths["device_token"].read_text().strip(), fw)
            self.assertIn(paths["device_psks"].read_text().strip().split(":")[1], fw)
            self.assertIn("callback.owner.net:9443/callback/results", env)
            self.assertIn("https://routine.example.invalid/webhook", env)
            self.assertIn("callback.owner.net:9443", paths["caddy"].read_text())
            for path in paths.values():
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(0o700, (root / "private/data/relay").stat().st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                prepare.prepare(
                    private_dir=root / "private", firmware_dir=firmware,
                    device_host="device.owner.net", callback_host="callback.owner.net",
                    owner_id="owner-1", gemini_api_key="fake-gemini-secret",
                    grok_routine_token="fake-routine-token",
                )

    def test_rejects_sample_hosts_and_non_https_routines(self):
        for hostname in ("device.example.invalid", "localhost", "relay.example.net", "my-device", "example.com"):
            with self.subTest(host=hostname), self.assertRaises(ValueError):
                prepare.host_name(hostname)
        with self.assertRaises(ValueError):
            prepare.routine_url("http://routine.owner.net/webhook")


if __name__ == "__main__":
    unittest.main()
