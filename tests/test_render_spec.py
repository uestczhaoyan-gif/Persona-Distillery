"""Draft Markdown views preserve evidence and distinguish simulations from observations."""
import copy
import unittest

import test_spec_candidate as fixtures
from spec_candidate import normalize_spec
from spec_review import prepare_decision
from candidate_records import fingerprint
from render_spec import render_views


class RenderSpecTests(unittest.TestCase):
    source = fixtures.SpecCandidateTests.source
    normalize = fixtures.SpecCandidateTests.normalize
    merge = fixtures.SpecCandidateTests.merge
    setUp = fixtures.SpecCandidateTests.setUp

    def reviewed(self):
        candidate = normalize_spec(self.spec, self.catalog)
        current = {"candidate": candidate, "catalog": self.catalog}
        decision = {"spec_content_sha256": fingerprint(candidate), "action": "accept", "reviewer": "Synthetic reviewer",
                    "note": "Fixture only"}
        return {"result": prepare_decision(current, decision)}

    def test_views_keep_grounding_simulation_and_unfinished_status(self):
        reviewed = self.reviewed()
        original = copy.deepcopy(reviewed)
        docs = render_views(reviewed, self.catalog, "Original fictional teacher")
        self.assertEqual(len(docs), 11)
        self.assertIn("E-0001", docs["03-persona-spec.md"])
        self.assertIn("M-0001", docs["03-core-anchors.md"])
        self.assertIn("P-2026-001", docs["02-evidence.md"])
        self.assertIn("lines 1", docs["02-evidence.md"])
        self.assertIn("原创模拟", docs["03-dialogue-examples.md"])
        self.assertIn("不是固定回答步骤", docs["03-persona-spec.md"])
        self.assertIn("头像：未完成", docs["06-build-report.md"])
        self.assertNotIn("manifest.v2.json", docs)
        self.assertEqual(reviewed, original)

    def test_model_text_cannot_create_active_image_link_or_html(self):
        self.spec["profile"]["description"] = '![image](https://example.invalid/image.png) <img src="x">\n# injected'
        docs = render_views(self.reviewed(), self.catalog, "<b>Fixture</b>")
        profile = docs["00-profile.md"]
        self.assertNotIn("![image]", profile)
        self.assertNotIn("<img", profile)
        self.assertNotIn("<b>", profile)
        self.assertIn("\\# injected", profile)

    def test_rejection_and_forged_readiness_are_rejected(self):
        for change in (lambda r: r["result"].update(action="reject"),
                       lambda r: r["result"]["candidate"].update(avatar_ready=True),
                       lambda r: r["result"].update(publication_approved=True)):
            reviewed = self.reviewed()
            change(reviewed)
            with self.subTest(change=change), self.assertRaises(ValueError):
                render_views(reviewed, self.catalog, "Fixture")

    def test_empty_examples_or_anchors_are_not_fabricated(self):
        self.spec["anchors"], self.spec["examples"] = [], []
        docs = render_views(self.reviewed(), self.catalog, "Fixture")
        self.assertIn("材料不足", docs["03-core-anchors.md"])
        self.assertIn("尚无", docs["03-dialogue-examples.md"])
        self.assertNotIn("## 示例 1", docs["03-dialogue-examples.md"])


if __name__ == "__main__":
    unittest.main()
