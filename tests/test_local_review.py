"""Terminal workflow tests contain only original synthetic local inputs."""
import io
import json
import unittest
from unittest.mock import patch
import test_spec_review as fixtures
import local_review as cli


class Terminal(io.StringIO):
    def isatty(self):
        return True


class LocalReviewTests(unittest.TestCase):
    for _name in ("setUp", "save", "write_config", "start", "load", "approve", "build", "ready", "snapshot",
                  "provider", "run_generation", "make_review", "prepare", "merge", "setup_spec", "generate", "setup_review", "save_review"):
        locals()[_name] = getattr(fixtures.SpecReviewTests, _name)

    def run_cli(self, *args, terminal=True, confirm=True):
        output, error = Terminal(), Terminal()
        base = ["--library", str(self.library), "--job", self.job["job"]["id"]]
        if confirm:
            base.append("--confirm-local-storage")
        with patch.object(cli.sys, "stdin", Terminal() if terminal else io.StringIO()), patch.object(cli.sys, "stdout", output), patch.object(cli.sys, "stderr", error):
            code = cli.main(base + list(args))
        return code, output.getvalue(), error.getvalue()

    def test_nonterminal_and_missing_confirmation_precede_reads(self):
        with patch.object(cli.candidate_review, "read_run", side_effect=AssertionError("Read before terminal consent")):
            for kwargs in ({"terminal": False}, {"confirm": False}):
                code, output, error = self.run_cli("inspect-candidate", "--run", "a" * 64, **kwargs)
                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertNotIn(str(self.library), error)

    def test_spec_inspection_review_and_draft_real_synthetic_chain(self):
        self.setup_review()
        code, output, _ = self.run_cli("inspect-spec", "--spec", self.spec_id)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["spec_content_sha256"], self.decision["spec_content_sha256"])
        decision = self.base / "cli-decision.json"
        decision.write_text(json.dumps(self.decision), encoding="utf-8")
        code, output, _ = self.run_cli("review-spec", "--spec", self.spec_id, "--decision", str(decision))
        self.assertEqual(code, 0)
        reviewed = json.loads(output)
        code, output, _ = self.run_cli("build-draft", "--review", reviewed["review_sha256"])
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(output)["persona_generated"])

    def test_decision_inside_input_is_rejected_without_echo(self):
        self.setup_review()
        decision = self.incoming / "private-decision.json"
        decision.write_text(json.dumps(self.decision), encoding="utf-8")
        code, output, error = self.run_cli("review-spec", "--spec", self.spec_id, "--decision", str(decision))
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertNotIn("private-decision", error)

    def test_terminal_control_characters_are_escaped(self):
        with patch.object(cli.candidate_review, "read_run", return_value={"text": "\x1b[31mOriginal"}):
            code, output, _ = self.run_cli("inspect-candidate", "--run", "a" * 64)
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", output)
        self.assertIn("\\u001b", output)

    def test_spec_template_is_bound_and_not_an_approval(self):
        self.setup_review()
        code, output, _ = self.run_cli("template-spec", "--spec", self.spec_id)
        self.assertEqual(code, 0)
        value = json.loads(output)
        self.assertEqual(value["spec_content_sha256"], self.decision["spec_content_sha256"])
        with self.assertRaises(ValueError):
            cli.spec_review.prepare_decision(self.current, value)
        value.update(action="reject", reviewer="Synthetic human", note="Original fixture lacks evidence")
        self.assertEqual(cli.spec_review.prepare_decision(self.current, value)["action"], "reject")

    def test_candidate_template_enumerates_exact_records_without_decisions(self):
        self.prepare()
        directory = self.library / self.job["job"]["id"] / "candidates"
        run_id = next(directory.iterdir()).name
        code, output, _ = self.run_cli("template-candidate", "--run", run_id)
        self.assertEqual(code, 0)
        value = json.loads(output)
        current = cli.candidate_review.read_run(self.library, self.job["job"]["id"], run_id, confirm_local_storage=True)
        self.assertEqual(value["candidates_sha256"], current["candidates_sha256"])
        self.assertEqual([x["record_id"] for x in value["decisions"]], ["E-0001"])
        self.assertTrue(all(x["action"] == "" for x in value["decisions"]))
        with self.assertRaises(ValueError):
            cli.candidate_review.prepare_review(current["bundle"], current["chunks"], current["policy"], value)


if __name__ == "__main__":
    unittest.main()
