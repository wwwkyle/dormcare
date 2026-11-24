"""DeepSeek ASR configuration, validation and end-to-end tests."""
import base64
import io
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import asr_client
import asr_config
from store import Store
from testclient import (DEMO_ADMIN, DEMO_STUDENT, DEMO_WORKERS, Client,
                        reset_store, server_output, spawn_server)


def wav_bytes(seconds=2, rate=16000, channels=1):
    data = b"\x00\x00" * int(rate * channels * seconds)
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate,
                                    rate * channels * 2, channels * 2, 16)
            + b"data" + struct.pack("<I", len(data)) + data)


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class FakeDeepSeek(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        FakeDeepSeek.requests.append((self.path, self.headers.get("Authorization"), body))
        payload = json.dumps({"request_id": "req-test", "text": "灯不亮了",
                              "duration_ms": 2000}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass


class AsrConfigTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        asr_config.bind(None)
        self.addCleanup(lambda: asr_config.bind(None))

    def test_environment_fallback_and_status(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-env-key"
        self.assertTrue(asr_config.enabled())
        self.assertEqual(asr_config.status()["source"], "env")
        self.assertEqual(asr_config.status()["max_seconds"], 60)

    def test_saved_key_wins_and_is_never_echoed(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-env-key"
        state, error = asr_config.configure("sk-page-key")
        self.assertIsNone(error)
        self.assertEqual(state["source"], "saved")
        self.assertEqual(asr_config.key(), "sk-page-key")
        self.assertNotIn("sk-page-key", json.dumps(state))

    def test_validation_and_reset(self):
        for raw, code in (("", "asr_missing_key"), ("bad key", "asr_bad_key"),
                          ("x" * 401, "asr_bad_key")):
            state, error = asr_config.configure(raw)
            self.assertIsNone(state)
            self.assertEqual(error, code)
        asr_config.configure("sk-page-key")
        os.environ["DEEPSEEK_API_KEY"] = "sk-env-key"
        self.assertEqual(asr_config.reset()["source"], "env")

    def test_saved_key_survives_a_fresh_store(self):
        with tempfile.TemporaryDirectory(prefix="dormcare-asr-") as temporary:
            path = os.path.join(temporary, "store.json")
            asr_config.bind(Store(path))
            _state, error = asr_config.configure("sk-page-key")
            self.assertIsNone(error)
            asr_config.bind(Store(path))
            self.assertEqual(asr_config.key(), "sk-page-key")


class AsrClientTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        asr_config.bind(None)
        self.addCleanup(lambda: asr_config.bind(None))

    def test_wav_validation(self):
        self.assertAlmostEqual(asr_client.parse_wav(wav_bytes(2)), 2.0, places=2)
        with self.assertRaises(asr_client.ASRError) as caught:
            asr_client.parse_wav(b"not a wav")
        self.assertEqual(str(caught.exception), "bad_audio")
        with self.assertRaises(asr_client.ASRError) as caught:
            asr_client.parse_wav(wav_bytes(61))
        self.assertEqual(str(caught.exception), "too_long")
        with self.assertRaises(asr_client.ASRError) as caught:
            asr_client.parse_wav(wav_bytes(2, rate=11025))
        self.assertEqual(str(caught.exception), "bad_audio")

    def test_client_sends_bearer_key_and_multipart_file(self):
        asr_config.configure("sk-page-key")
        response = io.BytesIO(json.dumps({"request_id": "r1", "text": "灯不亮了",
                                          "duration_ms": 2000}).encode())
        with patch("asr_client.urllib.request.build_opener") as factory:
            factory.return_value.open.return_value = response
            result = asr_client.transcribe(wav_bytes(1))
            request = factory.return_value.open.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/v1/tools/asr")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-page-key")
        self.assertIn(b'name="file"', request.data)
        self.assertIn(b"RIFF", request.data)
        self.assertEqual(result["text"], "灯不亮了")

    def test_http_errors_are_mapped_without_leaking_body(self):
        asr_config.configure("sk-page-key")
        with patch("asr_client.urllib.request.build_opener") as factory:
            factory.return_value.open.side_effect = urllib.error.HTTPError(
                "url", 401, "Unauthorized", {}, io.BytesIO(b"secret body"))
            with self.assertRaises(asr_client.ASRError) as caught:
                asr_client.transcribe(wav_bytes(1))
        self.assertEqual(str(caught.exception), "unauthorized")
        self.assertNotIn("secret body", str(caught.exception))


class AsrHTTPTests(unittest.TestCase):
    def _start(self, temporary, env_extra, fresh=True):
        data = os.path.join(temporary, "store.json")
        if fresh:
            reset_store(data)
        proc, _log = spawn_server(ROOT, free_port(), "", env_extra,
                                  log_name=os.path.join(temporary, "server.log"),
                                  data=data)
        return proc, _log

    @staticmethod
    def _stop(proc):
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        if getattr(proc, "_log_handle", None):
            proc._log_handle.close()

    def test_permissions_and_end_to_end_transcription(self):
        with tempfile.TemporaryDirectory(prefix="dormcare-asr-http-") as temporary:
            provider = ThreadingHTTPServer(("127.0.0.1", 0), FakeDeepSeek)
            thread = threading.Thread(target=provider.serve_forever, daemon=True)
            thread.start()
            FakeDeepSeek.requests = []
            proc, log = self._start(temporary, {
                "DEEPSEEK_API_KEY": "",
                "DEEPSEEK_ASR_URL": "http://127.0.0.1:%d/v1/tools/asr" % provider.server_port,
            })
            try:
                # The spawned app picked a free port itself; discover it from
                # the log line the server prints on boot.
                import re
                text = Path(log).read_text(encoding="utf-8", errors="replace")
                port = int(re.search(r"http://0\.0\.0\.0:(\d+)", text).group(1))
                base = "http://127.0.0.1:%d" % port
                anon, student = Client(base), Client(base, *DEMO_STUDENT)
                worker, admin = Client(base, *DEMO_WORKERS[0]), Client(base, *DEMO_ADMIN)

                for client, want in ((anon, 401), (student, 403), (worker, 403)):
                    self.assertEqual(client.call("/api/admin/asr")[0], want)
                    self.assertEqual(client.call("/api/asr/setup", {"api_key": "sk-x"})[0], want)
                    self.assertEqual(client.call("/api/asr/setup/reset", {})[0], want)
                for client, want in ((anon, 401), (worker, 403), (admin, 403)):
                    status, data = client.call("/api/asr/transcribe", {"audio": "x"})
                    self.assertEqual(status, want)

                state = admin.call("/api/admin/asr")[1]
                self.assertFalse(state["configured"])
                self.assertNotIn("api_key", json.dumps(state))
                self.assertFalse(anon.call("/api/config")[1]["asr_enabled"])

                saved = admin.call("/api/asr/setup", {"api_key": "sk-deepseek-test"})[1]
                self.assertEqual(saved["status"]["source"], "saved")
                self.assertNotIn("sk-deepseek-test", json.dumps(saved))
                self.assertTrue(anon.call("/api/config")[1]["asr_enabled"])
                self.assertEqual(anon.call("/api/config")[1]["asr_max_seconds"], 60)

                audio = base64.b64encode(wav_bytes(2)).decode()
                status, result = student.call("/api/asr/transcribe",
                                              {"audio": audio, "mime": "audio/wav"})
                self.assertEqual(status, 200)
                self.assertEqual(result["text"], "灯不亮了")
                self.assertEqual(result["duration_ms"], 2000)
                self.assertEqual(len(FakeDeepSeek.requests), 1)
                self.assertEqual(FakeDeepSeek.requests[0][1], "Bearer sk-deepseek-test")
                self.assertIn(b'name="file"', FakeDeepSeek.requests[0][2])

                before = len(FakeDeepSeek.requests)
                status, error = student.call("/api/asr/transcribe", {
                    "audio": base64.b64encode(wav_bytes(61)).decode()})
                self.assertEqual(status, 400)
                self.assertEqual(error.get("code"), "asr_too_long")
                self.assertEqual(len(FakeDeepSeek.requests), before)

                status, error = student.call("/api/asr/transcribe", {"audio": "!!!not-base64"})
                self.assertEqual(status, 400)
                self.assertEqual(error.get("code"), "asr_bad_audio")

                reset = admin.call("/api/asr/setup/reset", {})[1]
                self.assertFalse(reset["status"]["configured"])
                self.assertFalse(anon.call("/api/config")[1]["asr_enabled"])
            finally:
                self._stop(proc)
                provider.shutdown()
                provider.server_close()
                thread.join(timeout=5)
                output = server_output(os.path.join(temporary, "server.log"))
                self.assertNotIn("Traceback", output, output[-800:])


if __name__ == "__main__":
    unittest.main()
