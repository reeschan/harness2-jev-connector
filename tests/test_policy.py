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

"""policy on real git repositories under a temporary directory."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness2.core import worktree as wt
from harness2.core.worktree import WorktreeError

from harness2_jev_connector.policy import (
    PBI_DIR,
    pbi_name,
    unchanged_guard,
    workspace_policy,
)

# The real checker, copied into each test source repository.
REAL_UNCHANGED = Path(__file__).resolve().parent / "fixtures" / "unchanged.py"
# The repository location variables `harness2.core.worktree.git_env()` drops.
LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_record(pbi_dir, name, hashes):
    """Write `<pbi_dir>/verify/<name>.unchanged.json` as unchanged.py's save does."""
    rec = pbi_dir / "verify" / f"{name}.unchanged.json"
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps(hashes), encoding="utf-8")


def git(cwd, *args):
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


class PolicyTestBase(unittest.TestCase):
    def setUp(self):
        saved = {name: os.environ.pop(name) for name in LOCATION_VARS if name in os.environ}
        self.addCleanup(os.environ.update, saved)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.src = self.tmp / "src"
        self.src.mkdir()
        git(self.src, "init", "-q", "-b", "main")
        (self.src / "keep.txt").write_text("keep\n")
        (self.src / "edit.txt").write_text("base\n")
        (self.src / ".gitignore").write_text("ignored.log\n")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "init")
        self.pbi = self.src / PBI_DIR
        tools = self.pbi / "tools"
        tools.mkdir(parents=True)
        shutil.copyfile(REAL_UNCHANGED, tools / "unchanged.py")
        # GOOD guards keep.txt (left alone by the agent); BAD guards edit.txt (the agent edits it).
        write_record(self.pbi, "GOOD", {"keep.txt": sha((self.src / "keep.txt").read_bytes())})
        write_record(self.pbi, "BAD", {"edit.txt": sha((self.src / "edit.txt").read_bytes())})
        (self.src / "ignored.log").write_text("noise\n")
        self.run_dir = self.tmp / "run"
        self.wt = self.run_dir / wt.WORKTREE_DIRNAME
        self.diff = self.run_dir / "workspace" / "output" / wt.DIFF_NAME

    def snapshot(self):
        files = {}
        for p in sorted(self.src.rglob("*")):
            if ".git" in p.relative_to(self.src).parts or not p.is_file():
                continue
            files[p.relative_to(self.src).as_posix()] = p.read_bytes()
        return files


class PbiNameTest(unittest.TestCase):
    def test_name_before_first_dot(self):
        cases = (
            ("PBI-021", "PBI-021"),
            ("PBI-021.rework-1", "PBI-021"),
            ("PBI-021.harness2", "PBI-021"),
        )
        for task_id, name in cases:
            with self.subTest(task_id=task_id):
                self.assertEqual(pbi_name(task_id), name)


class WorkspacePolicyTest(unittest.TestCase):
    def test_excludes_the_pbi_dir_and_adds_no_guard(self):
        policy = workspace_policy()
        self.assertEqual(PBI_DIR, "docs/pbi")
        self.assertEqual(policy.exclude, ("docs/pbi",))
        self.assertIsNone(policy.guard)

    def test_pbi_dir_is_an_argument(self):
        self.assertEqual(workspace_policy(pbi_dir="other/pbi").exclude, ("other/pbi",))


class UnchangedGuardTest(PolicyTestBase):
    def setUp(self):
        super().setUp()
        self.base = wt.prepare_worktree(self.src, self.wt)

    def test_unchanged_returns_none(self):
        self.assertIsNone(unchanged_guard("GOOD")(self.wt, self.src))

    def test_changed_returns_reason_with_path(self):
        (self.wt / "keep.txt").write_text("tampered\n")
        reason = unchanged_guard("GOOD")(self.wt, self.src)
        self.assertIsNotNone(reason)
        self.assertIn("changed keep.txt", reason)

    def test_uses_source_script_and_record(self):
        # A check inside the worktree would pass after this: the guard must still refuse.
        (self.wt / "keep.txt").write_text("tampered\n")
        tampered = sha((self.wt / "keep.txt").read_bytes())
        write_record(self.wt / PBI_DIR, "GOOD", {"keep.txt": tampered})
        (self.wt / PBI_DIR / "tools" / "unchanged.py").write_text("raise SystemExit(0)\n")
        reason = unchanged_guard("GOOD")(self.wt, self.src)
        self.assertIsNotNone(reason)
        self.assertIn("changed keep.txt", reason)

    def test_missing_source_script_returns_reason(self):
        (self.src / PBI_DIR / "tools" / "unchanged.py").unlink()
        reason = unchanged_guard("GOOD")(self.wt, self.src)
        self.assertIsNotNone(reason)
        self.assertIn("unchanged.py", reason)


class ApplyBackTest(PolicyTestBase):
    def setUp(self):
        super().setUp()
        self.base = wt.prepare_worktree(self.src, self.wt)
        (self.wt / "edit.txt").write_text("by agent\n")
        (self.wt / PBI_DIR / "scratch.md").write_text("scratch\n")
        self.names = wt.collect_diff(
            self.wt, self.base, self.diff, exclude=workspace_policy().exclude
        )

    def test_applies_when_the_guard_passes(self):
        self.assertEqual(self.names, ["edit.txt"])
        paths = wt.apply_back(
            self.src,
            self.run_dir,
            guard=unchanged_guard("GOOD"),
            exclude=workspace_policy().exclude,
        )
        self.assertEqual(paths, ["edit.txt"])
        self.assertEqual((self.src / "edit.txt").read_text(), "by agent\n")
        self.assertFalse((self.src / PBI_DIR / "scratch.md").exists())

    def test_guard_refusal_keeps_source(self):
        (self.wt / "keep.txt").write_text("tampered\n")
        before = self.snapshot()
        with self.assertRaises(WorktreeError) as cm:
            wt.apply_back(
                self.src,
                self.run_dir,
                guard=unchanged_guard("GOOD"),
                exclude=workspace_policy().exclude,
            )
        self.assertIn("changed keep.txt", str(cm.exception))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
