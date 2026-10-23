English | [日本語](README.ja.md)

# harness2-jev-connector

## Overview

harness2-jev-connector is a task connector for [harness2](https://github.com/reeschan/harness2)'s
`harness2-intake`. It claims the PBIs that are queued in jev-mcp. harness2 runs each PBI in a git
worktree for every rollout. The connector takes only the diff of the chosen run and applies it to the
source repository, after the source's untouched-files check (`docs/pbi/tools/unchanged.py`). It then
sends the verification results and the details back with `submit_result`.

The package also has a model connector, `jev`. harness2-intake loads it with `--model-connector jev`
and gets its model candidates, fence declarations and attempt records through the same jev-mcp.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/architecture/architecture-dark.svg">
  <img src="assets/architecture/architecture-light.svg" alt="harness2-jev-connector architecture: the connector, loaded by harness2-intake through the jev-mcp entry point, claims PBIs from jev-mcp and hands them to IntakeAdapter, which runs each rollout in its own git worktree; the connector verifies the chosen run through jev-mcp, applies its diff to the source repository after the unchanged-files guard, and submits the result, while jev-mcp reads PBIs from and writes reports to docs/pbi. The model connector, loaded through the jev entry point, reads a named template from models.json and gives harness2-intake its executor models (opencode) and the improver its sessions, asking jev-mcp to route, run one try on a Jev runner (claude or opencode with the edit-only setting) and record each attempt.">
</picture>

The diagram source is `assets/architecture/architecture.d2`. Redraw it with the arch-diagram skill's `render.py`.

## Requirements

- POSIX: Linux or macOS. On Windows, use WSL.
- Python 3.12 to 3.13.
- harness2. The install below brings it in as a dependency.
- jev-mcp: the `jev_mcp.py` server that ships with the pbi-orchestration-jev skill.

## Install

```sh
pip install "harness2-jev-connector @ git+https://github.com/reeschan/harness2-jev-connector.git"
```

harness2 is installed from git as a dependency, pinned to the commit this package is tested against (see `pyproject.toml`).

## Usage

Start jev-mcp over stdio:

```sh
python <path-to>/jev_mcp.py --root <repo> --root <work-root>
```

Or start it over HTTP:

```sh
python <path-to>/jev_mcp.py --http --port 7830 --root <repo> --root <work-root>
```

Then run harness2-intake against the HTTP endpoint:

```sh
harness2-intake --connector jev-mcp \
  --connector-opt url=http://127.0.0.1:7830/mcp \
  --repo <repo> --work-root <work-root>
```

Or let harness2-intake start jev-mcp itself over stdio:

```sh
harness2-intake --connector jev-mcp \
  --connector-opt command="python <path-to>/jev_mcp.py --root <repo> --root <work-root>" \
  --repo <repo> --work-root <work-root>
```

## Connector options

| Option | Default | Meaning |
| --- | --- | --- |
| `url` | none | URL of a jev-mcp HTTP endpoint, for example `http://127.0.0.1:7830/mcp`. Use it instead of `command`. |
| `command` | none | Command that starts jev-mcp over stdio, as a string or a list. A string is split with `shlex.split`. |
| `timeout` | 60 | Seconds to wait for one MCP call. A value from `--connector-opt` arrives as a string and is converted to a float. |
| `scrub_env` | `TYPESAFE_API_KEY` | Comma-separated names of the environment variables to remove from the agent's environment. Name the variables that hold secrets, for example `EXAMPLE_SECRET,ANOTHER_SECRET`. Pass a value to replace the default; an empty value removes nothing. |
| `pbi_dir` | `docs/pbi` | Directory in the repository that holds the PBI files. The connector keeps it out of the diff. |
| `runner` | `harness2` | Runner name that the connector records on a claim. |

harness2-intake supplies `repo`, `mode`, `locate` and `timeout` (its own `--timeout`, 3600 seconds
by default). A `--connector-opt` with the same name wins. `connector_from_options` uses 60 seconds
for `timeout` when the caller does not pass one.

## Model connector

The `jev` model connector gives harness2-intake its model candidates, fence declarations and
attempt records through the same jev-mcp as the task connector. Pass its options in
`--model-connector-opt`:

```sh
harness2-intake --model-connector jev \
  --model-connector-opt url=http://127.0.0.1:7830/mcp \
  --model-connector-opt repo=<repo> \
  --repo <repo> --work-root <work-root>
```

| Option | Default | Meaning |
| --- | --- | --- |
| `url` | none | URL of a jev-mcp HTTP endpoint, for example `http://127.0.0.1:7830/mcp`. Use it instead of `command`. |
| `command` | none | Command that starts jev-mcp over stdio, as a string or a list. A string is split with `shlex.split`. |
| `repo` | none | Path of the repository, required. jev-mcp answers about this repository. |
| `config` | see below | Path of the template file. The default place is used when the value is missing. |
| `template` | none | Name of the template to use for this run, in place of the file's `use`. |
| `timeout` | 60 | Seconds to wait for one MCP call. A value from `--model-connector-opt` arrives as a string and is converted to a float. |
| `isolate_opencode` | `true` | With `command`, points opencode's settings variables at an empty temporary directory. opencode's fence is declared only while this isolation holds. |

The template file holds the template to use and the steps of each role. The connector reads
`HARNESS2_JEV_MODELS` when it is set, else `$XDG_CONFIG_HOME/harness2-jev-connector/models.json`,
or `~/.config/harness2-jev-connector/models.json` when `XDG_CONFIG_HOME` is not set. `config=`
names another file.

```json
{
  "use": "claude-only",
  "templates": {
    "claude-only": {
      "executor": [{"jev": "haiku", "runners": ["opencode"]}],
      "improver": [{"runner": "claude", "model": "sonnet", "setting": "edit-only"}]
    },
    "cheap": {
      "executor": [{"jev": "haiku", "runners": ["opencode"]}],
      "improver": [
        {"runner": "claude", "model": "sonnet", "setting": "edit-only"},
        {"jev": "haiku", "runners": ["opencode"], "setting": "edit-only"}
      ]
    }
  }
}
```

`use` names the template a caller gets when it names none. A step is either a runner step
(`runner` and `model`, with optional `effort` and `setting`) or a Jev step (`jev` with a tier, and
optional `runners` and `setting`). An omitted `setting` is `default` for the executor and
`edit-only` for the improver; an improver step cannot say `default`, because that setting declares
no fence. `runners` keeps only those runners from Jev's answer; omit it to keep every runner. The
connector asks jev-mcp's `route` for a Jev step and turns the steps it returns into candidates.
For an improver, Jev's `agent` step (Claude Code's pbi-worker) becomes `claude` with the same
model, run headless. A role a template does not name uses that role's default: executor
`{"jev": "haiku"}`, improver `{"jev": "sonnet", "setting": "edit-only"}`. When the file is
missing, both defaults are used; with Jev's default routing, the default improver is `claude` with
`edit-only`.

