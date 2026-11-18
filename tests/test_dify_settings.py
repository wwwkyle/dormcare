"""Tests for administrator-managed Dify settings.

The two Dify fields used to be deploy-time-only .env entries. They are now
enrolled from the system-settings page, so these tests cover validation, the
environment fallback, persistence across a restart, the permission gates, and
the fact that the outbound client actually uses what the page saved.
"""
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import dify_client
import dify_config
from store import Store
from testclient import (DEMO_ADMIN, DEMO_STUDENT, DEMO_WORKERS, Client,
                        reset_store, server_output, spawn_server)

USER = {"id": "U-test", "role": "student", "college": "shaw",
        "block": "东座 1 栋", "dorm": "A-301"}
SESSION = {"id": "A-test", "history": [{
    "role": "user", "content": "宿舍灯不亮，已经持续两天。"}]}
RESPONSE = {"answer": "请先检查开关与灯管。",
            "conversation_id": "conversation-1", "event": "message"}


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class DifyConfigTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        dify_config.bind(None)
        self.addCleanup(lambda: dify_config.bind(None))

    def test_environment_is_the_fallback(self):
        os.environ["DIFY_API_KEY"] = "app-env-secret"
        os.environ["DIFY_API_BASE_URL"] = "https://env.example/v1/"
        self.assertTrue(dify_config.enabled())
        self.assertEqual(dify_config.key(), "app-env-secret")
        self.assertEqual(dify_config.base_url(), "https://env.example/v1")
        self.assertEqual(dify_config.status()["source"], "env")

    def test_defaults_without_any_configuration(self):
        state = dify_config.status()
        self.assertFalse(state["configured"])
        self.assertEqual(state["source"], "")
        self.assertEqual(state["base_url"], "https://api.dify.ai/v1")
        self.assertEqual(state["base_source"], "default")

    def test_saved_values_win_and_status_never_echoes_the_key(self):
        os.environ["DIFY_API_KEY"] = "app-env-secret"
        state, error = dify_config.configure("app-page-secret", "https://page.example/v1")
        self.assertIsNone(error)
        self.assertTrue(state["configured"])
        self.assertEqual(state["source"], "saved")
        self.assertEqual(state["base_url"], "https://page.example/v1")
        self.assertEqual(dify_config.key(), "app-page-secret")
        dumped = json.dumps(state)
        self.assertNotIn("app-page-secret", dumped)
        self.assertNotIn("api_key", dumped)

    def test_validation_rejects_bad_fields(self):
        for raw, expected in (("", "dify_missing_key"),
                              ("app with space", "dify_bad_key"),
                              ("x" * 401, "dify_bad_key")):
            state, error = dify_config.configure(raw, "")
            self.assertIsNone(state)
            self.assertEqual(error, expected)
        for base in ("http://example.com/v1", "https://user:pw@example.com/v1",
                     "https://example.com/v1?x=1", "https://example.com/v1#f",
                     "x" * 301):
            state, error = dify_config.configure("app-page-secret", base)
            self.assertIsNone(state)
            self.assertEqual(error, "dify_bad_base")
        # A local HTTP endpoint is allowed for the fake provider in the tests.
        state, error = dify_config.configure("app-page-secret", "http://127.0.0.1:9/v1/")
        self.assertIsNone(error)
        self.assertEqual(state["base_url"], "http://127.0.0.1:9/v1")

    def test_reset_falls_back_to_environment_instead_of_disabling_it(self):
        os.environ["DIFY_API_KEY"] = "app-env-secret"
        dify_config.configure("app-page-secret", "https://page.example/v1")
        state = dify_config.reset()
        self.assertEqual(state["source"], "env")
        self.assertEqual(state["base_source"], "default")
        self.assertEqual(dify_config.key(), "app-env-secret")

    def test_saved_settings_survive_a_fresh_store(self):
        with tempfile.TemporaryDirectory(prefix="dormcare-dify-settings-") as temporary:
            path = os.path.join(temporary, "store.json")
            dify_config.bind(Store(path))
            state, error = dify_config.configure("app-page-secret", "https://page.example/v1")
            self.assertIsNone(error)
            self.assertEqual(state["source"], "saved")
            # A fresh process binds the same file; nothing may be read from .env.
            dify_config.bind(Store(path))
            self.assertTrue(dify_config.enabled())
            self.assertEqual(dify_config.key(), "app-page-secret")
            self.assertEqual(dify_config.base_url(), "https://page.example/v1")

    def test_outbound_client_sends_the_page_credentials(self):
        os.environ["DIFY_API_KEY"] = "app-env-secret"
        dify_config.configure("app-page-secret", "http://127.0.0.1:9999/v1")
        with patch("dify_client.urllib.request.build_opener") as factory:
            factory.return_value.open.return_value = io.BytesIO(
                json.dumps(RESPONSE).encode("utf-8"))
            dify_client.chat(dict(SESSION, image_ids=[]), USER)
            request = factory.return_value.open.call_args[0][0]
        self.assertEqual(request.full_url, "http://127.0.0.1:9999/v1/chat-messages")
        self.assertEqual(request.get_header("Authorization"), "Bearer app-page-secret")


