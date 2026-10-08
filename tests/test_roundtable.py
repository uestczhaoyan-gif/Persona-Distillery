"""Offline state protocol: late requests, references, stops and atomic recovery."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("roundtable_state", ROOT / "圆桌会议/scripts/roundtable.py")
rt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rt)


class RoundtableStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        shutil.copytree(ROOT / "圆桌会议/schemas", self.root / "圆桌会议/schemas")
        shutil.copytree(ROOT / "圆桌会议/personas", self.root / "圆桌会议/personas")
        for person_id in ("kongzi", "zhuangzi"):
            package = ROOT / "personas" / person_id
            target = self.root / "personas" / person_id
            manifest = rt.read_json(package / "manifest.json")
            target.mkdir(parents=True)
            shutil.copyfile(package / "manifest.json", target / "manifest.json")
            for role in ("persona_spec", "core_anchors", "dialogue_examples", "profile", "sources", "evidence", "context_memory", "structured_evidence", "structured_context_memory"):
                if role in manifest["files"]:
                    file = target / manifest["files"][role]
                    file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(package / manifest["files"][role], file)
        module = self.root / "直接对话"
        module.mkdir()
        (module / "persona-aliases.json").write_text('{"schema_version":"1.0","aliases":{}}', encoding="utf-8")
        self.config = rt.read_json(ROOT / "圆桌会议/_template/session.example.json")
        self.config["participants"] = self.config["participants"][:2]
        self.config["network_policy"] = "off"
        self.config["limits"]["max_turns"] = 2
        self.identifier = self.config["session_id"]

    def create(self):
        return rt.create(self.config, self.root, preview=True)

    def start(self):
        self.create()
        return rt.transition(self.identifier, "start", self.root)

    def ticket(self):
        return rt.request_turn(self.identifier, root=self.root)

    def turn(self, ticket):
        result = rt.read_json(ROOT / "圆桌会议/_template/turn.example.json")
        for key in ("session_id", "turn_id", "sequence", "section_index", "speaker_id"):
            result[key] = ticket[key]
        result["claims"][0]["claim_id"] = f"C-{ticket['sequence']:04d}"
        return result

    def state(self):
        return rt.load(rt.directory(self.identifier, self.root), self.root)

    def test_preview_is_explicit_and_capabilities_are_preserved(self):
        with self.assertRaisesRegex(ValueError, "explicit"):
            rt.create(self.config, self.root)
        self.create()
        manifest = rt.read_json(self.root / "personas/kongzi/manifest.json")
        self.assertFalse(manifest["capabilities"]["roundtable"])
        self.assertEqual(manifest["readiness"], "draft")

    def test_unknown_persona_schema_is_refused_before_adapter_read(self):
        path = self.root / "personas/kongzi/manifest.json"
        manifest = rt.read_json(path)
        manifest["schema_version"] = "2.0"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(rt, "checked_file", wraps=rt.checked_file) as checking:
            with self.assertRaisesRegex(ValueError, "schema_version"):
                rt.participant_pins(self.config, self.root, preview=True)
            self.assertEqual([c.args[1] for c in checking.call_args_list], ["manifest.json"])

    def test_pause_and_resume_discard_late_response(self):
        self.start()
        ticket = self.ticket()
        rt.transition(self.identifier, "pause", self.root)
        rt.transition(self.identifier, "resume", self.root)
        result = rt.commit_turn(ticket, self.turn(ticket), self.root)
        self.assertFalse(result["accepted"])
        self.assertEqual(self.state()["turns"], [])
        current = self.ticket()
        self.assertNotEqual(current["request_id"], ticket["request_id"])

    def test_duplicate_commit_and_turn_limit(self):
        self.start()
        first = self.ticket()
        self.assertTrue(rt.commit_turn(first, self.turn(first), self.root)["accepted"])
        self.assertFalse(rt.commit_turn(first, self.turn(first), self.root)["accepted"])
        second = self.ticket()
        result = rt.commit_turn(second, self.turn(second), self.root)
        self.assertEqual(result["status"], "finishing")
        with self.assertRaisesRegex(ValueError, "running"):
            self.ticket()

    def test_unknown_targets_claims_and_sources_are_rejected(self):
        self.start()
        ticket = self.ticket()
        for kind in ("target", "source", "claim"):
            turn = self.turn(ticket)
            if kind == "target":
                turn["responds_to"] = ["nonexistent"]
            elif kind == "source":
                turn["basis"]["source_ids"] = ["P-9999-999"]
            else:
                turn["claims"][0]["responds_to_claim_id"] = "C-9999"
            with self.assertRaises(ValueError):
                rt.commit_turn(ticket, turn, self.root)
        self.assertEqual(self.state()["turns"], [])

    def test_wrong_ticket_speaker_cannot_be_committed(self):
        self.start()
        ticket = self.ticket()
        turn = self.turn(ticket)
        turn["speaker_id"] = "zhuangzi"
        with self.assertRaisesRegex(ValueError, "identity"):
            rt.commit_turn(ticket, turn, self.root)

    def test_user_interjection_invalidates_request_and_must_be_addressed(self):
        self.start()
        old = self.ticket()
        rt.transition(self.identifier, "interject", self.root, "项目原创测试插话。")
        self.assertFalse(rt.commit_turn(old, self.turn(old), self.root)["accepted"])
        rt.transition(self.identifier, "resume", self.root)
        ticket = self.ticket()
        turn = self.turn(ticket)
        with self.assertRaisesRegex(ValueError, "interjection"):
            rt.commit_turn(ticket, turn, self.root)
        turn["responds_to"] = ["EV-0001"]
        self.assertTrue(rt.commit_turn(ticket, turn, self.root)["accepted"])
        self.assertIsNone(self.state()["session"]["current"]["pending_user_event_id"])

    def test_resume_refuses_same_version_modified_adapter(self):
        self.start()
        rt.transition(self.identifier, "pause", self.root)
        path = self.root / "圆桌会议/personas/kongzi.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Pinned"):
            rt.transition(self.identifier, "resume", self.root)

    def test_atomic_pointer_failure_keeps_previous_commit(self):
        self.start()
        ticket = self.ticket()
        old = self.state()
        original = rt.os.replace
        def fail_pointer(source, target):
            if Path(target).name == "current.json":
                raise OSError("injected pointer failure")
            return original(source, target)
        with patch.object(rt.os, "replace", side_effect=fail_pointer):
            with self.assertRaises(OSError):
                rt.commit_turn(ticket, self.turn(ticket), self.root)
        self.assertEqual(self.state(), old)
        self.assertTrue(rt.commit_turn(ticket, self.turn(ticket), self.root)["accepted"])

    def test_no_new_claim_plateau_stops(self):
        self.config["limits"]["no_new_claim_limit"] = 1
        self.start()
        ticket = self.ticket()
        turn = self.turn(ticket)
        turn["claims"][0]["novelty"] = "repeated"
        self.assertEqual(rt.commit_turn(ticket, turn, self.root)["status"], "finishing")

    def test_section_pause_then_final_section_stop(self):
        self.config["limits"]["max_turns"] = 4
        self.start()
        first = self.ticket()
        turn = self.turn(first)
        turn["moderation"]["stop_signal"] = "section_ready"
        self.assertEqual(rt.commit_turn(first, turn, self.root)["status"], "paused")
        rt.transition(self.identifier, "resume", self.root, "原创测试下一节问题")
        second = self.ticket()
        self.assertEqual(second["section_index"], 1)
        turn = self.turn(second)
        turn["moderation"]["stop_signal"] = "section_ready"
        self.assertEqual(rt.commit_turn(second, turn, self.root)["status"], "finishing")

    def test_cli_ticket_file_round_trip(self):
        config = self.root / "config.json"
        config.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        def command(*arguments):
            result = subprocess.run([sys.executable, str(ROOT / "圆桌会议/scripts/roundtable.py"),
                                     "--root", str(self.root), *arguments], capture_output=True,
                                    encoding="utf-8", timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        command("create", "--config", str(config), "--preview")
        command("start", "--session", self.identifier)
        ticket = command("request", "--session", self.identifier)
        ticket_file, turn_file = self.root / "ticket.json", self.root / "turn.json"
        ticket_file.write_text(json.dumps(ticket), encoding="utf-8")
        turn_file.write_text(json.dumps(self.turn(ticket), ensure_ascii=False), encoding="utf-8")
        result = command("commit", "--ticket", str(ticket_file), "--turn", str(turn_file))
        self.assertTrue(result["accepted"])

    def test_completion_requires_existing_paragraph_references(self):
        self.start()
        ticket = self.ticket()
        rt.commit_turn(ticket, self.turn(ticket), self.root)
        rt.transition(self.identifier, "finish", self.root)
        article = rt.read_json(ROOT / "圆桌会议/_template/article.example.json")
        article["simulation_notice"] = rt.NOTICE
        bad = copy.deepcopy(article)
        bad["development"][0]["turn_ids"] = ["RT-20260806-001-T-9999"]
        with self.assertRaisesRegex(ValueError, "paragraph"):
            rt.complete(self.identifier, bad, self.root)
        result = rt.complete(self.identifier, article, self.root)
        self.assertEqual(result["status"], "completed")
        path = rt.directory(self.identifier, self.root)
        pointer = rt.read_json(path / "current.json")
        self.assertTrue((path / pointer["snapshot"] / "essay.md").is_file())
        self.assertEqual(len(self.state()["turns"]), 1)

    def test_corrupted_snapshot_and_illegal_transitions_are_rejected(self):
        self.create()
        with self.assertRaisesRegex(ValueError, "Invalid pause"):
            rt.transition(self.identifier, "pause", self.root)
        path = rt.directory(self.identifier, self.root)
        pointer = rt.read_json(path / "current.json")
        (path / pointer["snapshot"] / "turns.jsonl").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.state()

    def test_nonlocal_paths_and_network_policy_are_rejected(self):
        with self.assertRaises(ValueError):
            rt.directory("../escape", self.root)
        self.config["network_policy"] = "facts_only"
        with self.assertRaisesRegex(ValueError, "network off"):
            self.create()