`examples/models.json` holds two templates. `claude-only` is the default (`use`): its executor
asks Jev's haiku tier and keeps opencode, and its improver runs `claude` with `edit-only`. `cheap`
has the same executor and an improver that tries `claude` with `edit-only` first, then Jev's
haiku tier among opencode with `edit-only`. To use a template for one run only, pass
`--model-connector-opt template=cheap`.

The improver session runs with jev-mcp's `run`, once for each candidate, in the `edit-only`
setting: it reads and writes, and has no command tool. Its working directory is a directory under
harness2's `--session-root` (default `<work-root>/_sessions`), and the session writes its answer
to `answer.md` in it. That is the only answer: `.last.md` is not written. Point `--session-root`
under jev-mcp's `--root`; `run` does not accept a working directory outside it.

An improver candidate is kept only when its runner has a declared fence. The connector translates
`edit-only` for `claude` and for `opencode` into the same declaration in harness2's terms: read,
write and search only, working directory only, asks no human, and no harness credentials. An HTTP
jev-mcp passes its own startup environment to the runner as it is, so start it in an environment
without harness2's credentials; the `claude` declaration assumes this.

opencode also reads the user's own settings and their MCP servers, so its declaration is given
only when the connector starts jev-mcp over stdio and the isolation holds. In that start the
child's `XDG_CONFIG_HOME` and `OPENCODE_CONFIG_DIR` point at an empty temporary directory,
`OPENCODE_CONFIG_CONTENT` and `OPENCODE_PERMISSION` are removed from the child's environment,
`PBI_HOME` keeps Jev's home where it was, and the harness2 credential variables are removed from
the child's environment. Over HTTP the connector cannot choose jev-mcp's environment, so it does
not declare opencode; starting that jev-mcp with an isolated environment does not change that. The
isolation also keeps opencode's user-wide settings from being read, including provider
definitions, so provide opencode's authentication and providers under `XDG_DATA_HOME` or through
environment variables.

opencode may also read `~/.opencode` and a project's `opencode.json` or `.opencode/`, and the
isolation does not close those places. List opencode as an improver in a template only for an
environment where those files and directories are absent.

Every attempt is written with jev-mcp's `record`, one line in Jev's `runs.jsonl`: `合格` when the
attempt succeeded, `使えず` when its choice was unavailable, and `差し戻し` otherwise.

Known limitations:

- `record` takes the token counts and the cost from the repository's `docs/pbi/<runner>/` logs,
  which Jev reads. An improver session writes its logs in its own working directory, so those
  values can be empty or stale.
- When the attempt number is already taken, the connector calls `record` again once with the next
  number. If that number is taken too, the attempt is not recorded.
- The report name passed to `run` is the one from the last `routes` call. The connector expects
  harness2-intake to run tasks one at a time; do not use it from a caller that runs tasks in
  parallel.

## The `--root` rule

jev-mcp's `verify` requires the working directory to be under one of its `--root` options. A
rollout's worktree is created under `--work-root`, so pass both `--repo` and `--work-root` as
`--root`. Keep `--work-root` outside the work tree of `--repo`; harness2-intake checks this.

## What the connector provides

- `workspace_policy`: keeps `docs/pbi` out of the diff.
- `scrub_env`: removes the named variables from the agent's environment. By default it removes
  `TYPESAFE_API_KEY`, the variable jev reads its API key from; only jev-mcp needs it.
- `judge()`: runs `verify` on a run and returns `n_passed`, `n_criteria` and `all_pass`. Selection
  does not use it.
- `report(task_id)`: the five-line report that jev-mcp wrote for the task.
- `close()`: closes the MCP client.
- If the connection drops while claiming, the connector fails the tasks that it has already claimed
  with `fail_task`, then stops.
- model connector (`jev`, the `jev` entry point): turns a template into the candidate models of
  each role, declares the fence of each improver runner, runs improver sessions with jev-mcp's
  `run`, and records every attempt with jev-mcp's `record`.

## Development

Run these in WSL:

```sh
uv run --python 3.12 python -m unittest discover -s tests
uvx ruff@0.17.1 check .
uvx ruff@0.17.1 format --check .
```

## License

Apache-2.0. Some code is moved from harness2 (Apache-2.0, Copyright 2026 Google LLC). Each such
file says so at the top.
