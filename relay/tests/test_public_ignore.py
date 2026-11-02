import pathlib, subprocess, tempfile, unittest

class IgnoreTests(unittest.TestCase):
    def test_private_paths_ignored_example_trackable(self):
        rules = pathlib.Path(__file__).resolve().parents[2].joinpath('.gitignore').read_bytes()
        with tempfile.TemporaryDirectory() as name:
            root = pathlib.Path(name)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / '.gitignore').write_bytes(rules)
            for path in ('deploy/oci-a1/secrets/gemini_api_key', 'deploy/oci-a1/secrets/device_psks', 'deploy/oci-a1/grok-staging.env'):
                self.assertEqual(subprocess.run(['git', '-C', str(root), 'check-ignore', '-q', path]).returncode, 0, path)
            self.assertEqual(subprocess.run(['git', '-C', str(root), 'check-ignore', '-q', 'deploy/oci-a1/grok-staging.env.example']).returncode, 1)