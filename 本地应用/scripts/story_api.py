"""Local story facade: expose actor knowledge, versioned actions and verified outputs."""
from __future__ import annotations
import copy
from pathlib import Path
import sys
import threading
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "故事推演/scripts"))
import story
import story_runner
import story_render
import story_editor


class Stories:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.jobs = {}
        self.guard = threading.Lock()

    def library(self):
        result = []
        for person in sorted(set(story.store.registry(self.root).values())):
            m = story.store.read_json(story.store.checked_file(self.root / "personas" / person, "manifest.json"))
            if m.get("schema_version") != "1.0" or m.get("subject_kind") != "historical_public":
                continue
            ready = m.get("visibility") == "public" and m.get("readiness") in {"simulation-ready", "publish-ready"} and m.get("capabilities", {}).get("story_simulation") is True
            result.append({"person_id": person, "display_name": m["display_name"], "requires_preview": not ready})
        return result

    def create(self, data):
        config = {k: data[k] for k in ("person_id", "title", "premise", "rules", "goal", "resources", "facts")}
        return story.create(config, root=self.root, preview=data.get("preview") is True)

    def view(self, identifier, branch="main"):
        with self.guard:
            job = copy.deepcopy(self.jobs.get((identifier, branch)))
        state = story.load(identifier, branch, self.root)
        story.check_pins(state, self.root)
        fields = ("session_id", "branch_id", "version", "status", "display_name", "person_id", "preview", "simulation_notice", "pending")
        edition = None
        location = story.branch_path(story.directory(identifier, self.root), branch)
        if (location / "edition-current.json").exists():
            edition = story_editor.read_edition(identifier, branch, root=self.root)
        rendered = False
        if (location / "result-current.json").exists():
            for name in ("story.md", "timeline.md", "provenance.md"):
                self._read_artifact(identifier, branch, name)
            rendered = True
        return {k: state[k] for k in fields} | {"actor": story.actor_view(state), "job": job, "edition": edition, "rendered": rendered}

    def sessions(self):
        result = []
        for path in sorted((self.root / "故事推演/sessions").glob("ST-*"), reverse=True)[:100]:
            try:
                v = self.view(path.name)
                result.append({"session_id": path.name, "title": v["actor"]["title"], "status": v["status"]})
            except (ValueError, OSError, KeyError, TypeError):
                continue
        return result

    def branches(self, identifier):
        path = story.directory(identifier, self.root)
        branches, points = [], []
        for folder in sorted((path / "branches").iterdir()):
            v = self.view(identifier, folder.name)
            branches.append({k: v[k] for k in ("branch_id", "status", "version")})
        for file in (path / "snapshots").glob("*.json"):
            state = story.read_snapshot(path, file.stem)
            if state["status"] in {"ready", "choosing"}:
                points.append({"snapshot": file.stem, **{k: state[k] for k in ("branch_id", "version", "decisions", "status")}})
        return {"branches": branches, "fork_points": sorted(points, key=lambda p: (p["branch_id"], p["version"]))}

    def artifact(self, identifier, branch, name):
        path = story.directory(identifier, self.root)
        with story.store.locked(path):
            return self._read_artifact(identifier, branch, name)

    def _read_artifact(self, identifier, branch, name):
        # Published files and pointers are immutable/atomic. Status reads must not
        # compete for the writer lock while a narrative job commits its edition.
        if name not in {"story.md", "timeline.md", "provenance.md", "narrative.md", "narrative-provenance.md"}:
            raise ValueError("Unknown story artifact")
        path = story.directory(identifier, self.root)
        if name in {"narrative.md", "narrative-provenance.md"}:
            edition = story_editor.read_edition(identifier, branch, root=self.root)
            file_name = "story.md" if name == "narrative.md" else "provenance.md"
            return story.store.checked_file(path, edition["directory"] + "/" + file_name).read_text(encoding="utf-8")
        state = story.load(identifier, branch, self.root)
        story.check_pins(state, self.root)
        location = story.branch_path(path, branch)
        current = story.store.read_json(story.store.checked_file(location, "current.json"))
        result = story.store.read_json(story.store.checked_file(location, "result-current.json"))
        if (result.get("session_id") != identifier or result.get("branch_id") != branch
                or result.get("source_snapshot") != current["snapshot"]
                or result.get("directory") != "rendered/" + current["snapshot"] or state["status"] != "ended"):
            raise ValueError("Result does not match current branch")
        file = story.store.checked_file(path, result["directory"] + "/" + name)
        if story.store.sha(file) != result["checksums"][name]:
            raise ValueError("Result changed")
        return file.read_text(encoding="utf-8")

    def action(self, identifier, branch, action, data):
        if action == "fork":
            return story.fork(identifier, data.get("snapshot"), root=self.root)
        if action in {"choose", "pause", "resume"}:
            version = data.get("version")
            if type(version) is not int:
                raise ValueError("Version required")
            return story.mutate(identifier, branch, version, action, data.get("index") if action == "choose" else None, root=self.root)
        if action == "render":
            return story_render.render(identifier, branch=branch, root=self.root)
        if action not in {"generate", "edit"}:
            raise ValueError("Unknown story action")
        model = data.get("model")
        if data.get("confirm_local_service") is not True or not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError("Confirm installed local model")
        state = self.view(identifier, branch)
        if state["status"] not in ({"ended"} if action == "edit" else {"ready", "awaiting_effect"}):
            raise ValueError("Generation unavailable")
        if action == "edit" and state["edition"] is not None:
            raise ValueError("Narrative already exists")
        key = (identifier, branch)
        with self.guard:
            if self.jobs.get(key, {}).get("status") == "running" or sum(j["status"] == "running" for j in self.jobs.values()) >= 2:
                raise ValueError("Story generation already running or capacity reached")
            job = {"id": uuid.uuid4().hex, "status": "running", "action": action}
            self.jobs[key] = job
        def run():
            try:
                generate = story_editor.edit if action == "edit" else story_runner.generate
                result = generate(identifier, model.strip(), branch=branch, root=self.root, confirm_local_service=True)
                update = {"status": "done", "result": result}
            except Exception:
                update = {"status": "failed", "error": "生成未完成。请检查本机模型与分支状态；已提交的选择和后果已保留。"}
            with self.guard:
                job.update(update)
        threading.Thread(target=run, daemon=True).start()
        return {"job_id": job["id"], "status": "running"}
