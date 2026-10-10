import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "直接对话/scripts"))
import pilot_inputs as pilot


class PilotInputsTests(unittest.TestCase):
    def test_four_groups_are_reproducible_and_have_no_results(self):
        first = pilot.prepare(preview=True)
        self.assertEqual(first, pilot.prepare(preview=True))
        self.assertEqual(len(first["inputs"]), 16)
        self.assertEqual(first["real_model_calls"], 0)
        self.assertIsNone(first["model_identity"])
        for item in first["inputs"]:
            text = json.dumps(item["messages"], ensure_ascii=False, sort_keys=True)
            self.assertEqual(item["sha256"], pilot.digest(text))
            self.assertNotIn('<package-file path="personas/' + item["person_id"] + '/04-evaluation.md">', text)
            self.assertEqual(item["messages"][-1]["content"], pilot.BOUNDARY)

    def test_preview_authorization_is_not_bypassed(self):
        with self.assertRaisesRegex(ValueError, "authorization"):
            pilot.prepare()

    def test_bad_historical_hash_is_rejected(self):
        with patch.object(pilot, "historical", return_value="tampered"):
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                pilot.prepare(preview=True)

    def test_filtered_memory_cannot_be_revived_by_historical_baseline(self):
        with patch.object(pilot, "compile_context", return_value={"requires_search_tool": True}):
            with self.assertRaisesRegex(ValueError, "complete eligible memory"):
                pilot.prepare(preview=True)
