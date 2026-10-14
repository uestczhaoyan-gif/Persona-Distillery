"""Local lecture workspace facade; content generation remains in the lecture module."""
from __future__ import annotations

import copy
from pathlib import Path
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "定题演讲/scripts"))
import prepare_lecture as prep
import lecture_runner as outlines
import lecture_draft as drafts


class Lectures:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.jobs = {}
        self.guard = threading.Lock()

    def library(self):
        result = []
        for person in sorted(set(prep.registry(self.root).values())):
            package = self.root / "personas" / person
            manifest = prep.read_json(prep.checked_file(package, "manifest.json"))
            if manifest.get("schema_version") != "1.0" or manifest.get("subject_kind") != "historical_public":
                continue
            ready = (manifest.get("visibility") == "public" and manifest.get("readiness") in {"chat-ready", "simulation-ready", "publish-ready"}
                     and manifest.get("capabilities", {}).get("themed_lecture") is True)
            result.append({"person_id": person, "display_name": manifest["display_name"], "requires_preview": not ready})
        return result

    def create(self, data):
        config = {key: data[key] for key in ("person_id", "topic", "audience", "goal", "minutes", "as_of", "facts")}
        brief = prep.prepare(config, root=self.root, preview=data.get("preview") is True)
        return {"session_id": brief["session_id"], "status": "prepared"}

    def view(self, identifier):
        with self.guard:
            job = copy.deepcopy(self.jobs.get(identifier))
        path, brief, _, _, _ = outlines.load_prepared(identifier, self.root)
        outline, draft = None, None
        if (path / "outline-current.json").exists():
            outline, _ = drafts.snapshot(path, "outline-current.json", "outlines", "outline.json")
        if (path / "draft-current.json").exists():
            draft, _ = drafts.snapshot(path, "draft-current.json", "drafts", "draft.json")
        status = "drafted" if draft else outline["status"] if outline else "prepared"
        fields = ("session_id", "person_id", "display_name", "topic", "audience", "goal", "minutes", "as_of", "facts", "preview", "fact_verification")
        return {"brief": {key: brief[key] for key in fields}, "status": status, "outline": outline, "draft": draft, "job": job}

    def sessions(self):
        result = []
        for path in sorted((self.root / "定题演讲/sessions").glob("SP-*"), reverse=True)[:100]:
            try:
                value = self.view(path.name)
                result.append({"session_id": path.name, "title": value["brief"]["topic"], "status": value["status"]})
            except (ValueError, OSError, KeyError, TypeError):
                continue
        return result

    def artifact(self, identifier, name):
        path, _, _, _, _ = outlines.load_prepared(identifier, self.root)
        with outlines.storage.locked(path):
            if name in {"00-brief.md", "01-research.md"}:
                return prep.checked_file(path, name).read_text(encoding="utf-8")
            if name == "02-outline.md":
                pointer, folder, payload = "outline-current.json", "outlines", "outline.json"
            elif name in {"speech.md", "provenance.md", "draft.json"}:
                pointer, folder, payload = "draft-current.json", "drafts", "draft.json"
            else:
                raise ValueError("Unknown lecture artifact")
            drafts.snapshot(path, pointer, folder, payload)
            relative = prep.read_json(path / pointer)["snapshot"]
            return prep.checked_file(path, relative + "/" + name).read_text(encoding="utf-8")

    def versions(self, identifier):
        path, _, _, _, _ = outlines.load_prepared(identifier, self.root)
        values, legacy_gap = drafts.history(path)
        return {"versions": [{"version_id": v["version_id"], "current": i == 0,
                              "title": v["draft"]["title"], "revision_request": v["draft"].get("revision_request"),
                              "character_count": v["draft"]["character_count"]} for i, v in enumerate(values)],
                "legacy_gap": legacy_gap}

    def version_artifact(self, identifier, version, name):
        if name not in {"speech.md", "provenance.md", "draft.json"}:
            raise ValueError("Unknown version artifact")
        path, _, _, _, _ = outlines.load_prepared(identifier, self.root)
        values, _ = drafts.history(path)
        for value in values:
            if value["version_id"] == version:
                return prep.checked_file(path, value["pointer"]["snapshot"] + "/" + name).read_text(encoding="utf-8")
        raise ValueError("Unknown committed revision")

    def action(self, identifier, action, data):
        if action not in {"outline", "draft", "revise"}:
            raise ValueError("Unknown lecture action")
        model = data.get("model")
        if data.get("confirm_local_service") is not True or not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError("Confirm installed local model")
        status = self.view(identifier)["status"]
        if status != {"outline": "prepared", "draft": "outlined", "revise": "drafted"}[action]:
            raise ValueError("Lecture action unavailable in current state")
        revision = prep.text(data.get("text"), "revision", 5000) if action == "revise" else None
        with self.guard:
            if self.jobs.get(identifier, {}).get("status") == "running":
                raise ValueError("Lecture generation already running")
            if sum(j["status"] == "running" for j in self.jobs.values()) >= 2:
                raise ValueError("Local lecture capacity reached")
            job = {"id": uuid.uuid4().hex, "action": action, "status": "running"}
            self.jobs[identifier] = job
        def run():
            try:
                if action == "outline":
                    result = outlines.generate_outline(identifier, model.strip(), root=self.root, confirm_local_service=True)
                else:
                    result = drafts.generate_draft(identifier, model.strip(), root=self.root, confirm_local_service=True, revision=revision)
                update = {"status": "done", "result": result}
            except Exception:
                update = {"status": "failed", "error": "生成未完成。请检查本机模型与材料状态；之前的简报和稿件已保留。"}
            with self.guard:
                job.update(update)
        threading.Thread(target=run, daemon=True).start()
        return {"job_id": job["id"], "status": "running"}
