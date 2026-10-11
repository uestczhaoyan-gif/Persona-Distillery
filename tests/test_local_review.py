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

    def test_attempt_listing_reads_no_candidate_body_and_never_claims_current(self):
        from pathlib import Path
        self.setup_review()
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name in {"candidates.json", "spec-candidate.json", "chunks.jsonl", "review.json"}:
                raise AssertionError("Attempt listing read a private body")
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", guarded):
            code, output, error = self.run_cli("attempts")
        self.assertEqual(code, 0, error)
        entries = json.loads(output)["attempts"]
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(v["binding_valid"] and not v["current_source_verified"] for v in entries))
        self.assertTrue(all(v["state"] == "recorded_unverified" for v in entries))
        self.assertNotIn("Original", output)

    def test_attempt_listing_reports_partial_binding_without_private_diagnostic(self):
        self.ready()
        root = self.library / self.job["job"]["id"] / "candidates" / ("a" * 64)
        root.mkdir(parents=True)
        (root / ".incomplete").write_text("Synthetic interruption", encoding="utf-8")
        (root / "binding.json").write_text("PRIVATE INVALID BODY", encoding="utf-8")
        code, output, _ = self.run_cli("attempts")
        self.assertEqual(code, 0)
        entry = json.loads(output)["attempts"][0]
        self.assertFalse(entry["binding_valid"])
        self.assertEqual(entry["state"], "incomplete")
        self.assertNotIn("PRIVATE", output)

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

    def test_full_synthetic_terminal_chain_to_preview_and_withdrawal(self):
        import local_candidates
        import local_spec
        import local_package
        self.ready()
        provider = self.provider()
        def response(messages, tools):
            chunk = json.loads(messages[1]["content"])["chunks"][0]
            return {"content": json.dumps({"evidence": [{"card_type": "synthesis", "claim": "Original synthetic tendency",
                "evidence_summary": "Original fixture", "interpretation": "Tentative", "limitations": "Synthetic only",
                "chunk_ids": [chunk["chunk_id"]], "topics": [], "confidence": "low"}], "memories": [], "gaps": []})}
        provider.complete.side_effect = response
        def model_command(main, args):
            output, error = Terminal(), Terminal()
            with patch.object(cli.sys, "stdin", Terminal()), patch.object(cli.sys, "stdout", output), patch.object(cli.sys, "stderr", error):
                code = main(["--library", str(self.library), "--job", self.job["job"]["id"],
                             "--model", "fixture", "--confirm-local-storage", "--confirm-local-service"] + args)
            self.assertEqual(code, 0, error.getvalue())
            return json.loads(output.getvalue())
        def command(*args):
            code, output, error = self.run_cli(*args)
            self.assertEqual(code, 0, error)
            return json.loads(output)
        run_id = model_command(local_candidates.main, [])["run_sha256"]
        decision = command("template-candidate", "--run", run_id)
        decision.update(reviewer="Synthetic reviewer", note="Fixture only")
        for item in decision["decisions"]:
            item.update(action="accept", reason="Synthetic acceptance")
        path = self.base / "decision-candidate.json"
        path.write_text(json.dumps(decision), encoding="utf-8")
        review_id = command("review-candidate", "--run", run_id, "--decision", str(path))["review_sha256"]
        merge_id = command("merge", "--review", review_id)["merge_sha256"]
        constructor = patch.object(local_spec, "OllamaProvider").start()
        self.addCleanup(patch.stopall)
        spec_provider = constructor.return_value
        spec_provider.base_url = "http://127.0.0.1:11434"
        spec_provider.inspect_identity.return_value = {"model": "fixture", "digest": "b" * 64, "options": {}}
        spec_provider.last_metrics = {"done_reason": "stop"}
        spec_provider.complete.return_value = {"content": json.dumps({
            "profile": {"description": "Original fictional profile", "limitations": "Fixture only", "record_ids": ["E-0001"]},
            "attention": [], "judgment": [], "dialogue_style": [], "knowledge_limits": [],
            "language": {"mode": "undetermined", "adjustment": "Need more material", "record_ids": ["E-0001"]},
            "anchors": [], "examples": [], "gaps": ["Synthetic integration only"]})}
        spec_id = model_command(local_spec.main, ["--merge", merge_id])["spec_sha256"]
        decision = command("template-spec", "--spec", spec_id)
        decision.update(action="accept", reviewer="Synthetic reviewer", note="Fixture only")
        path = self.base / "decision-spec.json"
        path.write_text(json.dumps(decision), encoding="utf-8")
        review_id = command("review-spec", "--spec", spec_id, "--decision", str(path))["review_sha256"]
        result = command("build-draft", "--review", review_id)
        package = self.base / "personas-output" / result["build_sha256"] / self.job["subject"]["person_id"]
        context = local_package.load_local_context(package, confirm_local_storage=True, preview=True)
        self.assertIn("Original fictional profile", context.context)
        self.assertEqual(provider.complete.call_count, 1)
        self.assertEqual(spec_provider.complete.call_count, 1)
        self.assertFalse(result["persona_generated"])
        (self.incoming / "source.txt").unlink()
        with self.assertRaises(ValueError):
            local_package.load_local_context(package, confirm_local_storage=True, preview=True)


if __name__ == "__main__":
    unittest.main()
