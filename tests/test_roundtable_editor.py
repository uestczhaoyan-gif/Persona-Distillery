"""Editorial pipeline uses original synthetic outputs, never real model evaluation."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import test_roundtable as fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "圆桌会议/scripts"))
import roundtable_editor as editor


class RoundtableEditorTests(unittest.TestCase):
    for _name in ("setUp", "create", "start", "ticket", "turn", "state"):
        locals()[_name] = getattr(fixtures.RoundtableStateTests, _name)

    def prepare(self):
        self.start()
        for _ in range(2):
            ticket = self.ticket()
            fixtures.rt.commit_turn(ticket, self.turn(ticket), self.root)
        self.ids = [t["turn_id"] for t in self.state()["turns"]]
        self.raw = {"development": [{"heading": "Synthetic discussion", "body": "Original synthetic summary.", "turn_ids": self.ids}],
                    "consensus": [{"text": "Synthetic shared starting point.", "turn_ids": self.ids}],
                    "disagreements": [{"summary": "Synthetic different approaches.", "participants": ["kongzi", "zhuangzi"], "turn_ids": self.ids}],
                    "open_questions": [{"text": "Synthetic next question?", "turn_ids": [self.ids[1]]}]}
        constructor = patch.object(editor, "OllamaProvider").start()
        self.addCleanup(patch.stopall)
        self.model = constructor.return_value
        self.model.inspect_identity.return_value = {"model": "synthetic", "digest": "a" * 64}
        self.model.last_metrics = {"done_reason": "stop"}
        self.response()

    def response(self):
        self.model.complete.return_value = {"content": json.dumps(self.raw)}

    def generate(self):
        return editor.generate_article(self.identifier, "synthetic", root=self.root, confirm_local_service=True)

    def test_completed_snapshot_has_essay_and_paragraph_provenance(self):
        self.prepare()
        before = copy.deepcopy(self.state()["turns"])
        self.assertTrue(self.generate()["accepted"])
        self.assertEqual(self.state()["turns"], before)
        self.assertEqual(self.state()["session"]["status"], "completed")
        messages, tools = self.model.complete.call_args.args
        self.assertEqual(set(json.loads(messages[1]["content"])), {"topic", "goal", "turns"})
        self.assertEqual(tools, [])
        path = fixtures.rt.directory(self.identifier, self.root)
        snapshot = path / fixtures.rt.read_json(path / "current.json")["snapshot"]
        self.assertIn("consensus/1", (snapshot / "provenance.md").read_text(encoding="utf-8"))
        self.assertIn("尚待人工审核", (snapshot / "essay.md").read_text(encoding="utf-8"))
        self.assertFalse((path / ".editor-generation.lock").exists())

    def test_unknown_turn_and_single_speaker_consensus_do_not_commit(self):
        self.prepare()
        for references in [["RT-20261012-999-T-0099"], [self.ids[0]]]:
            self.raw["consensus"][0]["turn_ids"] = references
            self.response()
            with self.assertRaises(ValueError):
                self.generate()
            self.assertIsNone(self.state()["article"])
            self.assertEqual(self.state()["session"]["status"], "finishing")

    def test_failure_and_truncation_preserve_discussion_for_retry(self):
        self.prepare()
        before = self.state()
        self.model.complete.side_effect = OSError("Synthetic unavailable")
        with self.assertRaises(ValueError):
            self.generate()
        self.model.complete.side_effect = None
        self.model.last_metrics = {"done_reason": "length"}
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.state(), before)
        self.model.last_metrics = {"done_reason": "stop"}
        self.assertTrue(self.generate()["accepted"])

    def test_concurrent_editor_rejected_and_other_completion_not_overwritten(self):
        self.prepare()
        response = self.model.complete.return_value
        def concurrent(*args):
            with self.assertRaisesRegex(ValueError, "already active"):
                self.generate()
            article = editor.normalize(response, self.state(), self.root)
            article["title"] = "Independent completed draft"
            fixtures.rt.complete(self.identifier, article, self.root)
            return response
        self.model.complete.side_effect = concurrent
        with self.assertRaises(ValueError):
            self.generate()
        self.assertEqual(self.state()["article"]["title"], "Independent completed draft")
        self.model.complete.assert_called_once()

    def test_disagreement_requires_each_participants_turn(self):
        self.prepare()
        self.raw["disagreements"][0]["turn_ids"] = [self.ids[0]]
        self.response()
        with self.assertRaises(ValueError):
            self.generate()
        self.assertIsNone(self.state()["article"])

    def test_wrong_state_or_missing_confirmation_never_calls_model(self):
        self.prepare()
        with self.assertRaises(ValueError):
            editor.generate_article(self.identifier, "synthetic", root=self.root)
        bundle = self.state()
        bundle["session"]["status"] = "paused"
        fixtures.rt.advance(bundle["session"])
        fixtures.rt.persist(fixtures.rt.directory(self.identifier, self.root), bundle, self.root)
        with self.assertRaises(ValueError):
            self.generate()
        self.model.complete.assert_not_called()

    def test_changed_snapshot_rejects_editor_output(self):
        self.prepare()
        response = self.model.complete.return_value
        def changed(*args):
            bundle = self.state()
            fixtures.rt.advance(bundle["session"])
            fixtures.rt.persist(fixtures.rt.directory(self.identifier, self.root), bundle, self.root)
            return response
        self.model.complete.side_effect = changed
        with self.assertRaises(ValueError):
            self.generate()
        self.assertIsNone(self.state()["article"])


if __name__ == "__main__":
    unittest.main()
