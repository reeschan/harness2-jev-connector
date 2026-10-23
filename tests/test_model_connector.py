# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0

"""JevModelConnector with a fake jev-mcp client."""

import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from harness2.core import models
from harness2.core.models import ModelChoice, Outcome, Role, SessionRequest
from harness2.plugins.mcp import McpToolError

from harness2_jev_connector import models as jev_models
from harness2_jev_connector.model_config import Step, Template
from harness2_jev_connector.models import JevModelConnector, model_connector_from_options

REPORT = "PBI-012"
# A copy of select_model.RUNNERS (common.EXTERNAL_RUNNERS) of pbi-orchestration-jev; `agent` is
# not in it.
EXTERNAL_RUNNERS = ("opencode", "codex", "agy", "dsh", "claude")


class FakeClient:
    def __init__(self, route=None, record_codes=()):
        self.route = route
        self.record_codes = list(record_codes)
        self.calls = []
        self.closed = 0

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name == "route":
            # jev-mcp's route refuses a skip that is not an external runner or a model id.
            bad = {r for r in arguments.get("skip", []) if "/" not in r} - set(EXTERNAL_RUNNERS)
            if bad:
                raise McpToolError(f"skip is not one of the external runners: {sorted(bad)}")
            if isinstance(self.route, Exception):
                raise self.route
            return {"tier": arguments["tier"], "route": self.route or []}
        if name == "record":
            code = self.record_codes.pop(0) if self.record_codes else 0
            return {"code": code, "stdout": "", "stderr": "boom" if code else ""}
        return {}

    def close(self):
        self.closed += 1

    def calls_of(self, name):
        return [args for n, args in self.calls if n == name]


def step_route(*items):
    return [{"runner": r, "model": m, "effort": e} for r, m, e in items]


def connector(template, client=None, **kwargs):
    return JevModelConnector(client or FakeClient(), "/repo", template, timeout=5, **kwargs)


def quiet(func, *args, **kwargs):
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        result = func(*args, **kwargs)
    return result, err.getvalue()


