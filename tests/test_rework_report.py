# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0
"""atelier-rework-report: rework rate, causes and compliance from Atelier's runs.jsonl."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness2_jev_connector import rework_report


def row(
    pbi,
    report,
    outcome,
    *,
    rework=0,
    attempt=1,
    cause=None,
    worker="v1",
    at="1",
    violation=False,
    missed=None,
    unchanged=None,
):
    return {
        "repo": "app",
        "work_key": "app:main:W-001",
        "pbi": pbi,
        "report": report,
        "attempt": attempt,
        "rework": rework,
        "runner": "agent",
        "model": "sonnet",
        "effort": "high",
        "recorded_at": f"2026-10-30T10:00:0{at}+09:00",
        "memory": {"violation": violation, "missed_refs": missed},
        "unchanged": {"violation": unchanged},
        "prompt": {"worker": worker},
        "archive": {"dir": f"/a/{report}"},
        "judged": {"outcome": outcome, "findings": None, "cause": cause},
    }


ROWS = [
    row("PBI-001", "PBI-001", "合格", missed=[]),
    row("PBI-002", "PBI-002", "使えず", at="2"),
    row(
        "PBI-002",
        "PBI-002",
        "差し戻し",
        attempt=2,
        cause=["spec", "convention"],
        at="3",
        missed=["mem-1"],
    ),
    row("PBI-002", "PBI-002.rework-1", "合格", rework=1, at="4", missed=[]),
    row("PBI-003", "PBI-003", "差し戻し", worker="v2", at="5", unchanged=True),
    row(
        "PBI-003",
        "PBI-003.rework-1",
        "保留",
        rework=1,
        worker="v2",
        cause=["model"],
        at="6",
        violation=True,
    ),
    row("PBI-004", "PBI-004", "使えず", worker="v2", at="7"),
]


class ReworkReportTest(unittest.TestCase):
    def test_rates_causes_and_versions(self):
        report = rework_report.summarize(ROWS)
        self.assertEqual(report["pbis"], 3)
        o = report["overall"]
        self.assertAlmostEqual(o["first_pass_rate"], 1 / 3)
        self.assertAlmostEqual(o["rework_rate"], 2 / 3)
        self.assertAlmostEqual(o["stop_rate"], 1 / 3)
        self.assertEqual(report["causes"]["spec"], 1)
        self.assertEqual(report["causes"]["convention"], 1)
        self.assertEqual(report["causes"]["model"], 1)
        self.assertEqual(report["unattributed"], 1)
        self.assertAlmostEqual(report["prompt_side_share"], 2 / 3)
        c = report["compliance"]
        self.assertEqual((c["memory_violation"]["hits"], c["memory_violation"]["checked"]), (1, 5))
        self.assertEqual((c["missed_refs"]["hits"], c["missed_refs"]["checked"]), (1, 3))
        self.assertEqual((c["do_not_touch"]["hits"], c["do_not_touch"]["checked"]), (1, 1))
        self.assertEqual([v["worker"] for v in report["by_worker_version"]], ["v1", "v2"])
        self.assertEqual([v["pbis"] for v in report["by_worker_version"]], [2, 1])
        self.assertEqual([c["report"] for c in report["candidates"]], ["PBI-002"])

    def test_cli_reads_the_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            group = Path(tmp) / "app" / "main__W-001"
            group.mkdir(parents=True)
            (group / "runs.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ROWS), encoding="utf-8"
            )
            r = subprocess.run(
                [sys.executable, "-m", "harness2_jev_connector.rework_report", "--archive", tmp],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("差し戻し率 67%", r.stdout)
            self.assertIn("| v1 | 2 |", r.stdout)
            missing = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "harness2_jev_connector.rework_report",
                    str(Path(tmp) / "none.jsonl"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(missing.returncode, 2)


if __name__ == "__main__":
    unittest.main()
