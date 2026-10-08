"""Real loopback transport, synthetic v2 data; no actual model inference."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_local_package as fixtures
import local_chat


class LocalChatTests(unittest.TestCase):
    save = fixtures.LocalPackageTests.save

    def setUp(self):
        fixtures.LocalPackageTests.setUp(self)
        self.requests = []
        self.metadata = {"capabilities": []}
        self.message = {"role": "assistant", "content": "Synthetic local response"}
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((self.path, data))
                value = owner.metadata if self.path == "/api/show" else {"done": True, "message": owner.message}
                payload = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def session(self, **kwargs):
        return local_chat.LocalChatSession(self.package, **{
            "model": "synthetic-local", "confirm_local_storage": True,
            "confirm_local_model": True, "preview": True, "base_url": self.url, **kwargs})

    def test_private_text_is_sent_only_after_metadata_check_and_never_written(self):
        before = {p.name: p.read_bytes() for p in self.package.iterdir()}
        session = self.session()
        self.assertEqual([path for path, _ in self.requests], ["/api/show"])
        self.assertNotIn("SYNTHETIC", json.dumps(self.requests))
        self.assertEqual(session.send("SYNTHETIC PRIVATE QUESTION"), "Synthetic local response")
        self.assertEqual([path for path, _ in self.requests], ["/api/show", "/api/show", "/api/chat"])
        payload = self.requests[-1][1]
        self.assertNotIn("tools", payload)
        self.assertIn("SYNTHETIC", payload["messages"][0]["content"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.package.iterdir()})

    def test_untrusted_or_remote_model_fails_before_body_loading(self):
        for options in ({"confirm_local_model": False}, {"confirm_local_storage": False},
                        {"base_url": "https://example.invalid"}, {"model": "some-cloud-model"}):
            with self.subTest(options=options), patch.object(local_chat, "load_local_context", side_effect=AssertionError("body read")):
                with self.assertRaises(ValueError):
                    self.session(**options)
        self.assertEqual(self.requests, [])

    def test_cloud_metadata_fails_before_body_loading(self):
        self.metadata["remote_host"] = "https://example.invalid"
        with patch.object(local_chat, "load_local_context", side_effect=AssertionError("body read")):
            with self.assertRaisesRegex(ValueError, "Remote/cloud"):
                self.session()
        self.assertEqual([path for path, _ in self.requests], ["/api/show"])

    def test_model_becoming_remote_between_turns_is_rechecked(self):
        session = self.session()
        self.metadata["remote_model"] = "changed"
        with self.assertRaisesRegex(ValueError, "Remote/cloud"):
            session.send("PRIVATE QUESTION")
        self.assertTrue(all(path == "/api/show" for path, _ in self.requests))
        self.assertEqual(len(session._messages), 1)

    def test_package_change_prevents_sending_old_or_new_body(self):
        session = self.session()
        (self.package / self.manifest["files"]["profile"]).write_text("MODIFIED", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Persona changed"):
            session.send("PRIVATE QUESTION")
        self.assertTrue(all(path == "/api/show" for path, _ in self.requests))

    def test_failed_response_is_transactional_and_tools_never_execute(self):
        session = self.session()
        self.message["tool_calls"] = [{"function": {"name": "upload", "arguments": {}}}]
        with self.assertRaisesRegex(ValueError, "does not execute tools"):
            session.send("PRIVATE QUESTION")
        self.assertEqual(len(session._messages), 1)
        self.message = {"role": "assistant", "content": ""}
        with self.assertRaisesRegex(ValueError, "empty text"):
            session.send("PRIVATE QUESTION")
        self.assertEqual(len(session._messages), 1)

    def test_history_limit_and_reset(self):
        session = self.session(max_history_chars=10)
        calls = len(self.requests)
        with self.assertRaisesRegex(ValueError, "budget"):
            session.send("QUESTION")
        self.assertEqual(len(self.requests), calls)
        session = self.session()
        session.send("QUESTION")
        self.assertEqual(len(session._messages), 3)
        session.reset()
        self.assertEqual(len(session._messages), 1)


if __name__ == "__main__":
    unittest.main()