class RoutesTest(unittest.TestCase):
    def test_runner_step_is_one_candidate(self):
        template = Template({"executor": (Step(runner="opencode", model="m", effort="high"),)})
        conn = connector(template)
        got = conn.routes(Role.EXECUTOR, task_id=REPORT, substrate="opencode")
        self.assertEqual(got, [ModelChoice("opencode", "m", "high")])

    def test_jev_step_calls_route_and_lists_steps(self):
        client = FakeClient(step_route(("opencode", "a/b", ""), ("opencode", "c/d", "low")))
        conn = connector(Template({"executor": (Step(jev="haiku"),)}), client)
        got = conn.routes(Role.EXECUTOR, task_id=REPORT, substrate="opencode")
        self.assertEqual(
            got, [ModelChoice("opencode", "a/b"), ModelChoice("opencode", "c/d", "low")]
        )
        self.assertEqual(
            client.calls_of("route"),
            [{"repo": "/repo", "report": REPORT, "tier": "haiku", "skip": []}],
        )

    def test_skip_is_the_runners_not_kept(self):
        client = FakeClient(step_route(("opencode", "a/b", "")))
        step = Step(jev="sonnet", runners=("opencode", "claude"))
        connector(Template({"executor": (step,)}), client).routes(
            Role.EXECUTOR, task_id=REPORT, substrate="opencode"
        )
        self.assertEqual(client.calls_of("route")[0]["skip"], ["codex", "agy", "dsh"])

    def test_runners_match_jev_mcp_select_model(self):
        self.assertEqual(jev_models.JEV_RUNNERS, EXTERNAL_RUNNERS)
        self.assertNotIn("agent", jev_models.JEV_RUNNERS)

    def test_skip_with_runners_is_accepted_by_route(self):
        client = FakeClient(step_route(("claude", "sonnet", "")))
        step = Step(jev="sonnet", runners=("claude",), setting="edit-only")
        got = connector(Template({"improver": (step,)}), client).routes(
            Role.IMPROVER, task_id=REPORT, substrate="opencode"
        )
        self.assertEqual(got, [ModelChoice("claude", "sonnet")])
        self.assertEqual(client.calls_of("route")[0]["skip"], ["opencode", "codex", "agy", "dsh"])

    def test_improver_agent_step_becomes_claude(self):
        client = FakeClient(step_route(("opencode", "a/b", ""), ("agent", "sonnet", "high")))
        step = Step(jev="sonnet", setting="edit-only")
        conn = connector(Template({"improver": (step,)}), client)
        got = conn.routes(Role.IMPROVER, task_id=REPORT, substrate="opencode")
        self.assertEqual(got, [ModelChoice("claude", "sonnet", "high")])

    def test_executor_agent_step_is_not_translated(self):
        client = FakeClient(step_route(("agent", "sonnet", "")))
        conn = connector(Template({"executor": (Step(jev="haiku"),)}), client)
        self.assertEqual(conn.routes(Role.EXECUTOR, task_id=REPORT, substrate="claude"), [])

    def test_agent_step_respects_runners_that_exclude_claude(self):
        client = FakeClient(step_route(("agent", "sonnet", "")))
        step = Step(jev="sonnet", runners=("opencode", "claude"), setting="edit-only")
        got = connector(Template({"improver": (step,)}), client).routes(
            Role.IMPROVER, task_id=REPORT, substrate="opencode"
        )
        self.assertEqual(got, [ModelChoice("claude", "sonnet")])
        step = Step(jev="sonnet", runners=("codex",), setting="edit-only")
        got = connector(Template({"improver": (step,)}), client).routes(
            Role.IMPROVER, task_id=REPORT, substrate="opencode"
        )
        self.assertEqual(got, [])

    def test_default_improver_has_one_claude_candidate_and_one_fence(self):
        client = FakeClient(
            step_route(("opencode", "a/b", ""), ("claude", "haiku", ""), ("agent", "sonnet", ""))
        )
        conn = connector(Template(), client)
        self.assertEqual(list(conn.fences()), ["claude"])
        got = conn.routes(Role.IMPROVER, task_id=REPORT, substrate="opencode")
        self.assertEqual(got, [ModelChoice("claude", "haiku"), ModelChoice("claude", "sonnet")])
        self.assertEqual(client.calls_of("route")[0]["tier"], "sonnet")

    def test_jev_step_with_default_setting_does_not_run_under_a_declared_one(self):
        client = FakeClient(step_route(("claude", "haiku", "")))
        template = Template(
            {
                "improver": (
                    Step(runner="claude", model="sonnet", setting="edit-only"),
                    Step(jev="haiku"),
                )
            }
        )
        got = connector(template, client).routes(
            Role.IMPROVER, task_id=REPORT, substrate="opencode"
        )
        self.assertEqual(got, [ModelChoice("claude", "sonnet")])

    def test_executor_keeps_only_the_substrate(self):
        client = FakeClient(step_route(("claude", "sonnet", ""), ("opencode", "a/b", "")))
        conn = connector(Template({"executor": (Step(jev="haiku"),)}), client)
        got = conn.routes(Role.EXECUTOR, task_id=REPORT, substrate="opencode")
        self.assertEqual(got, [ModelChoice("opencode", "a/b")])

    def test_improver_keeps_only_declared_runners(self):
        template = Template(
            {
                "improver": (
                    Step(runner="claude", model="sonnet", setting="edit-only"),
                    Step(runner="codex", model="x"),
                    Step(runner="opencode", model="y", setting="edit-only"),
                )
            }
        )
        conn = connector(template)
        got = conn.routes(Role.IMPROVER, task_id=REPORT, substrate="opencode")
        self.assertEqual(got, [ModelChoice("claude", "sonnet")])
        conn = connector(template, declare_opencode=True)
        got = conn.routes(Role.IMPROVER, task_id=REPORT, substrate="opencode")
        self.assertEqual([c.runner for c in got], ["claude", "opencode"])

    def test_duplicates_are_removed_keeping_the_first(self):
        client = FakeClient(step_route(("opencode", "a/b", ""), ("opencode", "a/b", "")))
        template = Template(
            {
                "executor": (
                    Step(runner="opencode", model="a/b"),
                    Step(jev="haiku"),
                    Step(runner="opencode", model="z"),
                )
            }
        )
        got = connector(template, client).routes(
            Role.EXECUTOR, task_id=REPORT, substrate="opencode"
        )
        self.assertEqual(got, [ModelChoice("opencode", "a/b"), ModelChoice("opencode", "z")])

    def test_route_error_gives_no_candidates_and_goes_on(self):
        client = FakeClient(McpToolError("facts が無い"))
        template = Template({"executor": (Step(jev="haiku"), Step(runner="opencode", model="m"))})
        got, err = quiet(
            connector(template, client).routes,
            Role.EXECUTOR,
            task_id=REPORT,
            substrate="opencode",
        )
        self.assertEqual(got, [ModelChoice("opencode", "m")])
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_setting_is_remembered_per_candidate(self):
        template = Template(
            {"improver": (Step(runner="claude", model="sonnet", setting="edit-only"),)}
        )
        conn = connector(template)
        (choice,) = conn.routes(Role.IMPROVER, task_id=REPORT, substrate="opencode")
        self.assertEqual(conn.setting_for(choice), "edit-only")
        self.assertEqual(conn.setting_for(ModelChoice("x", "y")), "default")

    def test_default_template_is_usable(self):
        client = FakeClient(step_route(("opencode", "a/b", "")))
        got = connector(Template(), client).routes(
            Role.EXECUTOR, task_id=REPORT, substrate="opencode"
        )
        self.assertEqual(got, [ModelChoice("opencode", "a/b")])


