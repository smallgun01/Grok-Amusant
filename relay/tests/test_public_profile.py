import unittest
from relay.application import load_relay_process_config

class PublicProfileTests(unittest.TestCase):
    def test_profile_must_be_explicit_grok(self):
        base = {'AMUSANT_CLIENT_ID': 'test-client', 'AMUSANT_BACKEND_ID': 'test-backend', 'AMUSANT_TASK_DB': 'test.sqlite3'}
        for profile in (None, 'hermes_runs', 'openab_acp', 'other'):
            env = dict(base)
            if profile is not None:
                env['AMUSANT_SLOW_EXECUTOR'] = profile
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                load_relay_process_config(env, expected_device_token='synthetic-token-value')
        env = dict(base, AMUSANT_SLOW_EXECUTOR='grok_callback')
        self.assertEqual(load_relay_process_config(env, expected_device_token='synthetic-token-value').slow_executor_kind, 'grok_callback')