[English](README.md) | 日本語

# harness2-jev-connector

## 概要

harness2-jev-connector は、[harness2](https://github.com/reeschan/harness2) の `harness2-intake` のための
タスクコネクターです。jev-mcp に積まれた PBI を claim します。harness2 はロールアウトごとに git worktree
を作り、そこで PBI を実行します。コネクターは選ばれた実行の diff だけを取り、ソースの触らない範囲の確認
（`docs/pbi/tools/unchanged.py`）を通したあと、ソースのリポジトリに当てます。そして検証の結果と詳細を
`submit_result` で返します。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/architecture/architecture-dark.svg">
  <img src="assets/architecture/architecture-light.svg" alt="harness2-jev-connector の構成図。harness2-intake が entry point の jev-mcp でコネクターを読み込み、コネクターは jev-mcp から PBI を claim して IntakeAdapter に渡す。IntakeAdapter はロールアウトごとに専用の git worktree で動かす。コネクターは選ばれた実行を jev-mcp で検証し、触らない範囲の確認を通したあと diff をソースのリポジトリに当てて、結果を返す。jev-mcp は docs/pbi から PBI を読み、報告を書く。">
</picture>

図のソースは `assets/architecture/architecture.d2` です。arch-diagram スキルの `render.py` で描き直します。

## 必要なもの

- POSIX: Linux または macOS。Windows では WSL を使います。
- Python 3.12〜3.13。
- harness2。下のインストールで依存として入ります。
- jev-mcp: pbi-orchestration-jev スキルに入っている `jev_mcp.py` サーバー。

## インストール

```sh
pip install "harness2-jev-connector @ git+https://github.com/reeschan/harness2-jev-connector.git"
```

harness2 は依存として git から入ります。入るのは、このパッケージがテストに使ったコミットです（`pyproject.toml` を参照）。

## 使い方

jev-mcp を stdio で起動します。

```sh
python <path-to>/jev_mcp.py --root <repo> --root <work-root>
```

HTTP で起動することもできます。

```sh
python <path-to>/jev_mcp.py --http --port 7830 --root <repo> --root <work-root>
```

HTTP のエンドポイントに対して harness2-intake を実行します。

```sh
harness2-intake --connector jev-mcp \
  --connector-opt url=http://127.0.0.1:7830/mcp \
  --repo <repo> --work-root <work-root>
```

harness2-intake に jev-mcp を stdio で起動させることもできます。

```sh
harness2-intake --connector jev-mcp \
  --connector-opt command="python <path-to>/jev_mcp.py --root <repo> --root <work-root>" \
  --repo <repo> --work-root <work-root>
```

## コネクターのオプション

| オプション | 既定値 | 意味 |
| --- | --- | --- |
| `url` | なし | jev-mcp の HTTP エンドポイントの URL。例は `http://127.0.0.1:7830/mcp`。`command` の代わりに使います。 |
| `command` | なし | jev-mcp を stdio で起動するコマンド。文字列かリストで書きます。文字列は `shlex.split` で分けます。 |
| `timeout` | 60 | MCP 呼び出し 1 回の待ち時間（秒）。`--connector-opt` からは文字列で来るので、float に直します。 |
| `scrub_env` | `TYPESAFE_API_KEY` | エージェントの環境から消す環境変数の名前をカンマ区切りで。秘密を持つ変数を指定します。例は `EXAMPLE_SECRET,ANOTHER_SECRET`。値を渡すと既定値を置き換えます。空の値を渡すと何も消しません。 |
| `pbi_dir` | `docs/pbi` | PBI のファイルを置くリポジトリ内のディレクトリ。コネクターはこれを diff から外します。 |
| `runner` | `harness2` | claim のときに記録するランナーの名前。 |

harness2-intake は `repo`、`mode`、`locate`、`timeout`（自身の `--timeout`、既定は 3600 秒）を渡します。
同じ名前の `--connector-opt` があればそちらが勝ちます。`connector_from_options` は、呼び出し元が
`timeout` を渡さないときに 60 秒を使います。

## `--root` の決まり

jev-mcp の `verify` は、作業ディレクトリが `--root` の下にあることを求めます。ロールアウトの worktree は
`--work-root` の下にできるので、`--root` には `--repo` と `--work-root` の両方を入れます。`--work-root` は
`--repo` の作業ツリーの外に置きます。harness2-intake がこれを確かめます。

## コネクターが用意するもの

- `workspace_policy`: `docs/pbi` を diff から外します。
- `scrub_env`: 指定した変数をエージェントの環境から消します。既定では、jev が API キーを読む変数
  `TYPESAFE_API_KEY` を消します。この変数が要るのは jev-mcp だけです。
- `judge()`: 実行に対して `verify` を回し、`n_passed`、`n_criteria`、`all_pass` を返します。選定には使いません。
- `report(task_id)`: jev-mcp がそのタスクについて書いた 5 行の報告。
- `close()`: MCP クライアントを閉じます。
- claim の途中で通信が切れたら、claim 済みのタスクを `fail_task` で返してから止まります。

## 開発

WSL で実行します。

```sh
uv run --python 3.12 python -m unittest discover -s tests
uvx ruff@0.17.1 check .
uvx ruff@0.17.1 format --check .
```

## ライセンス

Apache-2.0。一部のコードは harness2（Apache-2.0、Copyright 2026 Google LLC）から移したもので、その旨が
各ファイルの先頭にあります。