class FencesTest(unittest.TestCase):
    def test_claude_edit_only_is_declared(self):
        template = Template(
            {"improver": (Step(runner="claude", model="sonnet", setting="edit-only"),)}
        )
        fences = connector(template).fences()
        self.assertEqual(list(fences), ["claude"])
        declaration = fences["claude"]
        self.assertEqual(declaration.capabilities, frozenset({"read", "write", "search"}))
        self.assertIs(declaration.workdir_only, True)
        self.assertIs(declaration.asks_human, False)
        self.assertIs(declaration.harness_credentials, False)
        self.assertEqual(set(models.fenced_runners(connector(template))), {"claude"})

    def test_default_setting_is_not_declared(self):
        template = Template({"improver": (Step(runner="claude", model="sonnet"),)})
        self.assertEqual(connector(template).fences(), {})

    def test_opencode_needs_the_switch(self):
        template = Template(
            {"improver": (Step(runner="opencode", model="m", setting="edit-only"),)}
        )
        self.assertEqual(connector(template).fences(), {})
        self.assertEqual(list(connector(template, declare_opencode=True).fences()), ["opencode"])

    def test_jev_step_with_runners_and_edit_only(self):
        step = Step(jev="haiku", runners=("claude",), setting="edit-only")
        fences = connector(Template({"improver": (step,)})).fences()
        self.assertEqual(list(fences), ["claude"])

    def test_two_settings_for_one_runner_is_an_error(self):
        template = Template(
            {
                "improver": (
                    Step(runner="claude", model="a", setting="edit-only"),
                    Step(runner="claude", model="b"),
                )
            }
        )
        with self.assertRaises(ValueError):
            connector(template)

    def test_default_jev_step_does_not_conflict(self):
        template = Template(
            {
                "improver": (
                    Step(jev="sonnet"),
                    Step(runner="claude", model="b", setting="edit-only"),
                )
            }
        )
        self.assertEqual(list(connector(template).fences()), ["claude"])


