"""Read-only retrieval boundaries and bounded context; no provider calls."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "直接对话/scripts"))
from persona_memory import compile_context, doctor_persona, load_memory, search_memory


class PersonaMemoryTests(unittest.TestCase):
    def test_retrieval_is_deterministic_and_source_linked(self):
        first = search_memory("孔子", "学习", preview=True)
        self.assertEqual(first, search_memory("kongzi", "学习", preview=True))
        self.assertTrue(first["results"])
        for hit in first["results"]:
            self.assertEqual(hit["person_id"], "kongzi")
            self.assertTrue(hit["sources"][0]["locations"])
            self.assertTrue(hit["sources"][0]["reference"])

    def test_unrelated_or_empty_query_has_no_results(self):
        for query in ("", "zzzzunmatchedword777"):
            self.assertEqual(search_memory("孔子", query, preview=True)["results"], [])

    def test_exact_id_and_kind_filter(self):
        result = search_memory("kongzi", "E-0001", preview=True, kind="evidence")
        self.assertEqual(result["results"][0]["id"], "E-0001")
        self.assertTrue(all(hit["kind"] == "evidence" for hit in result["results"]))

    def test_readiness_and_selection_cannot_be_bypassed_by_search(self):
        with self.assertRaisesRegex(ValueError, "authorization"):
            search_memory("kongzi", "学习")
        with self.assertRaisesRegex(ValueError, "Unknown persona"):
            search_memory("../kongzi", "学习", preview=True)

    def test_context_keeps_contract_and_adapter_order(self):
        result = compile_context("kongzi", preview=True)
        self.assertFalse(result["omitted_memory_files"])
        self.assertEqual(result["compiled_files"][-1], "直接对话/personas/kongzi.md")
        self.assertNotIn("research.local/", " ".join(result["compiled_files"]))
        self.assertEqual(len(result["context"]), result["characters"])

    def test_small_budget_never_truncates_rules_or_memory_file(self):
        large = compile_context("kongzi", preview=True)
        # Find a capacity that fits all rules but omits at least one whole memory.
        for budget in range(1000, large["characters"], 1000):
            try:
                result = compile_context("kongzi", preview=True, max_chars=budget)
            except ValueError:
                continue
            self.assertTrue(result["requires_search_tool"])
            self.assertLessEqual(result["characters"], budget)
            self.assertIn("search_memory", result["context"])
            self.assertEqual(result["compiled_files"][-1], "直接对话/personas/kongzi.md")
            break
        else:
            self.fail("Expected a valid partial-memory context capacity")
        with self.assertRaisesRegex(ValueError, "Required persona instructions"):
            compile_context("kongzi", preview=True, max_chars=1000)

    def test_bad_search_limits_fail(self):
        with self.assertRaises(ValueError):
            search_memory("kongzi", "学习", preview=True, top_k=0)

    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        shutil.copytree(ROOT / "personas/kongzi", root / "personas/kongzi")
        shutil.copytree(ROOT / "直接对话/prompts", root / "直接对话/prompts")
        (root / "直接对话/persona-aliases.json").write_text(json.dumps({"schema_version": "1.0", "aliases": {}}), encoding="utf-8")
        return root

    def test_withdrawn_and_local_only_records_are_not_retrieved(self):
        root = self.fixture()
        path = root / "personas/kongzi/processed/evidence.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records[0]["status"] = "withdrawn"
        records[1]["delivery_channel"] = "local_only"
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
        self.assertEqual(search_memory("kongzi", "E-0001", root, True, kind="evidence")["results"], [])
        self.assertEqual(search_memory("kongzi", "E-0002", root, True, kind="evidence")["results"], [])

    def test_unknown_source_is_an_error(self):
        root = self.fixture()
        path = root / "personas/kongzi/processed/evidence.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        records[0]["source_refs"][0]["source_id"] = "P-9999-999"
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown or missing source"):
            load_memory("kongzi", root, True)

    def test_doctor_checks_files_outside_initial_loading_roles(self):
        root = self.fixture()
        path = root / "personas/kongzi/processed/index/index-manifest.json"
        path.write_text("broken json", encoding="utf-8")
        with self.assertRaises(ValueError):
            doctor_persona("kongzi", root, True)
