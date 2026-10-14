#!/usr/bin/env python3
"""Loopback application gateway reusing the roundtable protocol and local models."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lecture_api import Lectures
from story_api import Stories
from overview import overview
from chat_api import Chats
from model_status import model_status
sys.path.insert(0, str(ROOT / "圆桌会议/scripts"))
import roundtable as rt
from roundtable_runner import generate_turn
from roundtable_editor import generate_article


class Application:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.token = secrets.token_urlsafe(32)
        self.jobs = {}
        self.guard = threading.Lock()
        self.lectures = Lectures(self.root)
        self.stories = Stories(self.root)
        self.chats = Chats(self.root)

    def library(self):
        template = rt.read_json(self.root / "圆桌会议/_template/session.example.json")
        result = []
        for person in template["participants"]:
            manifest = rt.read_json(rt.checked_file(self.root / "personas" / person["person_id"], "manifest.json"))
            rt.require_legacy_manifest(manifest)
            if manifest.get("subject_kind") != "historical_public":
                continue
            result.append({"person_id": person["person_id"], "display_name": manifest["display_name"],
                           "package_version": manifest["package_version"], "adapter_version": person["adapter_version"],
                           "requires_preview": not (manifest.get("visibility") == "public"
                               and manifest.get("readiness") in {"simulation-ready", "publish-ready"}
                               and manifest.get("capabilities", {}).get("roundtable") is True)})
        return result

    def create(self, data):
        ids = data.get("participants")
        catalog = {p["person_id"]: p for p in self.library()}
        if (not isinstance(ids, list) or not 2 <= len(ids) <= 6
                or any(not isinstance(i, str) or i not in catalog for i in ids) or len(set(ids)) != len(ids)):
            raise ValueError("Select distinct available participants")
        topic, goal = data.get("topic"), data.get("goal")
        if any(not isinstance(s, str) or not s.strip() or len(s) > 2000 for s in (topic, goal)):
            raise ValueError("Topic and goal required")
        limit = data.get("max_turns", 6)
        if type(limit) is not int or not 2 <= limit <= 24:
            raise ValueError("Invalid turn limit")
        preview = data.get("preview") is True
        if any(catalog[i]["requires_preview"] for i in ids) and not preview:
            raise ValueError("Draft preview requires confirmation")
        config = rt.read_json(self.root / "圆桌会议/_template/session.example.json")
        config.update(session_id=f"RT-{datetime.now():%Y%m%d}-{uuid.uuid4().int % 10**12:012d}",
                      title=topic.strip(), topic=topic.strip(), goal=goal.strip(), network_policy="off",
                      preview_override=preview, created_at=rt.now(), updated_at=rt.now())
        config["participants"] = [{k: catalog[i][k] for k in ("person_id", "display_name", "package_version", "adapter_version")}
                                  | {"preview_override": preview} for i in ids]
        config["limits"]["max_turns"] = limit
        config["current"].update(question=topic.strip(), next_speaker=ids[0])
        return rt.create(config, self.root, preview=preview)

    def view(self, identifier):
        path = rt.directory(identifier, self.root)
        # Read job first: a reported completion must precede the snapshot read.
        # The reverse order can pair an older snapshot with a newly completed job.
        with self.guard:
            job = copy.deepcopy(self.jobs.get(identifier))
        # The pointer selects an immutable snapshot. Reads need not contend with commits.
        bundle = rt.load(path, self.root)
        return {k: bundle[k] for k in ("session", "turns", "events", "article")} | {"job": job}

    def sessions(self):
        parent = self.root / "圆桌会议/sessions"
        result = []
        for path in sorted(parent.glob("RT-*"), reverse=True)[:100]:
            try:
                value = self.view(path.name)["session"]
                result.append({k: value[k] for k in ("session_id", "title", "status", "updated_at")})
            except (ValueError, OSError, KeyError):
                continue
        return result

    def artifact(self, identifier, name):
        if name not in {"transcript.md", "essay.md", "provenance.md", "article.json"}:
            raise ValueError("Unknown artifact")
        path = rt.directory(identifier, self.root)
        with rt.locked(path):
            rt.load(path, self.root)
            pointer = rt.read_json(path / "current.json")
            if name not in pointer["checksums"]:
                raise ValueError("Artifact is not ready")
            return rt.checked_file(path, pointer["snapshot"] + "/" + name).read_text(encoding="utf-8")

    def action(self, identifier, action, data):
        if action in {"start", "pause", "resume", "interject", "finish"}:
            text = data.get("text", "")
            if not isinstance(text, str) or len(text) > 10000:
                raise ValueError("Invalid text")
            return rt.transition(identifier, action, self.root, text)
        if action not in {"generate", "edit"}:
            raise ValueError("Unknown action")
        model = data.get("model")
        if data.get("confirm_local_service") is not True or not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError("Confirm local model and provide its name")
        state = self.view(identifier)["session"]
        if state["status"] != ("running" if action == "generate" else "finishing"):
            raise ValueError("Action unavailable in this state")
        with self.guard:
            if self.jobs.get(identifier, {}).get("status") == "running":
                raise ValueError("Generation already running")
            if sum(j["status"] == "running" for j in self.jobs.values()) >= 2:
                raise ValueError("Local generation capacity reached")
            job = {"id": uuid.uuid4().hex, "action": action, "status": "running", "started_at": rt.now()}
            self.jobs[identifier] = job
        def run():
            try:
                result = (generate_turn if action == "generate" else generate_article)(
                    identifier, model.strip(), root=self.root, confirm_local_service=True)
                update = {"status": "done", "result": result}
            except Exception:
                # Do not echo model output or sensitive local exception details into the UI/log.
                update = {"status": "failed", "error": "生成未完成。请检查本机模型与会话状态；此前记录已保留。"}
            with self.guard:
                job.update(update, finished_at=rt.now())
        threading.Thread(target=run, daemon=True).start()
        return {"job_id": job["id"], "status": "running"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, value, kind="application/json; charset=utf-8"):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8") if kind.startswith("application/json") else value.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def permitted(self, api):
        expected = f"127.0.0.1:{self.server.server_port}"
        if (self.headers.get("Host") != expected or self.headers.get("Origin") not in (None, "http://" + expected)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            return False
        return not api or secrets.compare_digest(self.headers.get("X-Local-Token", ""), self.server.app.token)

    def do_GET(self):
        path = urlsplit(self.path).path
        api = path.startswith("/api/")
        if not self.permitted(api):
            return self.respond(403, {"error": "仅允许本机工作台访问。"})
        try:
            app = self.server.app
            if path == "/api/model-status":
                return self.respond(200, model_status())
            if path == "/api/overview":
                return self.respond(200, overview(app))
            if path == "/api/story-library":
                return self.respond(200, {"personas": app.stories.library()})
            if path == "/api/stories":
                return self.respond(200, {"sessions": app.stories.sessions()})
            if path == "/api/library":
                return self.respond(200, {"personas": app.library()})
            if path == "/api/sessions":
                return self.respond(200, {"sessions": app.sessions()})
            if path == "/api/lecture-library":
                return self.respond(200, {"personas": app.lectures.library()})
            if path == "/api/lectures":
                return self.respond(200, {"sessions": app.lectures.sessions()})
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "story-branches"]:
                return self.respond(200, app.stories.branches(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "stories"]:
                return self.respond(200, app.stories.view(parts[2], parts[3]))
            if len(parts) == 5 and parts[:2] == ["api", "story-artifacts"]:
                return self.respond(200, {"content": app.stories.artifact(parts[2], parts[3], parts[4])})
            if len(parts) == 3 and parts[:2] == ["api", "chats"]:
                try:
                    value = app.chats.view(parts[2])
                except KeyError:
                    return self.respond(404, {'error': '内存对话已结束，请重新开始。'})
                return self.respond(200, value)
            if len(parts) == 3 and parts[:2] == ["api", "lectures"]:
                return self.respond(200, app.lectures.view(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "lecture-artifacts"]:
                return self.respond(200, {"content": app.lectures.artifact(parts[2], parts[3])})
            if len(parts) == 3 and parts[:2] == ["api", "sessions"]:
                return self.respond(200, app.view(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "artifacts"]:
                return self.respond(200, {"content": app.artifact(parts[2], parts[3])})
            static = {"/model-status.js": ("model-status.js", "text/javascript; charset=utf-8"),
                      "/chat": ("chat.html", "text/html; charset=utf-8"),
                      "/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
                      "/home": ("home.html", "text/html; charset=utf-8"),
                      "/home.js": ("home.js", "text/javascript; charset=utf-8"),
                      "/": ("index.html", "text/html; charset=utf-8"),
                      "/lecture": ("lecture.html", "text/html; charset=utf-8"),
                      "/story": ("story.html", "text/html; charset=utf-8"),
                      "/story.js": ("story.js", "text/javascript; charset=utf-8"),
                      "/lecture.js": ("lecture.js", "text/javascript; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in static:
                name, kind = static[path]
                content = (ROOT / "本地应用/web" / name).read_text(encoding="utf-8")
                return self.respond(200, content.replace("__LOCAL_TOKEN__", app.token), kind)
            return self.respond(404, {"error": "入口不存在。"})
        except (ValueError, OSError, KeyError, TypeError):
            return self.respond(409, {"error": "暂时无法读取，请检查会话或稍后刷新。"})

    def do_POST(self):
        if not self.permitted(True):
            return self.respond(403, {"error": "仅允许本机工作台操作。"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 50000 or self.headers.get_content_type() != "application/json":
                raise ValueError("Invalid body")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Object required")
            parts = urlsplit(self.path).path.strip("/").split("/")
            if parts == ["api", "sessions"]:
                return self.respond(201, self.server.app.create(data))
            if parts == ["api", "chats"]:
                return self.respond(201, self.server.app.chats.create(data))
            if len(parts) == 4 and parts[:2] == ["api", "chats"]:
                return self.respond(200, self.server.app.chats.action(parts[2], parts[3], data))
            if parts == ["api", "stories"]:
                return self.respond(201, self.server.app.stories.create(data))
            if len(parts) == 5 and parts[:2] == ["api", "stories"]:
                return self.respond(200, self.server.app.stories.action(parts[2], parts[3], parts[4], data))
            if parts == ["api", "lectures"]:
                return self.respond(201, self.server.app.lectures.create(data))
            if len(parts) == 4 and parts[:2] == ["api", "lectures"]:
                return self.respond(200, self.server.app.lectures.action(parts[2], parts[3], data))
            if len(parts) == 4 and parts[:2] == ["api", "sessions"]:
                return self.respond(200, self.server.app.action(parts[2], parts[3], data))
            return self.respond(404, {"error": "操作不存在。"})
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            return self.respond(409, {"error": "操作未执行。请检查必填项、预览确认和当前会话状态。"})


def make_server(port=8765, root=ROOT):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = Application(root)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with make_server(args.port) as server:
        print(f"本地工作台 http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