class RecordTest(unittest.TestCase):
    CHOICE = ModelChoice("claude", "sonnet", "low")

    def record(self, conn, outcome, task_id=REPORT, role=Role.IMPROVER):
        return quiet(conn.record, role, task_id, self.CHOICE, outcome)[1]

    def test_outcome_words_and_arguments(self):
        client = FakeClient()
        conn = connector(Template(), client)
        self.record(conn, Outcome(ok=True))
        self.record(conn, Outcome(ok=False, unavailable=True))
        self.record(conn, Outcome(ok=False))
        calls = client.calls_of("record")
        self.assertEqual([c["outcome"] for c in calls], ["合格", "使えず", "差し戻し"])
        self.assertEqual([c["attempt"] for c in calls], [1, 2, 3])
        self.assertEqual(
            {k: calls[0][k] for k in ("repo", "report", "runner", "model", "effort")},
            {
                "repo": "/repo",
                "report": REPORT,
                "runner": "claude",
                "model": "sonnet",
                "effort": "low",
            },
        )

    def test_attempts_are_counted_per_report_across_roles(self):
        client = FakeClient()
        conn = connector(Template(), client)
        self.record(conn, Outcome(ok=True), role=Role.EXECUTOR)
        self.record(conn, Outcome(ok=True), task_id="PBI-013")
        self.record(conn, Outcome(ok=True), role=Role.IMPROVER)
        self.assertEqual(
            [(c["report"], c["attempt"]) for c in client.calls_of("record")],
            [(REPORT, 1), ("PBI-013", 1), (REPORT, 2)],
        )

    def test_duplicate_attempt_is_retried_once_with_the_next_number(self):
        client = FakeClient(record_codes=[2, 0])
        conn = connector(Template(), client)
        err = self.record(conn, Outcome(ok=True))
        self.assertEqual([c["attempt"] for c in client.calls_of("record")], [1, 2])
        self.assertEqual(err, "")
        self.record(conn, Outcome(ok=True))
        self.assertEqual(client.calls_of("record")[-1]["attempt"], 3)

    def test_failure_is_a_one_line_warning(self):
        client = FakeClient(record_codes=[1])
        err = self.record(connector(Template(), client), Outcome(ok=True))
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_client_error_does_not_raise(self):
        client = FakeClient()
        client.call_tool = mock.Mock(side_effect=McpToolError("down"))
        err = self.record(connector(Template(), client), Outcome(ok=True))
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_close_twice(self):
        client = FakeClient()
        conn = connector(Template(), client)
        conn.close()
        conn.close()
        self.assertEqual(client.closed, 1)


class FactoryTest(unittest.TestCase):
    def write_config(self, tmp, data):
        path = Path(tmp) / "models.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_strings_are_converted(self):
        client = FakeClient()
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(jev_models.mcp, "client_from", return_value=client) as made,
        ):
            path = self.write_config(
                tmp,
                {
                    "use": "a",
                    "templates": {
                        "a": {"executor": [{"runner": "opencode", "model": "ma"}]},
                        "b": {"executor": [{"runner": "opencode", "model": "mb"}]},
                    },
                },
            )
            conn = model_connector_from_options(
                repo="/repo", command="jev-mcp --root '/a b'", config=str(path), timeout="2.5"
            )
            made.assert_called_once_with(
                url=None, command=["jev-mcp", "--root", "/a b"], env=mock.ANY, timeout=2.5
            )
            self.assertEqual(conn.timeout, 2.5)
            self.assertEqual(
                conn.routes(Role.EXECUTOR, task_id=REPORT, substrate="opencode"),
                [ModelChoice("opencode", "ma")],
            )
            conn = model_connector_from_options(
                repo="/repo", command="x", config=str(path), template="b"
            )
            self.assertEqual(
                conn.routes(Role.EXECUTOR, task_id=REPORT, substrate="opencode"),
                [ModelChoice("opencode", "mb")],
            )

    def test_unknown_template_is_a_value_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_config(tmp, {"templates": {"a": {}}})
            with mock.patch.object(jev_models.mcp, "client_from", return_value=FakeClient()):
                with self.assertRaises(ValueError):
                    model_connector_from_options(repo="/r", config=str(path), template="zz")

    def test_entry_point_builds_the_connector(self):
        with mock.patch.object(jev_models.mcp, "client_from", return_value=FakeClient()):
            conn = models.connector_for(
                name="jev", options={"repo": "/repo", "command": "jev-mcp", "timeout": "3"}
            )
        self.assertIsInstance(conn, JevModelConnector)
        self.assertEqual(conn.timeout, 3.0)


