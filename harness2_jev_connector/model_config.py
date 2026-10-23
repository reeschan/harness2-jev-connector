# Copyright 2026 Ryuki Tobita
# SPDX-License-Identifier: Apache-2.0

"""The model templates of the connector: named steps to try for each role.

The file is JSON and looks like this:

    {"use": "claude-only",
     "templates": {
       "claude-only": {
         "executor": [{"jev": "haiku"}],
         "improver": [{"runner": "claude", "model": "sonnet", "setting": "edit-only"}]
       }
     }}

A step is either a runner step (`runner` and `model`, with optional `effort` and `setting`)
or a Jev step (`jev` and an optional `runners` list, plus `setting`). An omitted `setting` is
`default` for the executor and `edit-only` for the improver; an improver step cannot say
`default`, because that setting declares no fence. A role a template does
not name uses the default steps of that role. `use` names the template a caller gets when it
names none.

Jev's routing.json names a template per priority and carries deadlines and alternating groups.
This connector keeps only the named templates and the single `use`, so a run is reproducible.
The loader never talks to jev-mcp: it only reads and checks the file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness2.core.models import ROLES, Role

ENV_VAR = "HARNESS2_JEV_MODELS"
DIRNAME = "harness2-jev-connector"
FILENAME = "models.json"
DEFAULT_SETTING = "default"
EDIT_ONLY_SETTING = "edit-only"
SETTINGS = ("default", "edit-only")
JEV_TIERS = ("haiku", "sonnet", "opus")
_TOP_KEYS = frozenset({"use", "templates"})
_RUNNER_KEYS = frozenset({"runner", "model", "effort", "setting"})
_JEV_KEYS = frozenset({"jev", "runners", "setting"})


@dataclass(frozen=True)
class Step:
    """One way to run a role: a runner with a model, or a Jev tier to ask.

    Exactly one of `runner` and `jev` is set. `runners` is the subset of Jev's runners to
    keep for a Jev step; an empty tuple means every runner is kept. A runner step has no
    `runners` and a Jev step has no `runner`, `model` or `effort`.
    """

    runner: str = ""
    model: str = ""
    effort: str = ""
    jev: str = ""
    runners: tuple[str, ...] = ()
    setting: str = DEFAULT_SETTING

    @property
    def is_jev(self) -> bool:
        """True for a Jev step: ask jev-mcp's `route` instead of naming a runner here."""
        return bool(self.jev)


DEFAULT_STEPS: Mapping[str, tuple[Step, ...]] = {
    Role.EXECUTOR: (Step(jev="haiku"),),
    Role.IMPROVER: (Step(jev="sonnet", setting=EDIT_ONLY_SETTING),),
}

# The setting of a step that names none. An improver only runs under a fence harness2 accepts,
# which `default` (it has a command tool) never gives; an executor is fenced by its substrate.
OMITTED_SETTING: Mapping[str, str] = {
    Role.EXECUTOR: DEFAULT_SETTING,
    Role.IMPROVER: EDIT_ONLY_SETTING,
}


@dataclass(frozen=True)
class Template:
    """The steps of one named template, keyed by role; a missing role uses its defaults.

    `by_role` is normally built by `load`; `Template()` is the default configuration.
    """

    by_role: Mapping[str, tuple[Step, ...]] = field(default_factory=dict)

    def steps(self, role: str) -> tuple[Step, ...]:
        """The steps to try for `role`, in order; the role's defaults when it is missing."""
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}; known roles: {', '.join(ROLES)}")
        if role in self.by_role:
            return tuple(self.by_role[role])
        return DEFAULT_STEPS[role]


DEFAULT_TEMPLATE = Template()


@dataclass(frozen=True)
class ModelConfig:
    """The templates of a file and the name `use` selects.

    `use` is None when the file names no template; `template(None)` then returns the
    default configuration (executor: Jev haiku, improver: Jev sonnet).
    """

    templates: Mapping[str, Template] = field(default_factory=dict)
    use: str | None = None

    def template(self, name: str | None = None) -> Template:
        """The template called `name`, the one `use` names, or the default configuration.

        A name that is not in the file raises ValueError listing the names that are.
        """
        if name is not None:
            return self._named(name)
        if self.use is not None:
            return self._named(self.use)
        return DEFAULT_TEMPLATE

    def _named(self, name: str) -> Template:
        try:
            return self.templates[name]
        except KeyError:
            names = ", ".join(sorted(self.templates)) or "(none)"
            raise ValueError(f"unknown template {name!r}; available templates: {names}") from None


DEFAULT_CONFIG = ModelConfig()


