# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0

"""A model connector over jev-mcp: candidates from a template, fence declarations, records.

`JevModelConnector.routes` turns the steps of a template (see `model_config`) into harness2's
`ModelChoice` candidates. A runner step becomes one candidate; a Jev step asks jev-mcp's
`route` and turns the steps it returns into candidates. `fences` translates the fence Jev's
setting applies to a runner into harness2's runner-neutral `FenceDeclaration`. `record` writes
one attempt with jev-mcp's `record`. `run_session` runs an improver session with jev-mcp's `run`.

All translation between harness2's words and Jev's words happens in this module; jev-mcp's
tools are called with Jev's own arguments.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from harness2.core.models import (
    FenceDeclaration,
    ModelChoice,
    Outcome,
    Role,
    SessionRequest,
    SessionResult,
)
from harness2.plugins import mcp
from harness2.plugins.mcp import McpClient, McpError, McpToolError

from . import model_config
from .model_config import DEFAULT_SETTING, Step, Template

# The external runners jev-mcp's `route` accepts in `skip` (select_model.RUNNERS of
# pbi-orchestration-jev). `agent` is not among them: route never drops the final `agent` step.
JEV_RUNNERS = ("opencode", "codex", "agy", "dsh", "claude")

# Runner names jev-mcp's `route` can return for an improver that this connector runs as another
# runner: `agent` (Claude Code's pbi-worker) can only start inside the orchestrator, while jev-mcp's
# `run` drives the same Claude Code headless as `claude`. Executors are not translated.
RUNNER_ALIASES: Mapping[str, str] = {"agent": "claude"}

EDIT_ONLY = "edit-only"

# (runner, setting) -> the fence Jev's setting applies, in harness2's terms. `default` has a
# command tool, so it is never declared.
FENCES: Mapping[tuple[str, str], FenceDeclaration] = {
    ("claude", EDIT_ONLY): FenceDeclaration(
        capabilities=frozenset({"read", "write", "search"}),
        workdir_only=True,
        asks_human=False,
        harness_credentials=False,
        runner="claude",
    ),
    ("opencode", EDIT_ONLY): FenceDeclaration(
        capabilities=frozenset({"read", "write", "search"}),
        workdir_only=True,
        asks_human=False,
        harness_credentials=False,
        runner="opencode",
    ),
}
# The runners whose fence only holds when jev-mcp starts without the user's own settings.
NEEDS_ISOLATION = frozenset({"opencode"})

# Variables of the child's environment that point opencode at settings (and so at MCP servers):
# they go to an empty directory. Authentication is under XDG_DATA_HOME, which stays.
OPENCODE_CONFIG_VARIABLES = ("XDG_CONFIG_HOME", "OPENCODE_CONFIG_DIR")
# Variables that carry opencode settings inline: they are removed from the child's environment.
#
# Other places opencode reads settings from, not closed here (opencode is absent on this
# machine, so they are unconfirmed): `~/.opencode/`, and `opencode.json` / `.opencode/` from the
# session directory up to the root of its git work tree. Keep `--session-root` outside any git
# work tree.
OPENCODE_INLINE_VARIABLES = ("OPENCODE_CONFIG_CONTENT", "OPENCODE_PERMISSION")

# The harness2 credentials that must not reach a stdio jev-mcp child.
CREDENTIAL_PREFIX = "HARNESS2_"
CREDENTIAL_SUFFIXES = ("_KEY", "_TOKEN", "_SECRET")
CREDENTIAL_NAMES = frozenset(
    {"GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "HARNESS2_VERTEX_PROJECT"}
)

ANSWER_FILE = "answer.md"
# The directory under the config root where Atelier (formerly Jev) keeps its home, and the old
# name Atelier still reads when the new one does not exist (common.resolve_home of Atelier).
ATELIER_HOME_DIRNAME = "atelier"
JEV_HOME_DIRNAME = "pbi-orchestration-jev"
# The variables Atelier reads its home from, in its order: ATELIER_HOME, then the old names.
HOME_VARIABLES = ("ATELIER_HOME", "PBI_HOME", "PBI_TEMPLATES_HOME")

OUTCOME_OK = "合格"
OUTCOME_UNAVAILABLE = "使えず"
OUTCOME_REWORK = "差し戻し"
RECORD_DUPLICATE = 2

Key = tuple[str, str, str]


def _warn(message: str) -> None:
    print(f"harness2-jev-connector: {message}", file=sys.stderr)


def _setting_pairs(template: Template) -> list[tuple[str, str]]:
    """The (runner, setting) pairs the improver steps of `template` name.

    A Jev step without `runners` names every runner of the fence table, but only when its
    setting is not the default; a default setting there says nothing about any runner. The
    runner names go through `RUNNER_ALIASES` (jev-mcp's `agent` runs as `claude`).
    """
    pairs: list[tuple[str, str]] = []
    for step in template.steps(Role.IMPROVER):
        if step.runner:
            runners: tuple[str, ...] = (step.runner,)
        elif step.runners:
            runners = tuple(dict.fromkeys(RUNNER_ALIASES.get(r, r) for r in step.runners))
        elif step.setting != DEFAULT_SETTING:
            runners = tuple(dict.fromkeys(runner for runner, _ in FENCES))
        else:
            runners = ()
        pairs.extend((runner, step.setting) for runner in runners)
    return pairs


def _settings_by_runner(template: Template) -> dict[str, str]:
    """runner -> setting for the improver steps; two settings for one runner raise ValueError."""
    by_runner: dict[str, str] = {}
    for runner, setting in _setting_pairs(template):
        known = by_runner.setdefault(runner, setting)
        if known != setting:
            raise ValueError(
                f"improver steps use runner {runner!r} with two settings "
                f"({known!r} and {setting!r}); a fence is declared per runner"
            )
    return by_runner


class JevModelConnector:
    """A model connector for one repository, through a jev-mcp client.

    `declare_opencode` decides whether `fences` declares opencode's edit-only fence. It holds
    only when jev-mcp starts without the user's opencode settings (see `child_environment`), so
    it is off unless the factory made that start. `cleanups` run once, in `close`.
    """

    def __init__(
        self,
        client: McpClient,
        repo: str | Path,
        config: Template,
        *,
        timeout: float = 60,
        declare_opencode: bool = False,
        cleanups: tuple[Callable[[], None], ...] = (),
    ) -> None:
        self.client = client
        self.repo = str(repo)
        self.config = config
        self.timeout = timeout
        self.declare_opencode = declare_opencode
        self._settings = _settings_by_runner(config)
        self._fences = self._declared()
        self._setting_of: dict[Key, str] = {}
        self._attempts: dict[str, int] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._cleanups = cleanups
        self._task_id: str | None = None
        self._run_guard = threading.Lock()
        self._run_locks: dict[tuple[str, str], threading.Lock] = {}
        self._timeout_lock = threading.Lock()

    def _declared(self) -> dict[str, FenceDeclaration]:
        out: dict[str, FenceDeclaration] = {}
        for runner, setting in self._settings.items():
            if runner in NEEDS_ISOLATION and not self.declare_opencode:
                continue
            declaration = FENCES.get((runner, setting))
            if declaration is not None:
                out[runner] = declaration
        return out

    def fences(self) -> dict[str, FenceDeclaration]:
        """The fence of each runner the improver steps use, keyed by runner name."""
        return dict(self._fences)

    def setting_for(self, choice: ModelChoice) -> str:
        """The setting a candidate from `routes` came with; the default when it is unknown."""
        key = (choice.runner, choice.model, choice.effort)
        return self._setting_of.get(key, DEFAULT_SETTING)

    def routes(self, role: str, *, task_id: str, substrate: str) -> list[ModelChoice]:
        """The candidates of `role`, best first, one per (runner, model, effort)."""
        self._task_id = task_id
        out: list[ModelChoice] = []
        seen: set[Key] = set()
        for step in self.config.steps(role):
            for choice in self._candidates(step, task_id, role):
                if role == Role.EXECUTOR and choice.runner != substrate:
                    continue
                if role == Role.IMPROVER and (
                    choice.runner not in self._fences
                    or self._settings.get(choice.runner) != step.setting
                ):
                    continue
                key = (choice.runner, choice.model, choice.effort)
                if key in seen:
                    continue
                seen.add(key)
                self._setting_of[key] = step.setting
                out.append(choice)
        return out

    def _candidates(self, step: Step, task_id: str, role: str) -> list[ModelChoice]:
        if not step.is_jev:
            return [ModelChoice(step.runner, step.model, step.effort)]
        skip = [r for r in JEV_RUNNERS if step.runners and r not in step.runners]
        try:
            answer = self.client.call_tool(
                "route",
                {"repo": self.repo, "report": task_id, "tier": step.jev, "skip": skip},
            )
        except McpToolError as exc:
            _warn(f"route for {task_id} (tier {step.jev}) gave no candidates: {exc}")
            return []
        steps = answer.get("route") if isinstance(answer, Mapping) else None
        out: list[ModelChoice] = []
        for item in steps or []:
            if not isinstance(item, Mapping):
                continue
            runner, model = item.get("runner"), item.get("model")
            if not isinstance(runner, str) or not runner or not isinstance(model, str) or not model:
                continue
            if role == Role.IMPROVER:
                alias = RUNNER_ALIASES.get(runner, runner)
                if step.runners and runner not in step.runners and alias not in step.runners:
                    continue
                runner = alias
            effort = item.get("effort")
            out.append(ModelChoice(runner, model, effort if isinstance(effort, str) else ""))
        return out

    def run_session(self, role: str, request: SessionRequest) -> SessionResult:
        """Run an improver session on `request.choice` with jev-mcp's `run`, once.

        The (report, runner) lock keeps two `run` calls for the same report and runner apart. A
        stdio client passes calls one at a time anyway, so the lock matters for HTTP. A tool
        error of jev-mcp (a path outside `--root`, a missing runner) gives `unavailable=True`,
        so harness2 goes on to the next candidate.
        """
        choice = request.choice
        runner = choice.runner
        if runner not in self._fences:
            return SessionResult("", False, error=f"runner {runner!r} has no declared fence")
        task_id = self._task_id
        if not task_id:
            return SessionResult(
                "", False, error="routes was not called: no report name", runner=runner
            )
        cwd = Path(request.cwd)
        arguments: dict[str, Any] = {
            "repo": self.repo,
            "report": task_id,
            "runner": runner,
            "model": choice.model,
            "setting": self._settings[runner],
            "prompt": _prompt(cwd, request),
            "workdir": str(cwd),
            "timeout": int(request.wall),
        }
        if choice.effort:
            arguments["effort"] = choice.effort
        # An answer left by an earlier candidate in the same directory is not this one's.
        try:
            (cwd / ANSWER_FILE).unlink(missing_ok=True)
        except OSError:
            pass
        started = time.monotonic()
        with self._run_lock(task_id, runner):
            try:
                answer = self._call_run(arguments, request.wall)
            except McpError as exc:
                return SessionResult(
                    "",
                    False,
                    wall_s=time.monotonic() - started,
                    error=f"jev-mcp run: {exc}",
                    unavailable=isinstance(exc, McpToolError),
                    runner=runner,
                )
        wall = time.monotonic() - started
        answer = answer if isinstance(answer, Mapping) else {}
        code = answer.get("code")
        text = _read_text(cwd / ANSWER_FILE)
        has_answer = bool(text.strip())
        if not has_answer:
            last = answer.get("last")
            text = last if isinstance(last, str) else ""
        unusable = bool(answer.get("unusable"))
        if code == 0 and has_answer:
            # The improver cannot write `.last.md` (it is outside the directory), so jev-mcp's
            # `unusable` for a missing `.last.md` does not count.
            ok, unavailable = True, False
        else:
            unavailable = unusable
            ok = code == 0 and bool(text.strip()) and not unusable
        error = ""
        if not ok:
            reason = answer.get("unusable_reason")
            if isinstance(reason, str) and reason:
                error = reason
            elif code != 0:
                error = f"exit code {code}"
            else:
                error = "the session left no answer"
        return SessionResult(
            text,
            ok,
            wall_s=wall,
            error=error,
            unavailable=unavailable,
            runner=runner,
        )

    def _call_run(self, arguments: dict[str, Any], wall: float) -> Any:
        """Call `run` with a client timeout that covers `wall`, and only for this call.

        harness2's `call_tool` takes no timeout per call, so the shared `client.timeout` is raised
        and put back. Read, raise, call and restore happen under one lock of the whole client, so
        a concurrent session cannot restore a value under another's call.
        """
        with self._timeout_lock:
            client_timeout = getattr(self.client, "timeout", None)
            raised = isinstance(client_timeout, (int, float)) and client_timeout < wall + 30
            if raised:
                self.client.timeout = wall + 30  # type: ignore[attr-defined]
            try:
                return self.client.call_tool("run", arguments)
            finally:
                if raised:
                    self.client.timeout = client_timeout  # type: ignore[attr-defined]

    def _run_lock(self, task_id: str, runner: str) -> threading.Lock:
        with self._run_guard:
            return self._run_locks.setdefault((task_id, runner), threading.Lock())

    def record(self, role: str, task_id: str, choice: ModelChoice, outcome: Outcome) -> None:
        """Write one attempt of `task_id` with jev-mcp's `record`; a failure is only a warning."""
        if outcome.ok:
            word = OUTCOME_OK
        elif outcome.unavailable:
            word = OUTCOME_UNAVAILABLE
        else:
            word = OUTCOME_REWORK
        with self._lock:
            attempt = self._attempts.get(task_id, 0) + 1
            try:
                answer = self._record(task_id, attempt, word, choice)
                if _code(answer) == RECORD_DUPLICATE:
                    attempt += 1
                    answer = self._record(task_id, attempt, word, choice)
            except McpError as exc:
                _warn(f"record for {task_id} failed: {exc}")
                return
            self._attempts[task_id] = attempt
        if _code(answer) != 0:
            _warn(f"record for {task_id} attempt {attempt} failed: {_stderr(answer)}")

    def _record(self, task_id: str, attempt: int, word: str, choice: ModelChoice) -> Any:
        arguments: dict[str, Any] = {
            "repo": self.repo,
            "report": task_id,
            "attempt": attempt,
            "outcome": word,
            "runner": choice.runner,
            "model": choice.model,
        }
        if choice.effort:
            arguments["effort"] = choice.effort
        return self.client.call_tool("record", arguments)

    def close(self) -> None:
        """Close the client; a second call does nothing."""
        if self._closed:
            return
        self._closed = True
        try:
            self.client.close()
        finally:
            for cleanup in self._cleanups:
                cleanup()


def _prompt(cwd: Path, request: SessionRequest) -> str:
    parts = [
        f"Your working directory is {cwd}. Do not write outside it. "
        f"Write your final answer to {cwd / ANSWER_FILE}. "
        "You need not write a .last.md summary (you cannot write there); "
        "the answer is answer.md only."
    ]
    if request.system.strip():
        parts.append(f"## System\n{request.system}")
    parts.append(request.mission)
    return "\n\n".join(parts)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def is_credential(name: str) -> bool:
    """Whether the environment variable `name` is a harness2 credential."""
    if name in CREDENTIAL_NAMES:
        return True
    return name.startswith(CREDENTIAL_PREFIX) and name.endswith(CREDENTIAL_SUFFIXES)


def child_environment(
    base: Mapping[str, str], *, config_dir: str | Path | None = None
) -> dict[str, str]:
    """The environment of a stdio jev-mcp child: `base` without harness2's credentials.

    With `config_dir`, opencode's settings variables point at it (an empty directory), so
    opencode reads neither the user's settings nor their MCP servers.
    """
    env = {k: v for k, v in base.items() if not is_credential(k)}
    if config_dir is not None:
        # Atelier finds its home (routing, templates, agents.json) through XDG_CONFIG_HOME unless
        # one of HOME_VARIABLES is set; pin ATELIER_HOME to the home the user's environment
        # points at before XDG_CONFIG_HOME is redirected below.
        if not any(env.get(name) for name in HOME_VARIABLES):
            xdg = env.get("XDG_CONFIG_HOME")
            root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
            env["ATELIER_HOME"] = str(atelier_home(root))
        for name in OPENCODE_INLINE_VARIABLES:
            env.pop(name, None)
        for name in OPENCODE_CONFIG_VARIABLES:
            env[name] = str(config_dir)
    return env


def atelier_home(root: Path) -> Path:
    """Atelier's home under the config root: `atelier`, or the old `pbi-orchestration-jev` when
    only that one exists (the same choice Atelier's common.resolve_home makes)."""
    new, old = root / ATELIER_HOME_DIRNAME, root / JEV_HOME_DIRNAME
    return old if not new.exists() and old.is_dir() else new


def _flag(value: bool | str | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _code(answer: Any) -> Any:
    return answer.get("code") if isinstance(answer, Mapping) else None


def _stderr(answer: Any) -> str:
    text = str(answer.get("stderr") or "") if isinstance(answer, Mapping) else ""
    lines = text.strip().splitlines()
    return lines[-1] if lines else f"code {_code(answer)}"


def model_connector_from_options(
    *,
    repo: str | Path,
    url: str | None = None,
    command: list[str] | str | None = None,
    config: str | Path | None = None,
    template: str | None = None,
    timeout: float | str | None = 60,
    isolate_opencode: bool | str | None = True,
) -> JevModelConnector:
    """The model connector over an HTTP (`url`) or stdio (`command`) jev-mcp client.

    `--model-connector-opt key=value` delivers strings: `command` is split with `shlex.split`
    and `timeout` becomes a float (None becomes 60). `config` is the template file (the default
    place when missing) and `template` names the template to use for this run only.

    A stdio child gets an environment without harness2's credentials. With `isolate_opencode`
    (the default) its opencode settings variables also point at an empty temporary directory
    (removed on `close`), and only then is opencode's edit-only fence declared. An HTTP jev-mcp
    is another process whose environment is not decided here, so opencode is not declared.
    """
    if isinstance(command, str):
        command = shlex.split(command)
    if timeout is None:
        timeout = 60
    elif isinstance(timeout, str):
        timeout = float(timeout)
    loaded = model_config.load(Path(config) if config else None)
    selected = loaded.template(template or None)
    isolate = command is not None and _flag(isolate_opencode, True)
    cleanups: list[Callable[[], None]] = []
    if command is None:
        client = mcp.client_from(url=url, command=command, timeout=timeout)
    else:
        config_dir = tempfile.mkdtemp(prefix="harness2-jev-opencode-") if isolate else None
        if config_dir is not None:
            cleanups.append(lambda: shutil.rmtree(config_dir, ignore_errors=True))
        env = child_environment(os.environ, config_dir=config_dir)
        try:
            client = mcp.client_from(url=url, command=command, env=env, timeout=timeout)
        except BaseException:
            for cleanup in cleanups:
                cleanup()
            raise
    try:
        return JevModelConnector(
            client,
            repo,
            selected,
            timeout=timeout,
            declare_opencode=isolate,
            cleanups=tuple(cleanups),
        )
    except ValueError:
        client.close()
        for cleanup in cleanups:
            cleanup()
        raise
