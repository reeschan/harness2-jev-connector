English | [日本語](README.ja.md)

# harness2-jev-connector

## Overview

harness2-jev-connector is a task connector for [harness2](https://github.com/reeschan/harness2)'s
`harness2-intake`. It claims the PBIs that are queued in jev-mcp. harness2 runs each PBI in a git
worktree for every rollout. The connector takes only the diff of the chosen run and applies it to the
source repository, after the source's untouched-files check (`docs/pbi/tools/unchanged.py`). It then
sends the verification results and the details back with `submit_result`.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/architecture/architecture-dark.svg">
  <img src="assets/architecture/architecture-light.svg" alt="harness2-jev-connector architecture: the connector, loaded by harness2-intake through the jev-mcp entry point, claims PBIs from jev-mcp and hands them to IntakeAdapter, which runs each rollout in its own git worktree; the connector verifies the chosen run through jev-mcp, applies its diff to the source repository after the unchanged-files guard, and submits the result, while jev-mcp reads PBIs from and writes reports to docs/pbi.">
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
