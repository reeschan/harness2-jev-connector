# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0
"""A task connector that replays Atelier's judged PBIs from `cases.jsonl`.

Atelier's `export_cases.py` writes one `atelier.case/1` line per judged PBI. Each case keeps the
elements Atelier injected into the worker apart by kind, and this connector maps them onto
harness2's components:

- `harness.system_prompt` (worker.md) becomes `systemprompt.md` of the starting harness, and
  the CLAUDE.md files of `harness.guardrails` (read from the repository) become `guardrails.md`,
  through `base_harness()`;
- `task` (the PBI's sections) and `harness.memory` (the refs' gists) become the task's prompt;
- the verify commands, the acceptance criteria and `private.rework` become private criteria,
  which only the judge reads.

A replay never changes the source repository: `deliver` writes the chosen run's summary and
diff under `out`, and the judge runs the verify commands in the chosen run's worktree. Only the
cases whose `base_commit` is the repository's HEAD are taken up, so run the replay in a worktree
checked out at that commit.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from harness2.core import intake_report, worktree
from harness2.core.intake import RawTask, WorkspacePolicy
from harness2.core.worktree import git_env

from . import policy

CASE_SCHEMA = "atelier.case/1"
SENT_BACK = "sent-back"
ALL = "all"
INCLUDES = (SENT_BACK, ALL)
# The always-on cap of harness2's OpenCode surface (tree.SurfaceSpec.always_on_max).
ALWAYS_ON_MAX = 24_000
VERIFY_TIMEOUT = 900
_ID = re.compile(r"[^A-Za-z0-9._-]+")
_VERIFY = re.compile(r"^verify (\d+): `(.*)` exits 0$", re.S)
_TAIL = 2_000

DELIVERABLE = {
    "what": "the code change that implements the PBI, collected from the repository worktree",
    "where": worktree.DIFF_NAME,
}
PROMPTS = {
    "work": "implement the PBI in the repository",
    "domain_word": "PBI",
    "deliverable_word": "change",
    "yardstick": "the PBI's acceptance criteria and verification commands",
}

Locate = Callable[[Mapping], Mapping[str, "Path | None"]]


def _warn(message: str) -> None:
    print(f"harness2-jev-connector: {message}", file=sys.stderr)


def task_id_of(case_id: str) -> str:
    """A harness2 task id (`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`) for an Atelier case id."""
    text = _ID.sub("-", case_id).strip("-.") or "case"
    if len(text) > 128:
        digest = hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:10]
        text = f"{text[:117]}-{digest}"
    return text


def load_cases(paths: Sequence[str | Path]) -> list[dict]:
    """The `atelier.case/1` lines of the files, in order; other lines are skipped with a note."""
    out: list[dict] = []
    for path in paths:
        for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except ValueError:
                _warn(f"{path}:{n}: not JSON, skipped")
                continue
            if not isinstance(case, dict) or case.get("schema") != CASE_SCHEMA:
                _warn(f"{path}:{n}: not an {CASE_SCHEMA} case, skipped")
                continue
            out.append(case)
    return out


def head_commit(repo: Path) -> str | None:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, env=git_env()
    )
    return r.stdout.strip() if r.returncode == 0 else None


def _worker(case: Mapping) -> Mapping:
    return (case.get("harness") or {}).get("system_prompt") or {}


def select(
    cases: Sequence[Mapping],
    *,
    head: str | None,
    include: str = SENT_BACK,
    worker: str | None = None,
    dependent: bool = False,
) -> list[Mapping]:
    """The cases to replay on `head`: their base commit, one worker version, sent back by default.

    Without `worker`, the version of the newest case (the last line) that passes the other
    filters is used. A case with `task.depends_on` needs its dependencies' changes, which a
    checkout of the base commit lacks, so it is left out unless `dependent` is true.
    """
    if include not in INCLUDES:
        raise ValueError(f"include must be one of {', '.join(INCLUDES)}, got {include!r}")
    picked = []
    for case in cases:
        if head is None or case.get("base_commit") != head:
            continue
        if include == SENT_BACK and not (case.get("result") or {}).get("sent_back"):
            continue
        if not dependent and (case.get("task") or {}).get("depends_on"):
            continue
        if not _worker(case).get("text"):
            continue
        picked.append(case)
    if worker is None and picked:
        worker = _worker(picked[-1]).get("version")
    return [c for c in picked if _worker(c).get("version") == worker]


def render_prompt(case: Mapping) -> str:
    """The PBI as the worker read it, rebuilt from the case's sections, with the refs' gists."""
    task = case.get("task") or {}
    pbi = str(case.get("pbi") or "")
    lines = [
        f"Build this PBI in the repository (your working directory). Your report name is {pbi}.",
        "",
        f"# {task.get('title') or pbi}",
        "",
        f"- Depends on: {', '.join(task.get('depends_on') or []) or 'none'}",
    ]

    def block(heading: str, body: str) -> None:
        lines.extend(["", f"## {heading}", body.strip() or "none"])

    block("Goal", str(task.get("goal") or ""))
    block("Targets", str(task.get("targets") or ""))
    block("Do not touch", str(task.get("do_not_touch") or ""))
    block(
        "Acceptance criteria",
        "\n".join(f"{a['n']}. {a['text']}" for a in task.get("acceptance") or []),
    )
    block(
        "Verify commands",
        "\n".join(f"{v['n']}. `{v['command']}`" for v in task.get("verify") or []),
    )
    memory = (case.get("harness") or {}).get("memory") or []
    block(
        "Refs",
        "\n".join(f"- {m['id']}: {m.get('gist') or ''}".rstrip() for m in memory)
        + ("\n(The facts are given here; shared memory is not available.)" if memory else ""),
    )
    block("Notes", str(task.get("notes") or ""))
    return "\n".join(lines) + "\n"


def private_criteria(case: Mapping) -> list[str]:
    task = case.get("task") or {}
    out = [f"verify {v['n']}: `{v['command']}` exits 0" for v in task.get("verify") or []]
    out += [f"acceptance {a['n']}: {a['text']}" for a in task.get("acceptance") or []]
    for note in (case.get("private") or {}).get("rework") or []:
        out += [f"rework {item['kind']}: {item['text']}" for item in note.get("items") or []]
    return out


def task_spec(case: Mapping) -> dict:
    spec = {
        "task_id": task_id_of(str(case.get("id") or case.get("pbi") or "")),
        "domain": task_id_of(f"atelier-{case.get('repo') or 'repo'}"),
        "prompt": render_prompt(case),
        "files": [],
        "deliverable": dict(DELIVERABLE),
        "prompts": dict(PROMPTS),
    }
    criteria = private_criteria(case)
    if criteria:
        spec["private_criteria"] = criteria
    return spec


def guardrails_text(repo: Path, cases: Sequence[Mapping], room: int) -> str:
    """The CLAUDE.md files the cases name, read from the repository, within `room` characters.

    A file whose version differs from the one the case recorded is still used, with a note;
    a file that no longer exists is left out. Files that would go over `room` are left out.
    """
    seen: dict[str, str | None] = {}
    for case in cases:
        for g in (case.get("harness") or {}).get("guardrails") or []:
            seen.setdefault(str(g.get("path")), g.get("version"))
    parts: list[str] = []
    used = 0
    for rel, version in seen.items():
        path = repo / rel
        try:
            data = path.read_bytes()
        except OSError:
            _warn(f"guardrail {rel} is not in the repository, left out")
            continue
        now = hashlib.sha256(data).hexdigest()[:12]
        note = "" if version in (None, now) else f" (changed since the cases: {version} -> {now})"
        part = f"## {rel}{note}\n\n{data.decode('utf-8', errors='replace').strip()}\n"
        if used + len(part) + 1 > room:
            _warn(f"guardrail {rel} does not fit the always-on cap, left out")
            continue
        parts.append(part)
        used += len(part) + 1
    return "\n".join(parts)


class AtelierCasesConnector:
    """Replays the selected cases; nothing goes back to the source repository."""

    def __init__(
        self,
        repo: str | Path,
        cases: Sequence[Mapping],
        out: str | Path,
        *,
        mode: str = "agg",
        locate: Locate | None = None,
        pbi_dir: str = policy.PBI_DIR,
        verify_timeout: float = VERIFY_TIMEOUT,
    ) -> None:
        self.repo = Path(repo)
        self.cases = {task_id_of(str(c.get("id") or c.get("pbi") or "")): c for c in cases}
        self.out = Path(out)
        self.mode = mode
        self.locate = locate
        self.pbi_dir = pbi_dir
        self.verify_timeout = verify_timeout
        self.workspace_policy = WorkspacePolicy(exclude=(pbi_dir,))

    def base_harness(self) -> dict[str, str] | None:
        if not self.cases:
            return None
        first = next(iter(self.cases.values()))
        system = str(_worker(first).get("text") or "")
        room = ALWAYS_ON_MAX - len(system) - 2
        guard = guardrails_text(self.repo, list(self.cases.values()), room)
        return {"systemprompt.md": system, "guardrails.md": guard}

    def poll(self) -> list[RawTask]:
        out = []
        for task_id, case in self.cases.items():
            spec = task_spec(case)
            title = str((case.get("task") or {}).get("title") or case.get("pbi") or task_id)
            out.append(
                RawTask(
                    id=task_id,
                    title=title,
                    body=spec["prompt"],
                    meta={"task_spec": spec, "case_id": case.get("id")},
                )
            )
        return out

    def fetch_files(self, raw: RawTask, dest: Path) -> None:
        Path(dest).mkdir(parents=True, exist_ok=True)

    def _record(self, task_id: str, data: Mapping) -> Path:
        self.out.mkdir(parents=True, exist_ok=True)
        path = self.out / f"{task_id}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def deliver(self, task_id: str, deliverable_dir: Path, summary: Mapping) -> None:
        case = self.cases.get(task_id) or {}
        diff = Path(deliverable_dir) / worktree.DIFF_NAME
        kept = None
        if diff.is_file():
            self.out.mkdir(parents=True, exist_ok=True)
            kept = self.out / f"{task_id}.diff"
            shutil.copyfile(diff, kept)
        locations = self.locate(summary) if self.locate is not None else {}
        self._record(
            task_id,
            {
                "case_id": case.get("id"),
                "pbi": case.get("pbi"),
                "worker": _worker(case).get("version"),
                "status": intake_report.report_status(summary, mode=self.mode),
                "diff": str(kept) if kept else None,
                "harness_dir": str(locations.get("harness_dir") or "") or None,
                "summary_json": str(locations.get("summary_json") or "") or None,
            },
        )

    def fail(self, task_id: str, reason: str) -> None:
        case = self.cases.get(task_id) or {}
        self._record(
            task_id,
            {
                "case_id": case.get("id"),
                "pbi": case.get("pbi"),
                "status": intake_report.BLOCKED,
                "reason": reason or "failed",
            },
        )

    def judge(self) -> VerifyJudge:
        return VerifyJudge(self.verify_timeout)

    def report(self, task_id: str) -> str | None:
        try:
            data = json.loads((self.out / f"{task_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return f"{task_id}: {data.get('status')} ({data.get('case_id')})"


class VerifyJudge:
    """Runs the `verify <n>` criteria in the chosen run's worktree and counts those that exit 0.

    The other private criteria (acceptance, rework expectations) are for a person or a model
    judge reading the run; they are not counted here.
    """

    def __init__(self, timeout: float = VERIFY_TIMEOUT) -> None:
        self.timeout = timeout

    def __call__(self, run_dir: Path, task_id: str, criteria: Sequence[str]) -> dict | None:
        workdir = Path(run_dir) / worktree.WORKTREE_DIRNAME
        commands = [(int(m.group(1)), m.group(2)) for c in criteria if (m := _VERIFY.match(c))]
        if not commands or not workdir.is_dir():
            return None
        results = []
        for n, command in commands:
            try:
                r = subprocess.run(
                    ["bash", "-c", command],
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
                code, tail = r.returncode, (r.stdout + r.stderr)[-_TAIL:]
            except subprocess.TimeoutExpired:
                code, tail = None, f"timed out after {self.timeout} seconds"
            results.append({"n": n, "command": command, "code": code, "tail": tail})
        passed = sum(1 for r in results if r["code"] == 0)
        return {
            "n_passed": passed,
            "n_criteria": len(results),
            "all_pass": passed == len(results),
            "results": results,
        }


def _flag(value: bool | str | None) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def cases_connector_from_options(
    *,
    repo: str | Path,
    cases: str | Sequence[str],
    out: str | None = None,
    include: str = SENT_BACK,
    worker: str | None = None,
    dependent: bool | str = False,
    mode: str = "agg",
    locate: Locate | None = None,
    pbi_dir: str = policy.PBI_DIR,
) -> AtelierCasesConnector:
    """The `atelier-cases` connector. `--connector-opt` values arrive as strings.

    `cases` is a `cases.jsonl` path, or several separated by commas. `out` defaults to a
    `replay` directory next to the first file.
    """
    paths = [p.strip() for p in cases.split(",")] if isinstance(cases, str) else list(cases)
    paths = [p for p in paths if p]
    if not paths:
        raise ValueError("cases: give at least one cases.jsonl path")
    head = head_commit(Path(repo))
    chosen = select(
        load_cases(paths),
        head=head,
        include=include,
        worker=worker or None,
        dependent=_flag(dependent),
    )
    if not chosen:
        _warn(
            f"no case to replay at HEAD {head} (base_commit, include={include}, worker, depends_on)"
        )
    return AtelierCasesConnector(
        repo,
        chosen,
        out or Path(paths[0]).parent / "replay",
        mode=mode,
        locate=locate,
        pbi_dir=pbi_dir,
    )


__all__ = [
    "AtelierCasesConnector",
    "VerifyJudge",
    "cases_connector_from_options",
    "load_cases",
    "render_prompt",
    "select",
    "task_spec",
]