def default_path(
    *,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """The default place of the file: `HARNESS2_JEV_MODELS`, else the XDG config directory.

    The XDG directory is `$XDG_CONFIG_HOME/harness2-jev-connector/models.json`, or
    `~/.config/harness2-jev-connector/models.json` when `XDG_CONFIG_HOME` is unset. A variable
    that is set but empty counts as unset. `env` and `home` are for tests; by default the
    process environment and `Path.home()` are used.
    """
    values = os.environ if env is None else env
    override = values.get(ENV_VAR)
    if override:
        return Path(override)
    base = values.get("XDG_CONFIG_HOME")
    if base:
        root = Path(base)
    else:
        root = (Path.home() if home is None else home) / ".config"
    return root / DIRNAME / FILENAME


def load(path: Path | None = None) -> ModelConfig:
    """Read and check a model config; the default file or the defaults when it is missing.

    `path` None looks at `default_path()`. A missing file (the default one or a given one)
    returns the default configuration. Every problem with the contents (not JSON, a wrong key,
    a wrong step, a `use` that names no template) raises ValueError naming the item.
    """
    target = default_path() if path is None else Path(path)
    if not target.is_file():
        return DEFAULT_CONFIG
    text = target.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{target}: not valid JSON: {exc}") from exc
    return _parse(data, str(target))


def _fail(where: str, message: str) -> ValueError:
    return ValueError(f"{where}: {message}")


def _object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(where, f"expected an object, got {type(value).__name__}")
    return value


def _keys(obj: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise _fail(where, f"unknown key(s) {unknown}; allowed keys: {sorted(allowed)}")


def _string(value: Any, where: str, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _fail(where, f"expected a string, got {type(value).__name__}")
    if not value and not empty:
        raise _fail(where, "must not be empty")
    return value


def _setting(value: Any, where: str) -> str:
    setting = _string(value, where)
    if setting not in SETTINGS:
        raise _fail(where, f"must be one of {list(SETTINGS)}, got {setting!r}")
    return setting


def _role_setting(value: Any, where: str, role: str) -> str:
    setting = _setting(value, where)
    if role == Role.IMPROVER and setting == DEFAULT_SETTING:
        raise _fail(where, "an improver step cannot use 'default': it declares no fence")
    return setting


def _runners(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(where, f"expected an array of runner names, got {type(value).__name__}")
    if not value:
        raise _fail(where, "must not be empty; omit it to keep every runner")
    return tuple(_string(item, f"{where}[{index}]") for index, item in enumerate(value))


def _step(value: Any, where: str, role: str) -> Step:
    obj = _object(value, where)
    omitted = OMITTED_SETTING[role]
    has_runner = "runner" in obj
    has_jev = "jev" in obj
    if has_runner and has_jev:
        raise _fail(where, "a step must have either 'runner' or 'jev', not both")
    if not has_runner and not has_jev:
        raise _fail(where, "a step must have either 'runner' or 'jev'")
    if has_runner:
        _keys(obj, _RUNNER_KEYS, where)
        if "model" not in obj:
            raise _fail(f"{where}.model", "a runner step needs 'model'")
        return Step(
            runner=_string(obj["runner"], f"{where}.runner"),
            model=_string(obj["model"], f"{where}.model"),
            effort=_string(obj.get("effort", ""), f"{where}.effort", empty=True),
            setting=_role_setting(obj.get("setting", omitted), f"{where}.setting", role),
        )
    _keys(obj, _JEV_KEYS, where)
    jev = _string(obj["jev"], f"{where}.jev")
    if jev not in JEV_TIERS:
        raise _fail(f"{where}.jev", f"must be one of {list(JEV_TIERS)}, got {jev!r}")
    runners = _runners(obj["runners"], f"{where}.runners") if "runners" in obj else ()
    return Step(
        jev=jev,
        runners=runners,
        setting=_role_setting(obj.get("setting", omitted), f"{where}.setting", role),
    )


def _template(value: Any, where: str) -> Template:
    obj = _object(value, where)
    _keys(obj, frozenset(ROLES), where)
    by_role: dict[str, tuple[Step, ...]] = {}
    for role in ROLES:
        if role not in obj:
            continue
        raw = obj[role]
        if not isinstance(raw, list):
            raise _fail(f"{where}.{role}", f"expected an array of steps, got {type(raw).__name__}")
        by_role[role] = tuple(
            _step(item, f"{where}.{role}[{index}]", role) for index, item in enumerate(raw)
        )
    return Template(by_role)


def _parse(data: Any, where: str) -> ModelConfig:
    obj = _object(data, where)
    _keys(obj, _TOP_KEYS, where)
    if "templates" not in obj:
        raise _fail(where, "missing 'templates'")
    raw = _object(obj["templates"], f"{where}.templates")
    templates: dict[str, Template] = {}
    for name, value in raw.items():
        _string(name, f"{where}.templates")
        templates[name] = _template(value, f"{where}.templates[{name!r}]")
    use = None
    if "use" in obj:
        use = _string(obj["use"], f"{where}.use")
        if use not in templates:
            names = ", ".join(sorted(templates)) or "(none)"
            raise _fail(
                f"{where}.use",
                f"unknown template {use!r}; available templates: {names}",
            )
    return ModelConfig(templates=templates, use=use)