class DifySettingsHTTPTests(unittest.TestCase):
    def _start(self, temporary, env_extra, fresh=True):
        port = free_port()
        data = os.path.join(temporary, "store.json")
        if fresh:
            reset_store(data)
        proc, _log = spawn_server(ROOT, port, "", env_extra,
                                  log_name=os.path.join(temporary, "server.log"),
                                  data=data)
        return proc, "http://127.0.0.1:%d" % port, data

    @staticmethod
    def _stop(proc):
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        if getattr(proc, "_log_handle", None):
            proc._log_handle.close()

    def test_only_the_administrator_can_set_the_two_fields(self):
        with tempfile.TemporaryDirectory(prefix="dormcare-dify-http-") as temporary:
            proc, base, _data = self._start(temporary, {
                "DIFY_API_KEY": "", "DIFY_API_BASE_URL": ""})
            try:
                anon = Client(base)
                student = Client(base, *DEMO_STUDENT)
                worker = Client(base, *DEMO_WORKERS[0])
                admin = Client(base, *DEMO_ADMIN)

                for cli, want in ((anon, 401), (student, 403), (worker, 403)):
                    self.assertEqual(cli.call("/api/admin/dify")[0], want)
                    self.assertEqual(cli.call("/api/dify/setup",
                                              {"api_key": "app-x"})[0], want)
                    self.assertEqual(cli.call("/api/dify/setup/reset", {})[0], want)

                status, state = admin.call("/api/admin/dify")
                self.assertEqual(status, 200)
                self.assertFalse(state["configured"])
                self.assertNotIn("api_key", json.dumps(state))

                for body, code in (
                        ({"api_key": ""}, "dify_missing_key"),
                        ({"api_key": "bad key"}, "dify_bad_key"),
                        ({"api_key": "app-good-key", "base_url": "http://example.com/v1"},
                         "dify_bad_base")):
                    status, error = admin.call("/api/dify/setup", body)
                    self.assertEqual(status, 400)
                    self.assertEqual(error.get("code"), code)

                status, saved = admin.call("/api/dify/setup", {
                    "api_key": "app-page-secret", "base_url": "https://page.example/v1"})
                self.assertEqual(status, 200)
                self.assertEqual(saved["status"]["source"], "saved")
                self.assertNotIn("app-page-secret", json.dumps(saved))
                self.assertTrue(anon.call("/api/health")[1]["dify_enabled"])
                self.assertEqual(student.call("/api/config")[1]["agent_image_limit"], 3)

                status, cleared = admin.call("/api/dify/setup/reset", {})
                self.assertEqual(status, 200)
                self.assertFalse(cleared["status"]["configured"])
                self.assertFalse(anon.call("/api/health")[1]["dify_enabled"])
            finally:
                self._stop(proc)
                output = server_output(os.path.join(temporary, "server.log"))
                self.assertNotIn("Traceback", output, output[-800:])

    def test_page_value_survives_restart_and_environment_still_falls_back(self):
        with tempfile.TemporaryDirectory(prefix="dormcare-dify-restart-") as temporary:
            clean = {"DIFY_API_KEY": "", "DIFY_API_BASE_URL": ""}
            with_env = {"DIFY_API_KEY": "app-env-secret", "DIFY_API_BASE_URL": ""}
            proc, base, _data = self._start(temporary, clean)
            try:
                admin = Client(base, *DEMO_ADMIN)
                state = admin.call("/api/admin/dify")[1]
                self.assertEqual(state["source"], "")
                status, _saved = admin.call("/api/dify/setup", {
                    "api_key": "app-page-secret", "base_url": "https://page.example/v1"})
                self.assertEqual(status, 200)
            finally:
                self._stop(proc)

            proc, base, _data = self._start(temporary, clean, fresh=False)
            try:
                state = Client(base, *DEMO_ADMIN).call("/api/admin/dify")[1]
                self.assertTrue(state["configured"])
                self.assertEqual(state["source"], "saved")
                self.assertEqual(state["base_url"], "https://page.example/v1")
            finally:
                self._stop(proc)

            # A deployment that still has the old .env value gets it back after
            # the page configuration has been cleared.
            proc, base, _data = self._start(temporary, with_env, fresh=False)
            try:
                admin = Client(base, *DEMO_ADMIN)
                self.assertEqual(admin.call("/api/admin/dify")[1]["source"], "saved")
                self.assertEqual(admin.call("/api/dify/setup/reset", {})[1]["status"]["source"],
                                 "env")
            finally:
                self._stop(proc)


if __name__ == "__main__":
    unittest.main()
