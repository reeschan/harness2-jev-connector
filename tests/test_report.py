# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# Modified 2026 by Ryuki Tobita: moved out of harness2 into harness2-jev-connector.

"""Tests for the details body, reading a report, and its status line."""

import tempfile
import unittest
from pathlib import Path

from harness2.core.schedule import TaskResult

from harness2_jev_connector import report as r


def _summary(run_dir="", *, errors=(), status="ok", mode="agg", why="best", label="c1"):
    res = TaskResult(task_id="PBI-002", domain="intake", k=2, seed=None, status=status)
    res.base = [{"label": "base1", "run_id": "x/base1", "run_dir": "/nowhere"}]
    res.errors = list(errors)
    if run_dir is not None:
        res.modes[mode] = {"ok": True}
        res.selections[mode] = {
            "ok": True,
            "chosen_label": label,
            "why": why,
            "chosen_run_dir": str(run_dir),
            "chosen_tree_is_base": False,
        }
    return res.to_dict()


class RenderDetailsTest(unittest.TestCase):
    def _render(self, **kw):
        summary = kw.pop("summary", None) or _summary("/x", errors=["agg: odd"])
        locs = {"summary_json": Path("/r/s/summary.json"), "harness_dir": Path("/r/h/c1")}
        return r.render_details(summary, mode="agg", locations=locs, **kw)

    def test_has_five_headings_and_no_status_line(self):
        text = self._render()
        for head in ("## 自己判定", "## 変更", "## 検証", "## 未解決", "## ハーネス"):
            self.assertIn(head + "\n", text)
        self.assertFalse(text.startswith("状態:"))
        self.assertIn(str(Path("/r/s/summary.json")), text)
        self.assertIn(str(Path("/r/h/c1")), text)
        self.assertIn("chosen_tree_is_base: False", text)
        self.assertIn("- agg: odd", text)

    def test_why_is_cut_at_4000(self):
        text = self._render(summary=_summary("/x", why="A" * 5000 + "TAIL"))
        self.assertIn("A" * 4000, text)
        self.assertNotIn("A" * 4001, text)
        self.assertNotIn("TAIL", text)

    def test_changed_and_verify_are_listed(self):
        verify = [
            {"n": 1, "command": "python3 -m unittest", "code": 0},
            {"n": 2, "command": "ruff check", "code": 1},
        ]
        text = self._render(verify=verify, changed=["a.py", "b/c.py"])
        self.assertIn("- a.py\n- b/c.py", text)
        self.assertIn("- 1: `python3 -m unittest` -> 0", text)
        self.assertIn("- 2: `ruff check` -> 1", text)
        self.assertIn("pass 1/2", text)

    def test_empty_sections_say_none(self):
        text = self._render(summary=_summary("/x"))
        self.assertIn("## 変更\nなし", text)
        self.assertIn("## 未解決\nなし", text)


class ReadReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, body, *, report_dir=r.REPORT_DIR, task_id="PBI-002"):
        path = self.repo / report_dir / f"{task_id}.last.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def test_reads_body_without_surrounding_newlines(self):
        self._write("\n状態: DONE\n結果: 受け入れ条件 pass 3/4\n\n")
        self.assertEqual(
            r.read_report(self.repo, "PBI-002"),
            "状態: DONE\n結果: 受け入れ条件 pass 3/4",
        )

    def test_missing_report_is_none(self):
        self.assertIsNone(r.read_report(self.repo, "PBI-002"))

    def test_symlinked_report_is_not_read(self):
        target = self.tmp / "outside.md"
        target.write_text("状態: DONE\n", encoding="utf-8")
        path = self.repo / r.REPORT_DIR / "PBI-002.last.md"
        path.parent.mkdir(parents=True)
        path.symlink_to(target)
        try:
            self.assertIsNone(r.read_report(self.repo, "PBI-002"))
        finally:
            path.unlink()

    def test_report_dir_is_relative_to_repo(self):
        self._write("状態: BLOCKED\n", report_dir=Path("custom"))
        self.assertEqual(
            r.read_report(self.repo, "PBI-002", report_dir=Path("custom")),
            "状態: BLOCKED",
        )
        self.assertIsNone(r.read_report(self.repo, "PBI-002"))


class ReportStatusLineTest(unittest.TestCase):
    def test_status_line(self):
        self.assertEqual(r.report_status_line("状態: DONE\n結果: pass 3/4"), "DONE")

    def test_status_value_is_stripped(self):
        self.assertEqual(
            r.report_status_line("状態:   DONE_WITH_CONCERNS  \n"), "DONE_WITH_CONCERNS"
        )

    def test_no_status_line(self):
        self.assertIsNone(r.report_status_line("結果: 受け入れ条件 pass 3/4\n"))

    def test_empty_text(self):
        self.assertIsNone(r.report_status_line(""))


if __name__ == "__main__":
    unittest.main()
