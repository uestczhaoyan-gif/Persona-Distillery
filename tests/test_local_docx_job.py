"""DOCX task integration uses original synthetic external files only."""
import unittest
from unittest.mock import patch
import test_local_distill as fixtures
from test_local_docx import package


class DocxJobTests(unittest.TestCase):
    for _name in ("setUp", "save", "write_config", "start", "load", "approve", "build", "ready", "snapshot"):
        locals()[_name] = getattr(fixtures.LocalDistillationTests, _name)

    def docx(self, text):
        (self.incoming / "source.txt").unlink(missing_ok=True)
        (self.incoming / "source.docx").write_bytes(package('<w:p><w:r><w:t>' + text + '</w:t></w:r></w:p>'))

    def test_review_then_docx_snapshot_and_increment(self):
        self.docx("Original synthetic DOCX observation.")
        self.start()
        self.approve("identity")
        with patch("local_docx.extract_docx", side_effect=AssertionError("Before review")):
            self.assertEqual(self.build()["status"], "material_review")
        self.approve("material")
        with patch("socket.socket.connect", side_effect=AssertionError("network")):
            state = self.build()
        original = (self.snapshot(state) / "chunks.jsonl").read_bytes()
        self.assertIn(b"Original synthetic DOCX observation.", original)
        self.docx("A changed synthetic observation.")
        self.assertEqual(self.build()["status"], "material_review")
        self.approve("material")
        self.assertEqual(self.build()["status"], "input_changed_use_update")
        updated = self.build(update=True)
        self.assertNotEqual(state["snapshot"], updated["snapshot"])
        self.assertEqual((self.snapshot(state) / "chunks.jsonl").read_bytes(), original)

    def test_docx_sensitivity_and_malformed_archive(self):
        self.docx("Original test@example.invalid")
        self.assertEqual(self.ready()["status"], "sensitivity_review")
        self.approve("sensitivity")
        state = self.build()
        value = (self.snapshot(state) / "chunks.jsonl").read_text(encoding="utf-8")
        self.assertIn("EMAIL_REDACTED", value)
        self.assertNotIn("test@example.invalid", value)
        (self.incoming / "source.docx").write_bytes(b"Invalid synthetic archive")
        self.approve("material")
        self.assertEqual(self.build(update=True)["status"], "needs_inputs")


if __name__ == "__main__":
    unittest.main()
