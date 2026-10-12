"""Only actual selected path enters story rendering; hidden facts stay out of prose."""
import unittest
import test_story_state as fixtures
import story_render as renderer


class StoryRenderTests(unittest.TestCase):
    setUp = fixtures.StoryStateTests.setUp
    state = fixtures.StoryStateTests.state
    apply = fixtures.StoryStateTests.apply

    def finish(self, index=0, branch="main"):
        for _ in range(3):
            self.apply("propose", self.proposal, branch)
            self.apply("choose", index, branch)
            self.apply("effect", self.effect, branch)

    def body(self, result, name="story.md"):
        return (fixtures.story.directory(self.sid, self.root) / result["directory"] / name).read_text(encoding="utf-8")

    def test_render_binds_actual_path_and_is_repeatable(self):
        self.finish()
        result = renderer.render(self.sid, root=self.root)
        content = self.body(result)
        self.assertIn("Ask participants", content)
        self.assertNotIn("Try an experiment", content)
        self.assertNotIn("SYNTHETIC_HIDDEN", content)
        self.assertIn("决策 3", self.body(result, "timeline.md"))
        self.assertIn("simulation", self.body(result, "provenance.md"))
        self.assertEqual(renderer.render(self.sid, root=self.root), result)

    def test_alternative_branch_does_not_change_main_render(self):
        fork = fixtures.story.fork(self.sid, self.created["snapshot"], root=self.root)
        self.finish()
        main = renderer.render(self.sid, root=self.root)
        original = self.body(main)
        self.finish(1, fork["branch_id"])
        alternative = renderer.render(self.sid, branch=fork["branch_id"], root=self.root)
        self.assertIn("Try an experiment", self.body(alternative))
        self.assertNotIn("Ask participants", self.body(alternative))
        self.assertEqual(self.body(main), original)

    def test_incomplete_and_tampered_outputs_are_refused(self):
        with self.assertRaises(ValueError):
            renderer.render(self.sid, root=self.root)
        self.finish()
        result = renderer.render(self.sid, root=self.root)
        file = fixtures.story.directory(self.sid, self.root) / result["directory"] / "story.md"
        file.write_text("Original manual change retained", encoding="utf-8")
        with self.assertRaises(ValueError):
            renderer.render(self.sid, root=self.root)
        self.assertEqual(file.read_text(encoding="utf-8"), "Original manual change retained")


if __name__ == "__main__":
    unittest.main()