class RunClient(FakeClient):
    def __init__(self, answer=None, delay=0.0):
        super().__init__()
        self.answer = (
            {"code": 0, "last": "from last", "unusable": False} if answer is None else answer
        )
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.guard = threading.Lock()

    def call_tool(self, name, arguments):
        if name != "run":
            return super().call_tool(name, arguments)
        self.calls.append((name, dict(arguments)))
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self.guard:
            self.active -= 1
        return self.answer


IMPROVER = Template(
    {
        "improver": (
            Step(runner="claude", model="sonnet", effort="low", setting="edit-only"),
            Step(runner="opencode", model="a/b", setting="edit-only"),
        )
    }
)


class RunSessionTest(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.cwd = Path(holder.name)

    def request(self, choice, **kwargs):
        values = {
            "cwd": self.cwd,
            "mission": "MISSION",
            "system": "SYSTEM",
            "wall": 90,
            "label": "x",
            "choice": choice,
        }
        values.update(kwargs)
        return SessionRequest(**values)

    def start(self, client, **kwargs):
        conn = connector(IMPROVER, client, declare_opencode=True, **kwargs)
        choices = conn.routes(Role.IMPROVER, task_id=REPORT, substrate="opencode")
        return conn, choices

    def test_arguments_of_run(self):
        client = RunClient()
        conn, choices = self.start(client)
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        (args,) = client.calls_of("run")
        prompt = args.pop("prompt")
        self.assertEqual(
            args,
            {
                "repo": "/repo",
                "report": REPORT,
                "runner": "claude",
                "model": "sonnet",
                "effort": "low",
                "setting": "edit-only",
                "workdir": str(self.cwd),
                "timeout": 90,
            },
        )
        self.assertTrue(prompt.startswith(f"Your working directory is {self.cwd}."))
        self.assertIn(f"{self.cwd / 'answer.md'}", prompt.splitlines()[0])
        self.assertLess(prompt.index("## System\nSYSTEM"), prompt.index("MISSION"))
        self.assertTrue(prompt.endswith("MISSION"))
        self.assertEqual(result.runner, "claude")

    def test_empty_system_has_no_heading_and_no_effort_is_left_out(self):
        client = RunClient()
        conn, choices = self.start(client)
        conn.run_session(Role.IMPROVER, self.request(choices[1], system=""))
        (args,) = client.calls_of("run")
        self.assertNotIn("## System", args["prompt"])
        self.assertNotIn("effort", args)
        self.assertEqual(args["runner"], "opencode")

    def test_answer_file_wins(self):
        class Writing(RunClient):
            def call_tool(inner, name, arguments):  # noqa: N805
                (self.cwd / "answer.md").write_text("from answer", encoding="utf-8")
                return super().call_tool(name, arguments)

        conn, choices = self.start(Writing())
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertEqual((result.text, result.ok, result.error), ("from answer", True, ""))
        self.assertGreaterEqual(result.wall_s, 0)

    def test_last_when_no_answer_file(self):
        conn, choices = self.start(RunClient())
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertEqual((result.text, result.ok), ("from last", True))

    def test_nothing_is_not_ok(self):
        conn, choices = self.start(RunClient({"code": 0, "last": None, "unusable": False}))
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertEqual((result.text, result.ok), ("", False))
        self.assertTrue(result.error)

    def test_exit_code_not_zero_is_not_ok(self):
        conn, choices = self.start(RunClient({"code": 1, "last": "x", "unusable": False}))
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertFalse(result.ok)
        self.assertFalse(result.unavailable)
        self.assertIn("1", result.error)

    def test_unusable_is_unavailable(self):
        answer = {"code": 0, "last": "x", "unusable": True, "unusable_reason": "no output"}
        conn, choices = self.start(RunClient(answer))
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertTrue(result.unavailable)
        self.assertEqual(result.error, "no output")

    def test_client_error_is_a_failed_result(self):
        client = RunClient()
        conn, choices = self.start(client)
        client.call_tool = mock.Mock(side_effect=McpToolError("down"))
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertFalse(result.ok)
        self.assertIn("down", result.error)

    def test_runner_without_a_declaration_does_not_call_jev_mcp(self):
        client = RunClient()
        conn = connector(IMPROVER, client)  # opencode is not declared
        conn.routes(Role.IMPROVER, task_id=REPORT, substrate="opencode")
        result = conn.run_session(Role.IMPROVER, self.request(ModelChoice("opencode", "a/b")))
        self.assertFalse(result.ok)
        self.assertTrue(result.error)
        self.assertEqual(client.calls_of("run"), [])

    def test_same_report_and_runner_run_one_after_another(self):
        client = RunClient(delay=0.15)
        conn, choices = self.start(client)
        threads = [
            threading.Thread(
                target=conn.run_session, args=(Role.IMPROVER, self.request(choices[0]))
            )
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(client.calls_of("run")), 2)
        self.assertEqual(client.max_active, 1)

    def test_client_timeout_covers_the_wall_and_is_restored(self):
        client = RunClient()
        client.timeout = 60
        seen = []
        original = client.call_tool

        def spy(name, arguments):
            seen.append(client.timeout)
            return original(name, arguments)

        client.call_tool = spy
        conn, choices = self.start(client)
        conn.run_session(Role.IMPROVER, self.request(choices[0], wall=600))
        self.assertGreaterEqual(seen[-1], 600)
        self.assertEqual(client.timeout, 60)

    def test_concurrent_sessions_each_see_their_own_client_timeout(self):
        client = RunClient(delay=0.15)
        client.timeout = 60
        seen = {}
        original = client.call_tool

        def spy(name, arguments):
            seen[arguments["runner"]] = client.timeout
            return original(name, arguments)

        client.call_tool = spy
        conn, choices = self.start(client)
        walls = {"claude": 600, "opencode": 100}
        threads = [
            threading.Thread(
                target=conn.run_session,
                args=(Role.IMPROVER, self.request(choice, wall=walls[choice.runner])),
            )
            for choice in choices
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(seen, {"claude": 630, "opencode": 130})
        self.assertEqual(client.timeout, 60)

    def test_client_timeout_is_restored_after_an_error(self):
        client = RunClient()
        client.timeout = 60
        conn, choices = self.start(client)
        client.call_tool = mock.Mock(side_effect=McpToolError("down"))
        conn.run_session(Role.IMPROVER, self.request(choices[0], wall=600))
        self.assertEqual(client.timeout, 60)

    def test_stale_answer_of_an_earlier_candidate_is_removed(self):
        (self.cwd / "answer.md").write_text("old", encoding="utf-8")
        client = RunClient({"code": 0, "last": None, "unusable": False})
        conn, choices = self.start(client)
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertEqual((result.text, result.ok), ("", False))
        self.assertFalse((self.cwd / "answer.md").exists())

    def test_missing_answer_file_before_the_run_is_fine(self):
        conn, choices = self.start(RunClient())
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertTrue(result.ok)

    def test_prompt_says_last_md_is_not_needed(self):
        client = RunClient()
        conn, choices = self.start(client)
        conn.run_session(Role.IMPROVER, self.request(choices[0]))
        (args,) = client.calls_of("run")
        self.assertIn(".last.md", args["prompt"].splitlines()[0])

    def test_unusable_for_a_missing_last_md_is_ok_with_an_answer_file(self):
        class Writing(RunClient):
            def call_tool(inner, name, arguments):  # noqa: N805
                (self.cwd / "answer.md").write_text("from answer", encoding="utf-8")
                return super().call_tool(name, arguments)

        answer = {"code": 0, "last": None, "unusable": True, "unusable_reason": "no .last.md"}
        conn, choices = self.start(Writing(answer))
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertEqual((result.text, result.ok, result.unavailable), ("from answer", True, False))
        self.assertEqual(result.error, "")

    def test_unusable_with_a_failed_exit_is_unavailable_even_with_an_answer_file(self):
        class Writing(RunClient):
            def call_tool(inner, name, arguments):  # noqa: N805
                (self.cwd / "answer.md").write_text("partial", encoding="utf-8")
                return super().call_tool(name, arguments)

        answer = {"code": 1, "last": None, "unusable": True, "unusable_reason": "limit"}
        conn, choices = self.start(Writing(answer))
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertEqual((result.ok, result.unavailable), (False, True))

    def test_tool_error_is_unavailable(self):
        client = RunClient()
        conn, choices = self.start(client)
        client.call_tool = mock.Mock(side_effect=McpToolError("outside --root"))
        result = conn.run_session(Role.IMPROVER, self.request(choices[0]))
        self.assertEqual((result.ok, result.unavailable), (False, True))
        self.assertIn("outside --root", result.error)


class IsolationTest(unittest.TestCase):
    ENV = {
        "PATH": "/bin",
        "XDG_CONFIG_HOME": "/home/u/.config",
        "XDG_DATA_HOME": "/home/u/.local/share",
        "HARNESS2_GEMINI_KEY": "k",
        "HARNESS2_X_TOKEN": "t",
        "HARNESS2_Y_SECRET": "s",
        "HARNESS2_VERTEX_PROJECT": "p",
        "HARNESS2_WORKERS": "4",
        "GOOGLE_APPLICATION_CREDENTIALS": "/c.json",
        "GOOGLE_CLOUD_PROJECT": "gcp",
        "OPENCODE_CONFIG_CONTENT": "{}",
        "OPENCODE_PERMISSION": '{"bash": "allow"}',
    }
    TEMPLATE = {
        "use": "a",
        "templates": {
            "a": {"improver": [{"runner": "opencode", "model": "m", "setting": "edit-only"}]}
        },
    }

    def make(self, **options):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "models.json"
        path.write_text(json.dumps(self.TEMPLATE), encoding="utf-8")
        with (
            mock.patch.dict("os.environ", self.ENV, clear=True),
            mock.patch.object(jev_models.mcp, "client_from", return_value=FakeClient()) as made,
        ):
            conn = model_connector_from_options(repo="/r", config=str(path), **options)
        self.addCleanup(conn.close)
        return conn, made

    def test_stdio_child_environment(self):
        conn, made = self.make(command="jev-mcp")
        env = made.call_args.kwargs["env"]
        config_dir = Path(env["XDG_CONFIG_HOME"])
        self.assertTrue(config_dir.is_dir())
        self.assertEqual(list(config_dir.iterdir()), [])
        self.assertEqual(env["OPENCODE_CONFIG_DIR"], str(config_dir))
        self.assertNotIn("OPENCODE_CONFIG_CONTENT", env)
        self.assertNotIn("OPENCODE_PERMISSION", env)
        # Jev still finds its home where the user's environment points.
        self.assertEqual(env["PBI_HOME"], "/home/u/.config/pbi-orchestration-jev")
        self.assertEqual(env["XDG_DATA_HOME"], "/home/u/.local/share")
        self.assertEqual(env["PATH"], "/bin")
        self.assertEqual(env["HARNESS2_WORKERS"], "4")
        for name in self.ENV:
            if name.startswith(("HARNESS2_", "GOOGLE_")) and name != "HARNESS2_WORKERS":
                self.assertNotIn(name, env)
        self.assertEqual(list(conn.fences()), ["opencode"])
        conn.close()
        self.assertFalse(config_dir.exists())

    def test_isolation_off_keeps_config_but_not_credentials(self):
        conn, made = self.make(command="jev-mcp", isolate_opencode="false")
        env = made.call_args.kwargs["env"]
        self.assertEqual(env["XDG_CONFIG_HOME"], "/home/u/.config")
        self.assertNotIn("HARNESS2_GEMINI_KEY", env)
        self.assertEqual(conn.fences(), {})

    def test_http_does_not_declare_opencode(self):
        conn, made = self.make(url="http://127.0.0.1:1/mcp")
        self.assertNotIn("env", made.call_args.kwargs)
        self.assertEqual(conn.fences(), {})


if __name__ == "__main__":
    unittest.main()
