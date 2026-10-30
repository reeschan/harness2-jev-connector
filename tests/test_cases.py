# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0
"""The atelier-cases connector: Atelier's cases.jsonl mapped onto harness2's components."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness2.core import worktree as wt
from harness2.core.intake import capabilities, resolve_factory, validate_task_spec
from harness2.testing.connector_contract import ConnectorContractTests, check_judge_result

from harness2_jev_connector import cases as atelier_cases
from harness2_jev_connector.cases import (
    AtelierCasesConnector,
    VerifyJudge,
    cases_connector_from_options,
    select,
    task_spec,
)

WORKER = "# How to work on a PBI and report\n\nWork only inside the work root.\n"
RULES = "Use tabs.\n"


def version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def case(pbi="PBI-002", base="c0ffee", sent_back=True, depends=(), worker=WORKER, rules=RULES):
    return {
        "schema": "atelier.case/1",
        "id": f"app:app:main:W-003:{pbi}",
        "repo": "app",
        "work_key": "app:main:W-003",
        "pbi": pbi,
        "base_commit": base,
        "harness": {
            "system_prompt": {
                "path": "docs/pbi/worker.md",
                "version": version(worker.encode()),
                "text": worker,
            },
            "guardrails": [
                {"kind": "convention", "path": "CLAUDE.md", "version": version(rules.encode())}
            ],
            "memory": [{"id": "mem-1", "gist": "dates are UTC", "fetched": False}],
        },
        "task": {
            "title": f"{pbi}: sort the list",
            "goal": "Sort the list by date.",
            "targets": "- src/sort.ts",
            "do_not_touch": "- src/api/",
            "acceptance": [{"n": 1, "text": "verify 1 exits 0"}],
            "verify": [{"n": 1, "command": "test -f done.txt"}],
            "notes": "none",
            "depends_on": list(depends),
        },
        "result": {
            "first_pass": not sent_back,
            "passed": True,
            "sent_back": sent_back,
            "attempts": [],
        },
        "private": {
            "rework": [
                {
                    "note": f"{pbi}.rework-1.md",
                    "items": [{"kind": "expected", "text": "stable order"}],
                }
            ]
        },
    }


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class CasesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        (self.repo / "CLAUDE.md").write_text(RULES, encoding="utf-8")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.head = git(self.repo, "rev-parse", "HEAD")

    def write_cases(self, *items):
        path = self.tmp / "cases.jsonl"
        path.write_text(
            "".join(json.dumps(c) + "\n" for c in items) + "not json\n", encoding="utf-8"
        )
        return path

    def test_spec_is_valid_and_private_criteria_stay_out_of_the_prompt(self):
        spec = task_spec(case())
        self.assertEqual(validate_task_spec(spec), [])
        self.assertEqual(spec["task_id"], "app-app-main-W-003-PBI-002")
        self.assertEqual(spec["domain"], "atelier-app")
        self.assertIn("## Verify commands\n1. `test -f done.txt`", spec["prompt"])
        self.assertIn("- mem-1: dates are UTC", spec["prompt"])
        self.assertIn("report name is PBI-002", spec["prompt"])
        self.assertNotIn("stable order", spec["prompt"])
        self.assertEqual(
            spec["private_criteria"],
            [
                "verify 1: `test -f done.txt` exits 0",
                "acceptance 1: verify 1 exits 0",
                "rework expected: stable order",
            ],
        )

    def test_select_by_head_sent_back_dependencies_and_worker(self):
        older = case("PBI-001", worker="old\n")
        cases = [
            older,
            case("PBI-002"),
            case("PBI-003", sent_back=False),
            case("PBI-004", depends=["PBI-002"]),
            case("PBI-005", base="other"),
        ]
        self.assertEqual([c["pbi"] for c in select(cases, head="c0ffee")], ["PBI-002"])
        self.assertEqual(
            [c["pbi"] for c in select(cases, head="c0ffee", include="all")], ["PBI-002", "PBI-003"]
        )
        self.assertEqual(
            [c["pbi"] for c in select(cases, head="c0ffee", dependent=True)], ["PBI-002", "PBI-004"]
        )
        old = older["harness"]["system_prompt"]["version"]
        self.assertEqual([c["pbi"] for c in select(cases, head="c0ffee", worker=old)], ["PBI-001"])
        self.assertEqual(select(cases, head=None), [])
        with self.assertRaises(ValueError):
            select(cases, head="c0ffee", include="some")

    def test_factory_reads_the_file_and_maps_the_harness(self):
        path = self.write_cases(case(base=self.head), case("PBI-009"))
        factory = resolve_factory("atelier-cases")
        conn = factory(repo=str(self.repo), cases=str(path))
        self.assertIsInstance(conn, AtelierCasesConnector)
        caps = capabilities(conn)
        self.assertEqual(caps.base_harness["systemprompt.md"], WORKER)
        self.assertIn("## CLAUDE.md\n\nUse tabs.", caps.base_harness["guardrails.md"])
        self.assertNotIn("changed since", caps.base_harness["guardrails.md"])
        self.assertEqual(caps.workspace_policy.exclude, ("docs/pbi",))
        [raw] = conn.poll()
        self.assertEqual(raw.meta["task_spec"]["task_id"], raw.id)
        self.assertEqual(conn.out, self.tmp / "replay")

    def test_changed_guardrail_is_noted_and_a_missing_one_left_out(self):
        (self.repo / "CLAUDE.md").write_text("Use spaces.\n", encoding="utf-8")
        c = case(base=self.head)
        c["harness"]["guardrails"].append(
            {"kind": "convention", "path": "src/CLAUDE.md", "version": "x"}
        )
        conn = AtelierCasesConnector(self.repo, [c], self.tmp / "out")
        text = conn.base_harness()["guardrails.md"]
        self.assertIn("changed since the cases", text)
        self.assertIn("Use spaces.", text)
        self.assertNotIn("src/CLAUDE.md", text)

    def test_guardrails_over_the_cap_are_left_out(self):
        (self.repo / "CLAUDE.md").write_text("x" * 30_000, encoding="utf-8")
        conn = AtelierCasesConnector(self.repo, [case(base=self.head)], self.tmp / "out")
        self.assertEqual(conn.base_harness()["guardrails.md"], "")

    def test_no_case_gives_no_base_harness(self):
        conn = cases_connector_from_options(
            repo=str(self.repo), cases=str(self.write_cases(case()))
        )
        self.assertEqual(conn.poll(), [])
        self.assertIsNone(conn.base_harness())

    def test_deliver_and_fail_write_under_out_and_never_touch_the_source(self):
        conn = AtelierCasesConnector(self.repo, [case(base=self.head)], self.tmp / "out")
        [raw] = conn.poll()
        deliverable = self.tmp / "run" / "workspace" / "output"
        deliverable.mkdir(parents=True)
        (deliverable / wt.DIFF_NAME).write_text("diff --git a/x b/x\n", encoding="utf-8")
        conn.deliver(raw.id, deliverable, {"task_id": raw.id, "selections": {}})
        data = json.loads((self.tmp / "out" / f"{raw.id}.json").read_text(encoding="utf-8"))
        self.assertEqual(data["pbi"], "PBI-002")
        self.assertEqual(Path(data["diff"]).read_text(encoding="utf-8"), "diff --git a/x b/x\n")
        self.assertIn(raw.id, conn.report(raw.id))
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        conn.fail(raw.id, "no change")
        data = json.loads((self.tmp / "out" / f"{raw.id}.json").read_text(encoding="utf-8"))
        self.assertEqual((data["status"], data["reason"]), ("BLOCKED", "no change"))

    def test_judge_runs_only_the_verify_criteria_in_the_worktree(self):
        run_dir = self.tmp / "run"
        (run_dir / wt.WORKTREE_DIRNAME).mkdir(parents=True)
        (run_dir / wt.WORKTREE_DIRNAME / "done.txt").write_text("", encoding="utf-8")
        criteria = atelier_cases.private_criteria(case())
        criteria.append("verify 2: `exit 3` exits 0")
        result = VerifyJudge()(run_dir, "t", criteria)
        check_judge_result(result)
        self.assertEqual(
            (result["n_passed"], result["n_criteria"], result["all_pass"]), (1, 2, False)
        )
        self.assertEqual([r["code"] for r in result["results"]], [0, 3])
        self.assertIsNone(VerifyJudge()(run_dir, "t", ["acceptance 1: x"]))
        self.assertIsNone(VerifyJudge()(self.tmp / "none", "t", criteria))

    def test_long_ids_are_cut_with_a_digest(self):
        task_id = atelier_cases.task_id_of("a" * 200)
        self.assertEqual(len(task_id), 128)
        self.assertNotEqual(task_id, atelier_cases.task_id_of("a" * 199))


class CasesContractTest(ConnectorContractTests, unittest.TestCase):
    def make_connector(self, tmp: Path):
        repo = tmp / "repo"
        repo.mkdir()
        return AtelierCasesConnector(repo, [case(), case("PBI-003")], tmp / "out")


if __name__ == "__main__":
    unittest.main()
