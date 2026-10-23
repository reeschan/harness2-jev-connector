[English](README.md) | 日本語

# harness2-jev-connector

## 概要

harness2-jev-connector は、[harness2](https://github.com/reeschan/harness2) の `harness2-intake` のための
タスクコネクターです。jev-mcp に積まれた PBI を claim します。harness2 はロールアウトごとに git worktree
を作り、そこで PBI を実行します。コネクターは選ばれた実行の diff だけを取り、ソースの触らない範囲の確認
（`docs/pbi/tools/unchanged.py`）を通したあと、ソースのリポジトリに当てます。そして検証の結果と詳細を
`submit_result` で返します。

タスクのコネクターに加えて、モデルのコネクター `jev` も用意しています。harness2-intake は
`--model-connector jev` で読み込み、候補のモデル・柵の申告・試行の記録を、タスクのコネクターと
同じ jev-mcp から受け取ります。

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/architecture/architecture-dark.svg">
  <img src="assets/architecture/architecture-light.svg" alt="harness2-jev-connector の構成図。harness2-intake が entry point の jev-mcp でコネクターを読み込み、コネクターは jev-mcp から PBI を claim して IntakeAdapter に渡す。IntakeAdapter はロールアウトごとに専用の git worktree で動かす。コネクターは選ばれた実行を jev-mcp で検証し、触らない範囲の確認を通したあと diff をソースのリポジトリに当てて、結果を返す。jev-mcp は docs/pbi から PBI を読み、報告を書く。モデルのコネクター (エントリーポイント jev) は models.json の名前付きのテンプレートを読み、harness2-intake の実行役のモデル (opencode) と改善役のセッションを受け持つ。jev-mcp に、振り分け (route)、Jev の実行先 (claude か opencode、読む・書くだけの設定) での 1 回の実行 (run)、試行ごとの記録 (record) を頼む。">
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

## モデルのコネクター

モデルのコネクター `jev` は、候補のモデル・柵の申告・試行の記録を、タスクのコネクターと同じ jev-mcp
から受け取ります。オプションは `--model-connector-opt` で渡します。

```sh
harness2-intake --model-connector jev \
  --model-connector-opt url=http://127.0.0.1:7830/mcp \
  --model-connector-opt repo=<repo> \
  --repo <repo> --work-root <work-root>
```

| オプション | 既定値 | 意味 |
| --- | --- | --- |
| `url` | なし | jev-mcp の HTTP エンドポイントの URL。例は `http://127.0.0.1:7830/mcp`。`command` の代わりに使います。 |
| `command` | なし | jev-mcp を stdio で起動するコマンド。文字列かリストで書きます。文字列は `shlex.split` で分けます。 |
| `repo` | なし | リポジトリのパス。必須です。jev-mcp はこのリポジトリについて答えます。 |
| `config` | 下記 | テンプレートのファイルのパス。値が無ければ既定の場所を使います。 |
| `template` | なし | この実行だけで使うテンプレートの名前。ファイルの `use` の代わりに使います。 |
| `timeout` | 60 | MCP 呼び出し 1 回の待ち時間（秒）。`--model-connector-opt` からは文字列で来るので、float に直します。 |
| `isolate_opencode` | `true` | `command` のとき、opencode の設定の変数を空の一時ディレクトリに向けます。opencode の柵は、この分離が効いているときだけ申告します。 |

テンプレートのファイルは、使うテンプレートと役割ごとの手順を持ちます。`HARNESS2_JEV_MODELS` が
あればそのファイル、無ければ `$XDG_CONFIG_HOME/harness2-jev-connector/models.json`
（`XDG_CONFIG_HOME` が無ければ `~/.config/harness2-jev-connector/models.json`）を読みます。`config=`
で別のファイルを指せます。

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

`use` は、名前を指定されなかったときに使うテンプレートです。手順は 2 種類あります。実行先の手順は
`runner` と `model` を持ち、`effort` と `setting` は任意です。Jev に任せる手順は段（`jev`）を持ち、
`runners` と `setting` は任意です。`setting` を省いたときは、実行役は `default`、改善役は `edit-only`
になります。改善役の手順に `default` は書けません（その設定は柵を申告しないため）。`runners` を渡すと、
Jev の返す手順のうちその実行先だけを残します。省くと絞りません。コネクターは Jev に任せる手順を
jev-mcp の `route` に渡し、返った手順を候補にします。改善役では、Jev の `agent`（pbi-worker）の手順を、
同じモデルの `claude`（headless で起動）に読み替えます。テンプレートに無い役割は、その役割の既定
（実行役は `{"jev": "haiku"}`、改善役は `{"jev": "sonnet", "setting": "edit-only"}`）を使います。
ファイルが無いときは、両方の既定を使います。Jev の既定の振り分けなら、既定の改善役は `claude` の
`edit-only` になります。

`examples/models.json` に 2 つのテンプレートがあります。`claude-only` は既定（`use`）で、実行役は
Jev の haiku の段を opencode の中から選び、改善役は `claude` を `edit-only` で回します。`cheap` は
実行役が同じで、改善役は `claude` の `edit-only` のあとに、Jev の haiku の段を opencode の中から
`edit-only` で試します。テンプレートを 1 回だけ変えるときは、`--model-connector-opt template=cheap`
のように `template=` を渡します。

改善役のセッションは、jev-mcp の `run` で、候補ごとに 1 回、`edit-only` の設定で回します。`edit-only`
は読む・書くだけで、コマンドの道具を持ちません。作業ディレクトリは harness2 の `--session-root`
（既定は `<work-root>/_sessions`）の下にでき、答えはその作業ディレクトリの `answer.md` に書きます。
答えは `answer.md` だけで、`.last.md` は書きません。`--session-root` は jev-mcp の `--root` の下に
向けます。`run` は `--root` の外の作業ディレクトリを受け付けません。

改善役の候補は、柵を申告できる実行先のものだけを残します。コネクターは `claude` と `opencode` の
`edit-only` を、harness2 の申告（読む・書く・探すだけ、作業ディレクトリだけ、確認なし、harness2 の
資格情報なし）に訳します。HTTP の jev-mcp では、起動した環境がそのまま実行先に渡るので、
harness2 の資格情報を含めない環境で起動します。`claude` の申告はこれを前提にします。

opencode は利用者全体の設定とその MCP サーバーも読むので、その申告は、stdio で jev-mcp を起動して
分離が効いているときだけ出します。分離では、子の `XDG_CONFIG_HOME` と `OPENCODE_CONFIG_DIR` を空の
一時ディレクトリにし、`OPENCODE_CONFIG_CONTENT` と `OPENCODE_PERMISSION` を子の環境から外し、Jev の
home は `PBI_HOME` で元の場所に固定し、harness2 の資格情報の変数を子の環境から外します。HTTP の
jev-mcp は起動の環境をコネクターが決められないので、opencode は申告しません。HTTP の jev-mcp を
分離した環境で起動しても、申告は出ません。分離すると、利用者全体の opencode の設定（プロバイダーの
定義を含む）が読まれなくなるので、opencode の認証とプロバイダーは `XDG_DATA_HOME` 側か環境変数で
用意します。

opencode は `~/.opencode` やプロジェクトの `opencode.json`・`.opencode/` も読むことがあり、分離は
それらを塞いでいません。テンプレートに opencode を改善役として並べるなら、それらを置かない環境で
使います。

試行は、jev-mcp の `record` で Jev の `runs.jsonl` に 1 行ずつ書きます。成功は `合格`、使えないときは
`使えず`、それ以外は `差し戻し` です。

既知の制限:

- `record` のトークンと費用は、Jev がリポジトリの `docs/pbi/<実行先>/` のログから読みます。改善役の
  セッションは自分の作業ディレクトリにログを書くので、これらの値は空か古い値になることがあります。
- 記録の番号がぶつかったとき、コネクターは番号を進めて `record` を 1 回だけ呼び直します。その番号も
  ぶつかると、その試行は記録されません。
- `run` に渡す報告名は、最後に `routes` を呼んだときのものです。タスクを順に回す harness2-intake の
  使い方が前提で、タスクを並列に回す呼び出し元では使わないでください。

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
- モデルのコネクター（`jev`、entry point の `jev`）: テンプレートから役割ごとの候補を作り、改善役の
  実行先の柵を申告し、jev-mcp の `run` で改善役のセッションを回し、`record` で試行を記録します。

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
