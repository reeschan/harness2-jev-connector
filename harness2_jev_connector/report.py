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

"""The details body of a report and reading the five-line report jev-mcp writes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from harness2.core.intake_report import report_status

WHY_CAP = 4000
REPORT_DIR = Path("docs") / "pbi" / "mcp"
STATUS_PREFIX = "状態: "


def _selection(summary: Mapping, mode: str) -> Mapping:
    sel = (summary.get("selections") or {}).get(mode)
    return sel if isinstance(sel, Mapping) else {}


def _lines(items: Sequence[str]) -> list[str]:
    out = [f"- {' '.join(str(i).splitlines())}" for i in items]
    return out or ["なし"]


def render_details(
    summary: Mapping,
    *,
    mode: str,
    locations: Mapping[str, Path | None],
    verify: Sequence[Mapping] = (),
    changed: Sequence[str] = (),
) -> str:
    """The Markdown body of the details file (jev-mcp prepends the `状態:` line itself)."""
    sel = _selection(summary, mode)
    status = report_status(summary, mode=mode)
    passed = sum(1 for v in verify if v.get("code") == 0)

    def where(key: str) -> str:
        value = locations.get(key)
        return str(value) if value is not None else "なし"

    why = str(sel.get("why") or "")
    if len(why) > WHY_CAP:
        why = why[:WHY_CAP] + "\n…(truncated)"

    parts = [
        "## 自己判定",
        f"- 判定: {status}（mode: {mode}、summary の status: {summary.get('status', '')}）",
        f"- 検証: pass {passed}/{len(verify)}",
        "",
        "## 変更",
        *_lines(changed),
        "",
        "## 検証",
        *(
            [f"- {v.get('n', '')}: `{v.get('command', '')}` -> {v.get('code', '')}" for v in verify]
            or ["なし"]
        ),
        "",
        "## 未解決",
        *_lines(summary.get("errors") or []),
        "",
        "## ハーネス",
        f"- summary.json: {where('summary_json')}",
        f"- 選ばれたラベル: {sel.get('chosen_label') or 'なし'}",
        f"- chosen_tree_is_base: {sel.get('chosen_tree_is_base')}",
        f"- 採用したハーネス: {where('harness_dir')}",
        "",
        "### 選定の理由",
        why or "なし",
        "",
    ]
    return "\n".join(parts)


def read_report(repo: Path, task_id: str, *, report_dir: Path = REPORT_DIR) -> str | None:
    """The body of the report jev-mcp wrote for `task_id`, or None.

    The file is `<repo>/<report_dir>/<task_id>.last.md`; a symlink is not read.
    """
    path = repo / report_dir / f"{task_id}.last.md"
    if path.is_symlink() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip("\n")


def report_status_line(text: str) -> str | None:
    """The value after `STATUS_PREFIX` on the first line of `text`, or None."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith(STATUS_PREFIX):
        return None
    return lines[0][len(STATUS_PREFIX) :].strip()
