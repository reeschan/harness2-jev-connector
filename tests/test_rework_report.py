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
    seconds=None,
    tokens=None,
    tier="sonnet",
):
    return {
        "tier": tier,
        "time": {"seconds": seconds},
        "steps": None if seconds is None else seconds // 10,
        "tokens": {
            "total": tokens,
            "input": tokens and tokens // 2,
            "cache_read": tokens and tokens // 2,
        },
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

    def test_time_tokens_and_size(self):
        rows = [
            row("PBI-001", "PBI-001", "合格", seconds=100, tokens=1000),
            row("PBI-002", "PBI-002", "使えず", seconds=50, tokens=500, tier="haiku"),
            row(
                "PBI-002", "PBI-002", "差し戻し", attempt=2, seconds=200, tokens=2000, tier="haiku"
            ),
            row(
                "PBI-002",
                "PBI-002.rework-1",
                "合格",
                rework=1,
                seconds=100,
                tokens=1500,
                tier="haiku",
            ),
            row("PBI-003", "PBI-003", "合格"),
            row("PBI-005", "PBI-005", "使えず", tokens=5000, tier="opus"),
            row("PBI-006", "PBI-006", "使えず", tokens=100, tier="haiku"),
            row("PBI-006", "PBI-006", "合格", attempt=2, tokens=100),
        ]
        sizes = {
            ("app", "app:main:W-001", "PBI-001"): {
                "tier": "sonnet",
                "targets": 1,
                "refs": 0,
                "chars": {"worker": 3000, "pbi": 500, "refs": 0},
            },
            ("app", "app:main:W-001", "PBI-002"): {
                "tier": "haiku",
                "targets": 5,
                "refs": 3,
                "chars": {"worker": 3000, "pbi": 4000, "refs": 300},
            },
        }
        report = rework_report.summarize(rows, sizes=sizes)
        o = report["overall"]
        self.assertEqual((o["median_seconds"], o["median_tokens"]), (225, 1000))
        self.assertAlmostEqual(o["rework_token_share"], (2000 + 5000 + 100) / (5000 + 5000 + 200))
        cost = report["cost"]
        self.assertAlmostEqual(cost["token_parts"]["cache_read_share"], 0.5)
        self.assertEqual(cost["pbis_with_size"], 2)
        self.assertEqual([g["tier"] for g in cost["by_tier"]], ["haiku", "sonnet"])
        self.assertEqual([g["pbis"] for g in cost["by_tier"]], [2, 2])
        self.assertEqual([g["targets"] for g in cost["by_targets"]], ["1", "4+"])
        self.assertEqual([g["refs"] for g in cost["by_refs"]], ["0", "3+"])
        self.assertEqual([g["chars"] for g in cost["by_text_chars"]], ["0-3999", "4000-7999"])
        self.assertEqual([h["pbi"] for h in cost["heavy"]], ["PBI-002", "PBI-001", "PBI-006"])
        heavy = cost["heavy"][0]
        self.assertEqual((heavy["tokens"], heavy["seconds"], heavy["attempts"]), (4000, 350, 3))
        self.assertEqual((heavy["targets"], heavy["chars"], heavy["first_pass"]), (5, 7300, False))
        text = rework_report.table(report)
        self.assertIn("差し戻しと使えずで使ったトークンの割合 70%", text)
        self.assertIn("| app PBI-002 | 4,000 | 350 |", text)

    def test_cli_reads_the_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            group = Path(tmp) / "app" / "main__W-001"
            group.mkdir(parents=True)
            (group / "runs.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ROWS), encoding="utf-8"
            )
            (group / "cases.jsonl").write_text(
                json.dumps(
                    {
                        "schema": "atelier.case/1",
                        "repo": "app",
                        "work_key": "app:main:W-001",
                        "pbi": "PBI-001",
                        "size": {"tier": "sonnet", "targets": 2, "refs": 1, "chars": {}},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "schema": "other/1",
                        "repo": "app",
                        "work_key": "app:main:W-001",
                        "pbi": "PBI-002",
                        "size": {"targets": 9},
                    }
                )
                + "\nnot json\n",
                encoding="utf-8",
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
            self.assertIn("Targets の数別 (cases.jsonl のある 1 件)", r.stdout)
            self.assertIn("| 2-3 | 1 |", r.stdout)
            self.assertNotIn("| 4+ |", r.stdout)
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
