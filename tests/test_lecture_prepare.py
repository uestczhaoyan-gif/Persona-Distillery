"""Lecture brief separation and validation using original synthetic topic/facts."""
import importlib.util
import json
from pathlib import Path
import unittest
import test_roundtable as fixtures

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepare_lecture", ROOT / "定题演讲/scripts/prepare_lecture.py")
lecture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lecture)


class LecturePreparationTests(unittest.TestCase):
    def setUp(self):
        fixtures.RoundtableStateTests.setUp(self)
        self.config = {"person_id": "kongzi", "topic": "Synthetic learning topic", "audience": "Synthetic learners",
                       "goal": "Compare original examples", "minutes": 10, "as_of": "2026-10-12", "facts": []}

    def test_prepare_has_no_invented_facts_and_does_not_upgrade_persona(self):
        manifest = self.root / "personas/kongzi/manifest.json"
        before = manifest.read_bytes()
        value = lecture.prepare(self.config, root=self.root, preview=True)
        self.assertEqual(value["facts"], [])
        self.assertFalse(value["semantic_quality_verified"])
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(value["status"], "prepared")
        path = self.root / "定题演讲/sessions" / value["session_id"]
        self.assertIn("没有提供当代事实", (path / "01-research.md").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((path / "input.json").read_text(encoding="utf-8")), value)

    def test_supplied_fact_remains_separate_and_explicitly_unverified(self):
        self.config["facts"] = [{"fact_id": "F-0001", "statement": "Original synthetic test statement",
                                  "source_title": "Synthetic source", "source_url": "https://example.invalid/source",
                                  "checked_on": "2026-10-11", "limitations": "Synthetic fixture only"}]
        value = lecture.prepare(self.config, root=self.root, preview=True)
        self.assertEqual(value["fact_verification"], "supplied_not_independently_verified")
        self.assertEqual(value["facts"][0]["fact_id"], "F-0001")
        self.config["facts"].append(self.config["facts"][0].copy())
        with self.assertRaises(ValueError):
            lecture.prepare(self.config, root=self.root, preview=True)

    def test_preview_and_duration_validated_before_writing(self):
        with self.assertRaises(ValueError):
            lecture.prepare(self.config, root=self.root)
        for value in (True, 0, 61, "10"):
            self.config["minutes"] = value
            with self.assertRaises(ValueError):
                lecture.prepare(self.config, root=self.root, preview=True)
        self.assertFalse((self.root / "定题演讲/sessions").exists())

    def test_nonhistorical_persona_is_refused_even_with_preview(self):
        path = self.root / "personas/kongzi/manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["subject_kind"] = "private_person"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            lecture.prepare(self.config, root=self.root, preview=True)


if __name__ == "__main__":
    unittest.main()
