# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0

"""model_config on temporary JSON files and on the example shipped with the package."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness2.core.models import Role

from harness2_jev_connector.model_config import (
    DEFAULT_SETTING,
    DIRNAME,
    ENV_VAR,
    FILENAME,
    Step,
    Template,
    default_path,
    load,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "models.json"


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, data, name="models.json"):
        path = self.tmp / name
        text = data if isinstance(data, str) else json.dumps(data)
        path.write_text(text, encoding="utf-8")
        return path

    def invalid(self, data):
        path = self.write(data)
        with self.assertRaises(ValueError) as cm:
            load(path)
        return str(cm.exception)


class DefaultPathTest(ConfigTestCase):
    def test_env_override_wins(self):
        env = {ENV_VAR: "/models.json", "XDG_CONFIG_HOME": "/cfg"}
        self.assertEqual(default_path(env=env), Path("/models.json"))

    def test_xdg_config_home(self):
        self.assertEqual(
            default_path(env={"XDG_CONFIG_HOME": "/cfg"}),
            Path("/cfg") / DIRNAME / FILENAME,
        )

    def test_home_dot_config_without_xdg(self):
        self.assertEqual(
            default_path(env={}, home=Path("/home/user")),
            Path("/home/user/.config") / DIRNAME / FILENAME,
        )


class LoadTest(ConfigTestCase):
    def test_reads_runner_and_jev_steps(self):
        path = self.write(
            {
                "templates": {
                    "t": {
                        "executor": [{"runner": "opencode", "model": "m", "effort": "high"}],
                        "improver": [
                            {"jev": "opus", "runners": ["claude"], "setting": "edit-only"}
                        ],
                    }
                }
            }
        )
        template = load(path).template("t")
        self.assertEqual(
            template.steps(Role.EXECUTOR),
            (Step(runner="opencode", model="m", effort="high"),),
        )
        self.assertEqual(
            template.steps(Role.IMPROVER),
            (Step(jev="opus", runners=("claude",), setting="edit-only"),),
        )

    def test_runner_step_defaults_setting_and_effort(self):
        path = self.write(
            {"templates": {"t": {"executor": [{"runner": "claude", "model": "sonnet"}]}}}
        )
        step = load(path).template("t").steps(Role.EXECUTOR)[0]
        self.assertEqual(step.setting, DEFAULT_SETTING)
        self.assertEqual(step.effort, "")
        self.assertFalse(step.is_jev)

    def test_omitted_setting_depends_on_the_role(self):
        path = self.write(
            {
                "templates": {
                    "t": {
                        "executor": [{"jev": "haiku"}],
                        "improver": [
                            {"jev": "sonnet"},
                            {"runner": "claude", "model": "sonnet"},
                        ],
                    }
                }
            }
        )
        template = load(path).template("t")
        self.assertEqual(template.steps(Role.EXECUTOR)[0].setting, "default")
        self.assertEqual([s.setting for s in template.steps(Role.IMPROVER)], ["edit-only"] * 2)

    def test_jev_step_without_runners_keeps_every_runner(self):
        path = self.write({"templates": {"t": {"executor": [{"jev": "haiku"}]}}})
        step = load(path).template("t").steps(Role.EXECUTOR)[0]
        self.assertEqual(step.runners, ())
        self.assertTrue(step.is_jev)

    def test_missing_default_file_uses_defaults(self):
        missing = self.tmp / "missing.json"
        with mock.patch.dict(os.environ, {ENV_VAR: str(missing)}):
            config = load(None)
        self.assertIsNone(config.use)
        self.assertEqual(config.template().steps(Role.EXECUTOR), (Step(jev="haiku"),))
        self.assertEqual(
            config.template().steps(Role.IMPROVER), (Step(jev="sonnet", setting="edit-only"),)
        )

    def test_default_file_from_env_is_read(self):
        path = self.write({"templates": {"t": {"executor": [{"jev": "opus"}]}}})
        with mock.patch.dict(os.environ, {ENV_VAR: str(path)}):
            config = load(None)
        self.assertEqual(config.template("t").steps(Role.EXECUTOR), (Step(jev="opus"),))

    def test_explicit_missing_path_uses_defaults(self):
        config = load(self.tmp / "missing.json")
        self.assertEqual(config.template().steps(Role.EXECUTOR), (Step(jev="haiku"),))
        self.assertEqual(
            config.template().steps(Role.IMPROVER), (Step(jev="sonnet", setting="edit-only"),)
        )

    def test_use_selects_the_template(self):
        path = self.write(
            {
                "use": "one",
                "templates": {
                    "one": {"executor": [{"jev": "haiku"}]},
                    "two": {"executor": [{"jev": "opus"}]},
                },
            }
        )
        config = load(path)
        self.assertEqual(config.template().steps(Role.EXECUTOR), (Step(jev="haiku"),))
        self.assertEqual(config.template("two").steps(Role.EXECUTOR), (Step(jev="opus"),))

    def test_no_use_returns_the_default_template(self):
        path = self.write({"templates": {"one": {"executor": [{"jev": "opus"}]}}})
        self.assertEqual(load(path).template(), Template())

    def test_missing_role_uses_that_role_default(self):
        path = self.write({"templates": {"one": {"executor": [{"jev": "opus"}]}}})
        template = load(path).template("one")
        self.assertEqual(template.steps(Role.EXECUTOR), (Step(jev="opus"),))
        self.assertEqual(template.steps(Role.IMPROVER), (Step(jev="sonnet", setting="edit-only"),))

    def test_unknown_role_raises(self):
        with self.assertRaises(ValueError):
            Template().steps("reviewer")

    def test_unknown_template_lists_names(self):
        path = self.write({"templates": {"one": {}, "two": {}}})
        with self.assertRaises(ValueError) as cm:
            load(path).template("three")
        self.assertIn("one", str(cm.exception))
        self.assertIn("two", str(cm.exception))


class ValidationTest(ConfigTestCase):
    def test_not_json(self):
        self.assertIn("not valid JSON", self.invalid("{"))

    def test_root_not_object(self):
        self.assertIn("expected an object", self.invalid([]))

    def test_templates_not_object(self):
        self.assertIn("templates", self.invalid({"templates": []}))

    def test_templates_missing(self):
        self.assertIn("templates", self.invalid({"use": "t"}))

    def test_template_not_object(self):
        self.assertIn("expected an object", self.invalid({"templates": {"t": []}}))

    def test_role_not_array(self):
        self.assertIn("array", self.invalid({"templates": {"t": {"executor": {}}}}))

    def test_step_neither_runner_nor_jev(self):
        self.assertIn("either", self.invalid({"templates": {"t": {"executor": [{}]}}}))

    def test_step_with_both_keys(self):
        step = {"runner": "claude", "model": "sonnet", "jev": "haiku"}
        self.assertIn("not both", self.invalid({"templates": {"t": {"executor": [step]}}}))

    def test_jev_tier(self):
        step = {"jev": "large"}
        self.assertIn("haiku", self.invalid({"templates": {"t": {"executor": [step]}}}))

    def test_setting(self):
        step = {"runner": "claude", "model": "sonnet", "setting": "read-only"}
        self.assertIn("setting", self.invalid({"templates": {"t": {"executor": [step]}}}))

    def test_improver_cannot_declare_default(self):
        for step in (
            {"jev": "sonnet", "setting": "default"},
            {"runner": "claude", "model": "m", "setting": "default"},
        ):
            message = self.invalid({"templates": {"t": {"improver": [step]}}})
            self.assertIn("improver", message)
            self.assertIn("default", message)

    def test_executor_may_declare_default(self):
        path = self.write(
            {"templates": {"t": {"executor": [{"jev": "haiku", "setting": "default"}]}}}
        )
        self.assertEqual(load(path).template("t").steps(Role.EXECUTOR)[0].setting, "default")

    def test_runners_empty(self):
        step = {"jev": "haiku", "runners": []}
        self.assertIn("empty", self.invalid({"templates": {"t": {"executor": [step]}}}))

    def test_runners_string(self):
        step = {"jev": "haiku", "runners": "opencode"}
        self.assertIn("array", self.invalid({"templates": {"t": {"executor": [step]}}}))

    def test_runners_contains_non_string(self):
        step = {"jev": "haiku", "runners": ["opencode", 1]}
        self.assertIn("[1]", self.invalid({"templates": {"t": {"executor": [step]}}}))

    def test_use_names_unknown_template(self):
        self.assertIn("unknown template", self.invalid({"use": "t", "templates": {}}))

    def test_unknown_top_key(self):
        message = self.invalid({"templates": {}, "priority": 1})
        self.assertIn("unknown key", message)
        self.assertIn("priority", message)

    def test_unknown_template_key(self):
        message = self.invalid({"templates": {"t": {"reviewer": []}}})
        self.assertIn("unknown key", message)
        self.assertIn("reviewer", message)

    def test_unknown_step_key(self):
        message = self.invalid({"templates": {"t": {"executor": [{"jev": "haiku", "model": "m"}]}}})
        self.assertIn("unknown key", message)
        self.assertIn("model", message)

    def test_runner_step_needs_model(self):
        message = self.invalid({"templates": {"t": {"executor": [{"runner": "claude"}]}}})
        self.assertIn("model", message)


class ExamplesTest(unittest.TestCase):
    def setUp(self):
        self.config = load(EXAMPLE)

    def test_example_uses_claude_only(self):
        self.assertEqual(self.config.use, "claude-only")
        self.assertEqual(
            self.config.template().steps(Role.EXECUTOR),
            (Step(jev="haiku", runners=("opencode",)),),
        )
        self.assertEqual(
            self.config.template().steps(Role.IMPROVER),
            (Step(runner="claude", model="sonnet", setting="edit-only"),),
        )

    def test_example_cheap_template(self):
        cheap = self.config.template("cheap")
        self.assertEqual(cheap.steps(Role.EXECUTOR), (Step(jev="haiku", runners=("opencode",)),))
        self.assertEqual(
            cheap.steps(Role.IMPROVER),
            (
                Step(runner="claude", model="sonnet", setting="edit-only"),
                Step(jev="haiku", runners=("opencode",), setting="edit-only"),
            ),
        )

    def test_example_has_no_model_ids(self):
        for template in self.config.templates.values():
            for role in (Role.EXECUTOR, Role.IMPROVER):
                for step in template.steps(role):
                    self.assertNotIn("/", step.model)


if __name__ == "__main__":
    unittest.main()
