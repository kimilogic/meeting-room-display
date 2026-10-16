import tempfile
import unittest
from pathlib import Path

from app.config import ConfigError, load_credentials, load_settings


class ConfigTests(unittest.TestCase):
    def config(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text(text, encoding="utf-8")
            return load_settings(path)

    def test_dummy_needs_no_credentials(self):
        self.assertEqual(self.config('DATA_SOURCE = "dummy"').data_source, "dummy")

    def test_graph_room_required(self):
        with self.assertRaises(ConfigError):
            self.config('DATA_SOURCE = "graph"')

    def test_invalid_file_and_secret_keys_fail_safely(self):
        for content in ('bad toml', 'DATA_SOURCE = "other"', 'CLIENT_SECRET = "do-not-expose"'):
            with self.subTest(content=content):
                with self.assertRaises(ConfigError) as caught:
                    self.config(content)
                self.assertNotIn("do-not-expose", str(caught.exception))

    def test_missing_environment(self):
        with self.assertRaises(ConfigError) as caught:
            load_credentials({})
        self.assertIn("CLIENT_SECRET", str(caught.exception))

    def test_credentials_do_not_repr_secret(self):
        # Synthetic GUIDs only for validation, never deployment settings.
        result = load_credentials({"TENANT_ID": "00000000-0000-0000-0000-000000000001",
            "CLIENT_ID": "00000000-0000-0000-0000-000000000002", "CLIENT_SECRET": "synthetic-secret"})
        self.assertNotIn("synthetic-secret", repr(result))


if __name__ == "__main__":
    unittest.main()
