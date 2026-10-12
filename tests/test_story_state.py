"""Synthetic story decisions, costs, knowledge isolation and branch recovery."""
import copy
import json
from pathlib import Path
import sys
import unittest
import test_roundtable as fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "故事推演/scripts"))
import story


class StoryStateTests(unittest.TestCase):
    def setUp(self):
        fixtures.RoundtableStateTests.setUp(self)
        self.config = {"person_id": "kongzi", "title": "Original synthetic scenario", "premise": "A fictional workshop needs a decision.",
                       "rules": "Three days available; no supernatural knowledge.", "goal": "Compare costly approaches",
                       "resources": {"days": 6, "budget": 5},
                       "facts": [{"id": "W-0001", "text": "The workshop is open.", "known": True},
                                 {"id": "W-0002", "text": "SYNTHETIC_HIDDEN_CONDITION", "known": False}]}
        self.proposal = {"situation": "Choose a way to learn about the workshop.", "options": [
            {"label": "Ask participants", "reason": "Understand their concerns", "cost": {"days": 1}, "tradeoff": "Less time to plan"},
            {"label": "Try an experiment", "reason": "Observe a small trial", "cost": {"days": 1, "budget": 1}, "tradeoff": "Fewer supplies remain"}]}
        self.effect = {"observation": "An original synthetic reaction is observed.", "cause": "The selected action elicited a reaction.",
                       "resource_delta": {}, "reveal": [], "new_facts": []}
        self.created = story.create(self.config, root=self.root, preview=True)
        self.sid = self.created["session_id"]

    def state(self, branch="main"):
        return story.load(self.sid, branch, self.root)

    def apply(self, operation, payload=None, branch="main"):
        return story.mutate(self.sid, branch, self.state(branch)["version"], operation, payload, root=self.root)

    def test_three_choices_have_costs_and_end(self):
        for _ in range(3):
            self.apply("propose", self.proposal)
            self.apply("choose", 0)
            self.apply("effect", self.effect)
        state = self.state()
        self.assertEqual(state["status"], "ended")
        self.assertEqual(state["resources"]["days"], 3)
        self.assertEqual(len(state["history"]), 3)
        with self.assertRaises(ValueError):
            self.apply("propose", self.proposal)

    def test_hidden_world_and_engine_cause_never_enter_actor_context(self):
        self.assertNotIn("SYNTHETIC_HIDDEN", json.dumps(story.actor_view(self.state())))
        self.apply("propose", self.proposal)
        self.apply("choose", 0)
        self.effect.update(cause="SYNTHETIC_HIDDEN causal explanation", new_facts=[{"text": "ANOTHER_HIDDEN_FACT", "known": False}])
        self.apply("effect", self.effect)
        visible = json.dumps(story.actor_view(self.state()))
        self.assertNotIn("SYNTHETIC_HIDDEN", visible)
        self.assertNotIn("ANOTHER_HIDDEN", visible)

    def test_fork_restores_resources_and_knowledge_before_other_branch(self):
        point = self.apply("propose", self.proposal)
        self.apply("choose", 1)
        self.effect["reveal"] = ["W-0002"]
        self.apply("effect", self.effect)
        main = self.state()
        forked = story.fork(self.sid, point["snapshot"], root=self.root)
        branch = forked["branch_id"]
        self.assertEqual(self.state(branch)["resources"]["budget"], 5)
        self.assertNotIn("SYNTHETIC_HIDDEN", json.dumps(story.actor_view(self.state(branch))))
        self.apply("choose", 0, branch)
        self.effect["reveal"] = []
        self.apply("effect", self.effect, branch)
        self.assertEqual(self.state(), main)
        self.assertEqual(self.state(branch)["resources"]["budget"], 5)

    def test_pause_resume_invalidates_late_proposal(self):
        version = self.state()["version"]
        self.apply("pause")
        self.apply("resume")
        with self.assertRaisesRegex(ValueError, "Stale"):
            story.mutate(self.sid, "main", version, "propose", self.proposal, root=self.root)
        self.assertIsNone(self.state()["pending"])

    def test_invalid_cost_and_consequence_do_not_change_pointer(self):
        before = self.state()
        bad = copy.deepcopy(self.proposal)
        bad["options"][0]["cost"] = {"budget": 100}
        with self.assertRaises(ValueError):
            self.apply("propose", bad)
        self.assertEqual(self.state(), before)
        self.apply("propose", self.proposal)
        self.apply("choose", 0)
        before = self.state()
        self.effect["resource_delta"] = {"budget": -99}
        with self.assertRaises(ValueError):
            self.apply("effect", self.effect)
        self.assertEqual(self.state(), before)

    def test_preview_required_and_corrupt_snapshot_rejected(self):
        with self.assertRaises(ValueError):
            story.create(self.config, root=self.root)
        file = story.directory(self.sid, self.root) / "snapshots" / (self.created["snapshot"]+".json")
        file.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.state()


if __name__ == "__main__":
    unittest.main()
