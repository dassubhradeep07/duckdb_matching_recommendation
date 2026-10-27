"""Integration checks: real Soda execution against isolated CSV fixtures."""

import csv
from pathlib import Path
import tempfile
import unittest

import yaml

from data_quality.run import DEFAULT_CONFIG, scan, sha256, write_reports


class SodaQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-customer-dq-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = yaml.safe_load(DEFAULT_CONFIG.read_text())
        self.config["project_root"] = str(self.root)
        self.headers = {}
        self.rows = {}
        for name, entry in self.config["datasets"].items():
            contract = DEFAULT_CONFIG.parent / entry["contract"]
            entry["contract"] = str(contract)
            self.headers[name] = [c["name"] for c in yaml.safe_load(contract.read_text())["columns"]]
        for name, prefix in [("source_a", "a"), ("source_b", "b")]:
            self.rows[name] = [self.customer(prefix + str(i)) for i in (1, 2)]
        matched = {"match_score": "100"}
        for prefix, source in [("source_", "source_a"), ("reference_", "source_b")]:
            matched.update({prefix + key: value for key, value in self.rows[source][0].items()})
        self.rows["matched_customers"] = [matched]
        self.rows["unmatched_customers"] = [
            {"source": source, "best_candidate_score": "0", **self.rows[source][1]}
            for source in ("source_a", "source_b")
        ]
        self.rows["auto_merge_candidates"] = [{
            **dict.fromkeys(self.headers["auto_merge_candidates"], ""),
            "candidate_key": "pair-1", "source_customer_id": "a1",
            "reference_customer_id": "b1", "match_score": "100",
            "match_status": "selected_match", "auto_merge_candidate": "yes",
        }]
        self.path = self.root / "datasets.yaml"
        self.path.write_text(yaml.safe_dump(self.config))
        self.save()

    def customer(self, key):
        return dict(zip(self.headers["source_a"],
                        [key, "Test Customer", "customer@example.test", "1234567890",
                         "1 Test Street", "Test City", "CA", "00123", "USA"]))

    def save(self):
        for name, entry in self.config["datasets"].items():
            path = self.root / entry["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=self.headers[name])
                writer.writeheader()
                writer.writerows(self.rows[name])

    def test_valid_data_and_reports_do_not_change_inputs(self):
        files = [self.root / e["path"] for e in self.config["datasets"].values()]
        hashes = {p: sha256(p) for p in files}
        report = scan(self.path, "all")
        self.assertEqual(report["status"], "PASSED", report["errors"])
        self.assertGreaterEqual(report["summary"]["PASSED"], 60)
        write_reports(report, self.root / "reports")
        self.assertTrue((self.root / "reports/dq_report.html").is_file())
        self.assertTrue((self.root / "reports/dq_checks.csv").is_file())
        self.assertEqual(hashes, {p: sha256(p) for p in files})

    def test_dirty_raw_data_fails_and_warns(self):
        self.rows["source_a"][1].update(customer_id="a1", customer_name="  ", email="bad email")
        self.save()
        report = scan(self.path, "raw")
        self.assertEqual(report["status"], "FAILED", report["errors"])
        self.assertGreater(report["summary"].get("WARN", 0), 0)
        failures = [c for c in report["checks"] if c["outcome"] == "FAILED"]
        self.assertTrue(any("customer_name" in c["check"] for c in failures))
        self.assertTrue(any(c["type"] == "duplicate" for c in failures))

    def test_dirty_master_breaks_scores_references_and_reconciliation(self):
        self.rows["matched_customers"][0]["match_score"] = "not-a-number"
        self.rows["unmatched_customers"][0]["customer_id"] = "unknown-id"
        self.rows["auto_merge_candidates"][0]["review_decision"] = "rejected"
        self.save()
        report = scan(self.path, "master")
        self.assertEqual(report["status"], "FAILED", report["errors"])
        failed = {c["check"] for c in report["checks"] if c["outcome"] == "FAILED"}
        self.assertIn("Match score is numeric and within 75 to 100", failed)
        self.assertIn("No reviewer-rejected auto-merge candidates", failed)
        self.assertIn("Unmatched IDs exist in their respective raw source", failed)
        self.assertIn("Every source_a ID appears exactly once across matched and unmatched", failed)

    def test_missing_master_is_error_but_raw_scan_still_works(self):
        (self.root / self.config["datasets"]["matched_customers"]["path"]).unlink()
        self.assertEqual(scan(self.path, "raw")["status"], "PASSED")
        report = scan(self.path, "master")
        self.assertEqual(report["status"], "ERROR")
        self.assertTrue(any("matched_customers" in error for error in report["errors"]))

    def test_invalid_contract_is_error(self):
        broken = self.root / "broken.yaml"
        broken.write_text("dataset: customer_dq/main/source_a\nchecks: [broken\n")
        self.config["datasets"]["source_a"]["contract"] = str(broken)
        self.path.write_text(yaml.safe_dump(self.config))
        self.assertEqual(scan(self.path, "raw")["status"], "ERROR")

    def test_empty_master_is_valid_when_all_raw_records_are_unmatched(self):
        self.rows["matched_customers"] = []
        self.rows["auto_merge_candidates"] = []
        self.rows["unmatched_customers"] = [
            {"source": source, "best_candidate_score": "0", **row}
            for source in ("source_a", "source_b") for row in self.rows[source]
        ]
        self.save()
        report = scan(self.path, "master")
        self.assertEqual(report["status"], "PASSED", report["errors"])


if __name__ == "__main__":
    unittest.main()
