import json
import unittest
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "real_workflow_2026-08-31"


class RealWorkflowFixtureTests(unittest.TestCase):
    def test_review_cases_and_snapshot_are_structurally_consistent(self):
        cases_payload = json.loads(
            (FIXTURE_DIR / "cases.json").read_text(encoding="utf-8")
        )
        snapshot_payload = json.loads(
            (FIXTURE_DIR / "snapshots.json").read_text(encoding="utf-8")
        )

        cases = cases_payload["cases"]
        records = snapshot_payload["records"]
        records_by_id = {record["snapshot_record_id"]: record for record in records}

        self.assertEqual(cases_payload["schema_version"], 1)
        self.assertEqual(snapshot_payload["schema_version"], 1)
        self.assertEqual(len(cases), 5)
        self.assertEqual([case["difficulty"] for case in cases], [1, 2, 3, 4, 5])
        self.assertEqual(len({case["fixture_id"] for case in cases}), 5)

        for case in cases:
            with self.subTest(fixture_id=case["fixture_id"]):
                record = records_by_id[case["snapshot_record_id"]]
                self.assertEqual(record["origin"], "seed")
                self.assertEqual(record["status"], "done")
                self.assertEqual(record["question"], case["input"]["question"])
                self.assertEqual(record["user_thoughts"], case["input"]["user_thoughts"])
                self.assertEqual(
                    record["reference_answer"], case["input"]["reference_answer"]
                )
                self.assertTrue(case["scripted_grill_answers"])
                self.assertTrue(case["review_targets"])
                self.assertTrue(record["grilling_summary"])
                self.assertIsInstance(record["grilling_conversation"], list)
                self.assertIsInstance(record["teach_conversation"], list)

        derived = [record for record in records if record["origin"] == "drill-derived"]
        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0]["status"], "pending-grill")
        self.assertEqual(derived[0]["grilling_conversation"], [])
        self.assertEqual(derived[0]["teach_conversation"], [])
        self.assertEqual(
            snapshot_payload["run"]["final_status_counts"],
            {"done": 5, "pending-grill": 1},
        )
