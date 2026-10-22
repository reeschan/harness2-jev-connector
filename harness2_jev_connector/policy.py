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

"""The workspace policy of a jev task: keep `docs/pbi` out of the diff, check untouched files.

jev records the hashes of the files a PBI must not touch before the PBI starts, and checks them
with `docs/pbi/tools/unchanged.py check <name>`. The agent can rewrite the script and the record
inside its worktree, so the check runs the source's script against the source's record only.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from harness2.core.intake import WorkspaceGuard, WorkspacePolicy
from harness2.core.worktree import git_env

PBI_DIR = "docs/pbi"
PYTHON = sys.executable
_TAIL = 2000


def pbi_name(task_id: str) -> str:
    """The PBI name of a report name: the part before the first `.`.

    `PBI-021.rework-1` becomes `PBI-021`.
    """
    return task_id.split(".", 1)[0]


def unchanged_guard(name: str, *, pbi_dir: str = PBI_DIR, python: str = PYTHON) -> WorkspaceGuard:
    """Make a guard that runs the source's `unchanged.py check <name>` in the worktree.

    The script and the record are read from the source (`source / pbi_dir`), never from the
    worktree. `name` is a PBI name (see `pbi_name`); the guard is called as
    `guard(worktree, source)` and returns None when none of the recorded files changed, else
    the reason it did not pass.
    """

    def guard(worktree: Path, source: Path) -> str | None:
        script = Path(source) / pbi_dir / "tools" / "unchanged.py"
        if not script.is_file():
            return f"no unchanged.py in the source: {script}"
        # `--dir` is an option of the top-level parser, so it goes before the subcommand.
        cmd = [
            python,
            str(script.resolve()),
            "--dir",
            str((Path(source) / pbi_dir).resolve()),
            "check",
            name,
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, cwd=worktree, env=git_env()
        )
        if proc.returncode == 0:
            return None
        changed = [line for line in proc.stdout.splitlines() if line.startswith("changed ")]
        detail = "; ".join(changed) or proc.stdout[-_TAIL:]
        return (
            f"{' '.join(cmd)} failed (exit {proc.returncode}): {detail} {proc.stderr[-_TAIL:]}"
        ).rstrip()

    return guard


def workspace_policy(*, pbi_dir: str = PBI_DIR) -> WorkspacePolicy:
    """The policy that keeps `pbi_dir` out of the diff; the guard is added for each task."""
    return WorkspacePolicy(exclude=(pbi_dir,))
