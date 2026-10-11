"""HTML integration uses only synthetic external local inputs."""
import json
import unittest
from unittest.mock import patch
import test_local_distill as fixtures


class HtmlJobTests(unittest.TestCase):
    for _name in ("setUp", "save", "write_config", "start", "load", "approve", "build", "ready", "snapshot"):
        locals()[_name] = getattr(fixtures.LocalDistillationTests, _name)

    def html(self, text):
        (self.incoming / "source.txt").unlink(missing_ok=True)
        (self.incoming / "source.HTML").write_text(text, encoding="utf-8")

    def test_html_review_and_snapshot_no_network(self):
        self.html('<p>Original synthetic observation.</p><script>OMIT ME</script>')
        self.start()
        self.approve("identity")
        with patch("local_html.extract_html", side_effect=AssertionError("Before review")):
            self.assertEqual(self.build()["status"], "material_review")
        self.approve("material")
        with patch("socket.socket.connect", side_effect=AssertionError("Network")):
            state = self.build()
        chunks = [json.loads(x) for x in (self.snapshot(state) / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(chunks[0]["text"], "Original synthetic observation.")
        self.assertFalse(chunks[0]["direct_quote"])
        self.assertEqual(chunks[0]["delivery_channel"], "local_only")
        self.assertEqual(self.build(), state)

    def test_html_sensitive_text_requires_review_and_redaction(self):
        self.html('<p>Original fictional email: test@example.invalid</p>')
        self.assertEqual(self.ready()["status"], "sensitivity_review")
        self.approve("sensitivity")
        state = self.build()
        value = (self.snapshot(state) / "chunks.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("test@example.invalid", value)
        self.assertIn("EMAIL_REDACTED", value)

    def test_empty_html_quarantined_without_source_snapshot(self):
        self.html('<script>Only non-content</script>')
        state = self.ready()
        self.assertEqual(state["status"], "needs_inputs")
        self.assertEqual(state["quarantined"][0]["reason"], "invalid_or_empty_text")


if __name__ == "__main__":
    unittest.main()
