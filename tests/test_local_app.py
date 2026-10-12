"""Loopback UI integration with actual state and original synthetic model calls."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
import test_roundtable as fixtures

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("persona_local_app", ROOT / "本地应用/scripts/server.py")
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)
import roundtable_runner as runner
import roundtable_editor as editor


class LocalAppTests(unittest.TestCase):
    def setUp(self):
        fixtures.RoundtableStateTests.setUp(self)
        folder = self.root / "圆桌会议/_template"
        folder.mkdir()
        (folder / "session.example.json").write_text(json.dumps(self.config), encoding="utf-8")
        self.server = app.make_server(0, self.root)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.close)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)

    def request(self, path, data=None, headers=None):
        h = {"X-Local-Token": self.server.app.token}
        if data is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = Request(self.url + path, data=json.dumps(data).encode() if data is not None else None, headers=h)
        with urlopen(req, timeout=5) as response:
            body = response.read().decode()
            return json.loads(body) if "application/json" in response.headers["Content-Type"] else body

    def create(self):
        value = self.request("/api/sessions", {"participants": ["kongzi", "zhuangzi"], "topic": "Synthetic topic",
                                               "goal": "Synthetic comparison", "max_turns": 2, "preview": True})
        self.sid = value["session_id"]
        return value

    def action(self, action, data=None):
        return self.request(f"/api/sessions/{self.sid}/{action}", data or {})

    def wait_job(self):
        for _ in range(100):
            value = self.request(f"/api/sessions/{self.sid}")
            if value["job"]["status"] != "running":
                return value
            time.sleep(.03)
        self.fail("Synthetic job did not finish")

    def test_static_and_host_origin_token_boundaries(self):
        html = self.request("/")
        self.assertIn(self.server.app.token, html)
        self.assertNotIn("__LOCAL_TOKEN__", html)
        self.assertIn("create-form", html)
        for headers in ({"X-Local-Token": "bad"}, {"Origin": "https://example.invalid"},
                        {"Host": "attacker.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.assertRaises(HTTPError) as error:
                self.request("/api/library", headers=headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError):
            self.request("/../../AGENTS.md")

    def test_preview_required_and_client_cannot_choose_arbitrary_paths(self):
        with self.assertRaises(HTTPError):
            self.request("/api/sessions", {"participants": ["kongzi", "zhuangzi"], "topic": "test", "goal": "test"})
        self.create()
        self.assertEqual(len(self.request("/api/sessions")["sessions"]), 1)
        with self.assertRaises(HTTPError):
            self.request(f"/api/artifacts/{self.sid}/runtime.json")
        self.assertNotIn("runtime", self.request(f"/api/sessions/{self.sid}"))

    def test_http_create_generate_finish_edit_and_replay(self):
        self.create()
        self.action("start")
        raw = {"text": "Original synthetic reply", "responds_to": ["opening"], "stance": "opening",
               "claims": [{"text": "Synthetic idea", "novelty": "new"}], "questions": [], "needs_fact_check": False}
        settings = {"model": "synthetic", "confirm_local_service": True}
        with patch.object(runner, "OllamaProvider") as provider:
            model = provider.return_value
            model.inspect_identity.return_value = {"model": "synthetic", "digest": "a"*64}
            model.last_metrics = {"done_reason": "stop"}
            model.complete.return_value = {"content": json.dumps(raw)}
            for _ in range(2):
                self.action("generate", settings)
                result = self.wait_job()
                self.assertEqual(result["job"]["status"], "done")
        self.assertEqual(result["session"]["status"], "finishing")
        ids = [t["turn_id"] for t in result["turns"]]
        with patch.object(editor, "OllamaProvider") as provider:
            model = provider.return_value
            model.inspect_identity.return_value = {"model": "synthetic", "digest": "a"*64}
            model.last_metrics = {"done_reason": "stop"}
            model.complete.return_value = {"content": json.dumps({"development": [{"heading": "Synthetic", "body": "Original synthetic summary", "turn_ids": ids}], "consensus": [], "disagreements": [], "open_questions": []})}
            self.action("edit", settings)
            result = self.wait_job()
        self.assertEqual(result["session"]["status"], "completed")
        self.assertIn("Original synthetic summary", self.request(f"/api/artifacts/{self.sid}/essay.md")["content"])
        self.assertIn(ids[0], self.request(f"/api/artifacts/{self.sid}/provenance.md")["content"])
        self.assertEqual(app.Application(self.root).view(self.sid)["article"], result["article"])

    def test_pause_remains_available_and_duplicate_requests_rejected(self):
        self.create()
        self.action("start")
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {"accepted": False, "reason": "synthetic_cancelled"}
        with patch.object(app, "generate_turn", side_effect=slow):
            settings = {"model": "synthetic", "confirm_local_service": True}
            self.action("generate", settings)
            self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(HTTPError):
                    self.action("generate", settings)
                self.action("pause")
                self.assertEqual(self.request(f"/api/sessions/{self.sid}")["session"]["status"], "paused")
            finally:
                release.set()
            self.wait_job()

    def test_poll_does_not_pair_stale_snapshot_with_new_job_completion(self):
        self.create()
        application = self.server.app
        application.jobs[self.sid] = {"status": "running"}
        original = app.rt.load
        def completing(path, root):
            bundle = original(path, root)
            with application.guard:
                application.jobs[self.sid]["status"] = "done"
            return bundle
        with patch.object(app.rt, "load", side_effect=completing):
            result = application.view(self.sid)
        self.assertEqual(result["job"]["status"], "running")
        self.assertEqual(application.view(self.sid)["job"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
