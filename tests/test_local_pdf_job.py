"""PDF integration with original generated fixtures and explicit local errors."""
import unittest
from unittest.mock import patch
import test_local_distill as fixtures
from test_local_pdf import document


class PdfJobTests(unittest.TestCase):
    for _name in ("setUp", "save", "write_config", "start", "load", "approve", "build", "ready", "snapshot"):
        locals()[_name] = getattr(fixtures.LocalDistillationTests, _name)

    def pdf(self, **kwargs):
        (self.incoming / "source.txt").unlink(missing_ok=True)
        (self.incoming / "source.pdf").write_bytes(document(**kwargs))

    def test_review_gates_pdf_then_current_snapshot(self):
        self.pdf()
        self.start()
        self.approve("identity")
        with patch("local_pdf.extract_pdf", side_effect=AssertionError("Before review")):
            self.assertEqual(self.build()["status"], "material_review")
        self.approve("material")
        state = self.build()
        text = (self.snapshot(state) / "chunks.jsonl").read_text(encoding="utf-8")
        self.assertIn("Original synthetic PDF observation.", text)
        self.assertIn("local_only", text)
        self.assertEqual(self.build(), state)

    def test_encrypted_pdf_quarantine_has_specific_reason(self):
        self.pdf(encrypted=True)
        state = self.ready()
        self.assertEqual(state["status"], "needs_inputs")
        self.assertEqual(state["quarantined"][0]["reason"], "pdf_encrypted")

    def test_dependency_failure_does_not_block_other_valid_material(self):
        self.pdf()
        (self.incoming / "other.txt").write_text("Separate original source", encoding="utf-8")
        with patch("local_pdf.extract_pdf", side_effect=ValueError("pdf_dependency_missing")):
            state = self.ready()
        self.assertEqual(state["status"], "review_required")
        import json
        report = json.loads((self.snapshot(state) / "report.json").read_text(encoding="utf-8"))
        self.assertIn("pdf_dependency_missing", str(report))
        self.assertEqual(self.build(), state)
        import local_distill
        with self.assertRaisesRegex(ValueError, "requires update"):
            local_distill.build(self.library, self.job["job"]["id"], confirm_local_storage=True, reparse=True)
        updated = local_distill.build(self.library, self.job["job"]["id"], confirm_local_storage=True,
                                     update=True, reparse=True)
        self.assertNotEqual(updated["snapshot"], state["snapshot"])
        current = json.loads((self.snapshot(updated) / "report.json").read_text(encoding="utf-8"))
        self.assertTrue(current["explicit_reparse"])
        self.assertEqual(current["quarantined"], [])
        self.assertEqual(current["unique_text_sources"], 2)
        self.assertEqual(json.loads((self.snapshot(state) / "report.json").read_text(encoding="utf-8")), report)


if __name__ == "__main__":
    unittest.main()
