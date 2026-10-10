"""Offline dialogue protocol, tool limits, transactional failures and persistence."""
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "直接对话/scripts"))
from chat_runtime import ChatSession, interactive
from ollama_provider import OllamaProvider, ProviderError


class StubProvider:
    supports_tools = True

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, messages, tools):
        self.requests.append(json.loads(json.dumps(messages)))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def answer(text="先找一个可以检验的新问题。"):
    return {"role": "assistant", "content": text}


def tool(name="search_memory", query="学习"):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": {"query": query}}}]}


class ChatRuntimeTests(unittest.TestCase):
    def test_natural_turn_needs_no_forced_retrieval(self):
        provider = StubProvider([answer(), answer("再换一个例子。")])
        session = ChatSession("kongzi", provider, preview=True)
        with patch("chat_runtime.search_memory", side_effect=AssertionError("unrequested search")):
            session.send("如何学习？")
            session.send("然后呢？")
        self.assertEqual(len(session.messages), 5)
        self.assertEqual(session.last_tool_ids, [])

    def test_actual_tool_ids_are_distinct_from_post_hoc_audit(self):
        provider = StubProvider([tool(), answer()])
        session = ChatSession("kongzi", provider, preview=True)
        session.send("学习的依据？")
        self.assertTrue(session.last_tool_ids)
        self.assertEqual(provider.requests[1][-1]["role"], "tool")
        audit = session.audit()
        self.assertEqual(audit["actual_tool_ids"], session.last_tool_ids)
        self.assertIn("audit_notice", audit["post_hoc_audit"])

    def test_failed_or_unsupported_tools_do_not_commit_the_turn(self):
        for response in (ProviderError("offline"), tool("write_file"), answer(""), {"role": "user", "content": "wrong"}):
            session = ChatSession("kongzi", StubProvider([response]), preview=True)
            with self.assertRaises(ValueError):
                session.send("问题")
            self.assertEqual(len(session.messages), 1)
            self.assertEqual(session.last_question, "")

    def test_tool_loop_has_a_hard_limit(self):
        provider = StubProvider([tool()] * 5)
        session = ChatSession("kongzi", provider, preview=True)
        with self.assertRaisesRegex(ValueError, "limit"):
            session.send("继续检索")
        self.assertEqual(len(provider.requests), 5)
        self.assertEqual(len(session.messages), 1)

    def test_history_budget_fails_before_provider_request(self):
        provider = StubProvider([answer()])
        session = ChatSession("kongzi", provider, preview=True, max_history_chars=10)
        with self.assertRaisesRegex(ValueError, "budget"):
            session.send("问题")
        self.assertEqual(provider.requests, [])

    def test_summary_is_explicit_and_resumes_only_same_person_version(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        session = ChatSession("kongzi", StubProvider([answer()]), preview=True)
        session.root = Path(temporary.name)
        session.send("原始私人问题不可保存")
        self.assertFalse((session.root / "直接对话/sessions").exists())
        identifier = session.save_summary("我确认的短摘要")
        saved = (session.sessions_dir() / identifier / "summary.json").read_text(encoding="utf-8")
        self.assertNotIn("原始私人问题", saved)
        session.resume_summary(identifier)
        self.assertEqual(len(session.messages), 2)
        with self.assertRaisesRegex(ValueError, "session ID"):
            session.resume_summary("../outside")
        data = json.loads(saved)
        data["person_id"] = "zhuangzi"
        (session.sessions_dir() / identifier / "summary.json").write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "identity/version"):
            session.resume_summary(identifier)

    def test_candidate_request_and_summary_do_not_mix_with_default(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = StubProvider([answer()])
            candidate = ChatSession("kongzi", provider, preview=True, prompt_profile="m2-v1")
            candidate.root = Path(directory)
            candidate.send("你好")
            self.assertEqual(provider.requests[0][0]["content"], candidate.compiled["context"])
            identifier = candidate.save_summary("原创测试摘要")
            candidate.resume_summary(identifier)
            current = ChatSession("kongzi", StubProvider([]), preview=True)
            current.root = Path(directory)
            with self.assertRaisesRegex(ValueError, "identity/version"):
                current.resume_summary(identifier)
            path = candidate.sessions_dir() / identifier / "summary.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("prompt_profile")
            path.write_text(json.dumps(data), encoding="utf-8")
            current.resume_summary(identifier)  # Old summaries belong to current only.
            with self.assertRaisesRegex(ValueError, "identity/version"):
                candidate.resume_summary(identifier)

    def test_interactive_research_new_and_exit(self):
        session = ChatSession("kongzi", StubProvider([answer()]), preview=True)
        lines = iter(["/research on", "问题", "/sources", "/new", "/exit"])
        output = []
        interactive(session, lambda _: next(lines), output.append)
        self.assertTrue(session.research)
        self.assertEqual(len(session.messages), 1)
        self.assertTrue(any("post_hoc_audit" in line for line in output))

    def test_summary_storage_accepts_equivalent_workspace_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = ChatSession("kongzi", StubProvider([]), preview=True)
            session.root = Path(temporary) / "unused" / ".."
            identifier = session.save_summary("确认保存")
            saved = Path(temporary).resolve() / "直接对话/sessions" / identifier / "summary.json"
            self.assertTrue(saved.is_file())
            self.assertEqual(session.sessions_dir(), saved.parent.parent)


class OllamaTransportTests(unittest.TestCase):
    def test_nonlocal_cloud_and_credential_urls_are_refused(self):
        for url in ("https://ollama.com", "http://192.168.0.1:11434", "http://u:p@localhost:11434", "http://localhost:11434/api", "http://localhost:11434?key=secret"):
            with self.assertRaises(ProviderError):
                OllamaProvider("local-model", url)
        with self.assertRaises(ProviderError):
            OllamaProvider("model:cloud")

    def test_metadata_preflight_refuses_remote_models_before_chat(self):
        provider = OllamaProvider("local-model")
        response = io.BytesIO(json.dumps({"remote_host": "https://ollama.com", "capabilities": ["tools"]}).encode())
        with patch.object(provider.opener, "open", return_value=response) as opening:
            with self.assertRaisesRegex(ProviderError, "Remote/cloud"):
                provider.complete([{"role": "user", "content": "private"}], [])
        request = opening.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/api/show"))
        self.assertNotIn(b"private", request.data)

    def test_chat_payload_and_malformed_response(self):
        provider = OllamaProvider("local-model", "http://localhost:11434")
        payloads = [io.BytesIO(b'{"capabilities":["tools"]}'),
                    io.BytesIO(json.dumps({"done": True, "message": answer()}).encode())]
        with patch.object(provider.opener, "open", side_effect=payloads) as opening:
            self.assertEqual(provider.complete([{"role": "user", "content": "hi"}], [{"type": "function"}]), answer())
        request = opening.call_args.args[0]
        data = json.loads(request.data)
        self.assertFalse(data["stream"])
        self.assertIn("tools", data)
        self.assertEqual(provider.base_url, "http://127.0.0.1:11434")
        with patch.object(provider.opener, "open", return_value=io.BytesIO(b'not json')):
            with self.assertRaisesRegex(ProviderError, "invalid JSON"):
                provider.complete([], [])

    def test_sampling_is_explicit_validated_and_sent(self):
        for kwargs in ({"temperature": float("nan")}, {"temperature": float("inf")},
                       {"temperature": True}, {"temperature": -1}, {"temperature": 3},
                       {"seed": True}, {"seed": 1.5}, {"seed": -1}, {"seed": 2147483648}):
            with self.assertRaises(ProviderError):
                OllamaProvider("local-model", **kwargs)
        self.assertNotIn("temperature", OllamaProvider("local-model").options())
        self.assertNotIn("seed", OllamaProvider("local-model").options())
        provider = OllamaProvider("local-model", temperature=.7, seed=20261008)
        with patch.object(provider, "post", side_effect=[{}, {"done": True, "message": answer()}]) as post:
            provider.complete([], [])
        self.assertEqual(post.call_args.args[1]["options"], {
            "num_ctx": 32768, "num_predict": 1600, "temperature": .7, "seed": 20261008})

    def test_model_identity_is_fresh_metadata_only_and_requires_unique_digest(self):
        provider = OllamaProvider("local-model")
        inventory = {"models": [{"name": "local-model:latest", "digest": "a" * 64}]}
        payloads = [io.BytesIO(json.dumps(x).encode()) for x in (
            {"capabilities": ["tools"], "template": "synthetic template"}, inventory,
            {"capabilities": [], "template": "changed template"}, inventory)]
        with patch.object(provider.opener, "open", side_effect=payloads) as opening:
            first = provider.inspect_identity()
            second = provider.inspect_identity()
        self.assertNotEqual(first["metadata_sha256"], second["metadata_sha256"])
        self.assertEqual(first["digest"], "a" * 64)
        self.assertNotIn("template", first)
        self.assertFalse(second["supports_tools"])
        self.assertEqual([x.args[0].get_method() for x in opening.call_args_list], ["POST", "GET", "POST", "GET"])
        for bad in ({"models": []}, {"models": [inventory["models"][0]] * 2},
                    {"models": [{"name": "local-model", "digest": "bad"}]},
                    {"models": [{"name": "local-model", "digest": "a" * 64, "remote_model": "remote"}]}):
            with patch.object(provider, "_request", side_effect=[{}, bad]):
                with self.assertRaises(ProviderError):
                    provider.inspect_identity()

    def test_response_metrics_do_not_retain_messages_or_stale_results(self):
        provider = OllamaProvider("local-model")
        result = {"done": True, "message": answer("synthetic private response"), "eval_count": 8,
                  "prompt_eval_count": 20, "done_reason": "length", "total_duration": True,
                  "eval_duration": -1, "unrecognized": "private"}
        with patch.object(provider, "post", side_effect=[{}, result, ProviderError("offline")]):
            provider.complete([], [])
            self.assertEqual(provider.last_metrics, {"eval_count": 8, "prompt_eval_count": 20, "done_reason": "length"})
            with self.assertRaises(ProviderError):
                provider.complete([], [])
        self.assertEqual(provider.last_metrics, {})

    def test_full_cli_against_loopback_http_stub(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, data))
                if self.path == "/api/show":
                    value = {"capabilities": ["completion", "tools"]}
                elif data["messages"][-1]["role"] == "tool":
                    value = {"done": True, "message": answer("这是离线 HTTP 桩的回答，不是人物质量结果。")}
                else:
                    value = {"done": True, "message": tool()}
                payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {**os.environ, "PYTHONUTF8": "1"}
            result = subprocess.run([sys.executable, str(ROOT / "直接对话/scripts/persona.py"),
                                     "chat", "kongzi", "--preview", "--provider", "ollama", "--model", "stub-local",
                                     "--temperature", "0.7", "--seed", "20261008",
                                     "--base-url", f"http://127.0.0.1:{server.server_port}", "--prompt", "学习的依据是什么？"],
                                    capture_output=True, text=True, encoding="utf-8", env=env, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("离线 HTTP 桩的回答", result.stdout)
            self.assertIn("默认不保存对话", result.stderr)
            self.assertEqual([p for p, _ in requests], ["/api/show", "/api/chat", "/api/chat"])
            self.assertEqual(requests[-1][1]["messages"][-1]["role"], "tool")
            self.assertEqual(requests[-1][1]["options"]["temperature"], .7)
            self.assertEqual(requests[-1][1]["options"]["seed"], 20261008)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
