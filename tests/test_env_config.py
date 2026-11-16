"""Local .env parsing and existing-environment precedence tests."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from env_config import load_env


class EnvConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dormcare-env-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / ".env"
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def load(self, content):
        self.path.write_text(content, encoding="utf-8")
        load_env(self.path)

    def test_loads_dify_settings(self):
        self.load("DIFY_API_KEY=app-test\nDIFY_API_BASE_URL=https://api.dify.ai/v1\n")
        self.assertEqual(os.environ["DIFY_API_KEY"], "app-test")
        self.assertEqual(os.environ["DIFY_API_BASE_URL"], "https://api.dify.ai/v1")

    def test_existing_environment_wins_even_when_empty(self):
        os.environ["DIFY_API_KEY"] = "external-key"
        os.environ["DIFY_INPUTS_JSON"] = ""
        self.load("DIFY_API_KEY=local-key\nDIFY_INPUTS_JSON={}\n")
        self.assertEqual(os.environ["DIFY_API_KEY"], "external-key")
        self.assertEqual(os.environ["DIFY_INPUTS_JSON"], "")

    def test_preserves_json_quotes_equals_and_hashes(self):
        self.load("DIFY_INPUTS_JSON='{\"token\": \"a=b#c\"}' # workflow inputs\n"
                  'DIFY_API_KEY="app-test#value"\n')
        self.assertEqual(os.environ["DIFY_INPUTS_JSON"], '{"token": "a=b#c"}')
        self.assertEqual(os.environ["DIFY_API_KEY"], "app-test#value")

    def test_comments_export_whitespace_and_empty_values(self):
        self.load("\ufeff# configuration\n\n export DIFY_API_KEY = 'app-test' # local\n"
                  "DIFY_TIMEOUT_SECONDS=60 # seconds\nDIFY_INPUTS_JSON=\n")
        self.assertEqual(os.environ["DIFY_API_KEY"], "app-test")
        self.assertEqual(os.environ["DIFY_TIMEOUT_SECONDS"], "60")
        self.assertEqual(os.environ["DIFY_INPUTS_JSON"], "")

    def test_missing_file_is_optional(self):
        load_env(self.path)
        self.assertEqual(dict(os.environ), {})

    def test_invalid_entries_are_ignored(self):
        self.load("no assignment\nBAD-NAME=value\n=empty\n"
                  "DIFY_API_KEY='unclosed\nDIFY_TIMEOUT_SECONDS='60' trailing\n")
        self.assertEqual(dict(os.environ), {})

    def test_does_not_execute_or_expand_shell_values(self):
        self.load("DIFY_API_KEY=$(printf unsafe)\nDIFY_INPUTS_JSON=${OTHER}\n")
        self.assertEqual(os.environ["DIFY_API_KEY"], "$(printf unsafe)")
        self.assertEqual(os.environ["DIFY_INPUTS_JSON"], "${OTHER}")


if __name__ == "__main__":
    unittest.main()
