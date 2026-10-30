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
- jev-mcp: [Atelier](https://github.com/reeschan/atelier) スキルに入っている `jev_mcp.py` サーバー (`skills/atelier/jev_mcp.py`。Atelier の旧名は pbi-orchestration-jev、Jev)。

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

モデルのコネクター `jev` は、候補のモデル・柵の申告・試行の記録を、タスクのコネクターと同じ
jev-mcp から受け取ります。オプションは `--model-connector-opt` で渡します。

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

### 部品のつながり

harness2-intake は、タスクを claim から submit まで進めるプログラムです。このパッケージは 2 回
読み込まれます。タスクのコネクター（`jev-mcp`）は jev-mcp から PBI を claim し、選ばれた実行の
diff を返します。モデルのコネクター（`jev`）は、モデルと改善役のセッションの供給元です。

harness2-intake の中では、次の部品が働きます。

- **実行役の候補選び。** ロールアウトの前に
  `routes("executor", task_id=..., substrate="opencode")` を 1 回呼び、opencode の候補を
  覚えます。実行先が opencode でない候補は、ここでは実行できないので、飛ばした行として
  残ります。opencode が今のモデルを使えないときは、元の worktree から、次の候補で 1 回だけ
  やり直します。
- **ロールアウト。** ロールアウトごとに `WorktreeLauncher` が `--work-root` の下に git worktree
  を作り、そこで opencode を回し、diff を `workspace/output/changes.diff` に書きます。
  `docs/pbi` の下のパスは diff から外し、`scrub_env` に並べた変数（既定は、jev が API キーを
  読む `TYPESAFE_API_KEY`）はエージェントの環境から外します。
- **改善役の connector チャネル。** モデルのコネクターに `run_session` があると、harness2 は
  改善役のセッションを、自分のチャネルの代わりにそれで回します。セッションの作業ディレクトリは
  `--session-root`（既定は `<work-root>/_sessions`）の下にできます。
- **柵の照合。** harness2 は `fences()` の申告を、改善役のセッションに許す上限（ファイルの道具
  だけ、作業ディレクトリの外に出ない、人に聞かない、harness2 の資格情報を持たない）と比べます。
  その上限を満たす申告の無い実行先の候補は飛ばし、1 つも無ければ起動しません。

このコネクターは、harness2-intake と jev-mcp の間に立って、harness2 の言葉と Jev の言葉を訳します。
jev-mcp は、順番待ちを持ち、Jev を動かすサーバーです。タスクの道具は `list_tasks`・
`claim_task`・`verify`・`submit_result`・`fail_task`、モデルの道具は `route`・`run`・`record`
です。このコネクターが使う Jev の実行先は `claude`（Claude Code、headless）と `opencode` です。

リポジトリと Jev の home は別の場所です。リポジトリの `docs/pbi` には、PBI のファイル・報告・
検証の記録があり、jev-mcp が読み書きします。Jev の home には `routing.json`・テンプレート・
`agents.json` があり、Atelier はそれを `ATELIER_HOME` (旧名の `PBI_HOME` も読む) で見つけ、
無ければ `$XDG_CONFIG_HOME/atelier` で見つけます。そこが無く旧名の
`$XDG_CONFIG_HOME/pbi-orchestration-jev` だけがあれば、旧名を使います。コネクターは `docs/pbi`
をどの diff からも外し、opencode を分離するときは `ATELIER_HOME` を固定して、`XDG_CONFIG_HOME`
を向け直しても Atelier の home が動かないようにします。

harness2 がモデルのコネクターをどう呼ぶか（`routes`・`run_session`・`fences`・`record` と、
それぞれが返すもの）は、harness2 の
[MODEL_CONNECTORS.md](https://github.com/reeschan/harness2/blob/main/docs/MODEL_CONNECTORS.md)
にあります。

### 1 件のタスクの順序

```mermaid
sequenceDiagram
    participant H as harness2-intake
    participant C as this connector
    participant M as jev-mcp
    participant R as Jev runner

    H->>C: claim (task connector)
    C->>M: list_tasks, claim_task
    M-->>C: PBI, report name, verify commands
    C-->>H: task
    H->>C: routes(executor)
    C->>M: route(tier, skip)
    M-->>C: runner, model, effort
    C-->>H: executor candidates
    H->>H: rollout with opencode in a git worktree
    H->>C: record(executor, outcome)
    C->>M: record
    H->>C: routes(improver)
    C->>M: route(tier, skip)
    M-->>C: steps
    C->>C: translate agent to claude
    C-->>H: improver candidates
    H->>C: fences()
    C-->>H: edit-only declaration per runner
    H->>C: run_session(choice)
    C->>M: run(edit-only, workdir under --session-root)
    M->>R: one session
    R-->>M: answer.md
    M-->>C: code and answer
    C-->>H: SessionResult
    H->>C: record(improver, outcome)
    C->>M: record
    H->>C: verify(chosen run)
    C->>M: verify(workdir)
    M-->>C: results
    C->>C: apply the diff after the unchanged-files check
    C->>M: submit_result
```

1. **claim。** harness2-intake のタスクのコネクターが、`list_tasks` で順番待ちの PBI を並べ、
   `claim_task` で 1 件ずつ claim します。claim は依頼文・PBI・差し戻し文・`worker.md`・
   検証コマンドを運びます。
2. **実行役の候補。** harness2 が `routes("executor", ...)` を呼びます。Jev に任せる手順では、
   コネクターが jev-mcp の `route` に段と `skip`（その手順が要らない実行先）を渡し、返った
   実行先・モデル・effort を候補にします。harness2 はそのうち opencode の候補を残します。
3. **ロールアウト。** harness2 が、今の実行役の候補で、`--work-root` の下の worktree で
   opencode を回します。worktree の diff が成果物で、`docs/pbi` は diff から外れます。
4. **実行役の記録。** harness2 がロールアウトの結果をコネクターの `record` に渡し、コネクターが
   jev-mcp の `record` を呼びます。Jev は `runs.jsonl` に 1 行書きます。成功は `合格`、モデルを
   使えなかったときは `使えず`、それ以外は `差し戻し` です。
5. **改善役の候補。** harness2 が `routes("improver", ...)` を呼びます。Jev に任せる手順では、
   また `route` を呼びます。改善役では、Jev の `agent`（Claude Code の pbi-worker）の手順を、
   同じモデルの `claude`（headless で起動）に読み替えます。候補は、実行先の柵を申告でき、
   設定が手順のものと一致するときだけ残ります。
6. **柵。** harness2 が `fences()` を読み、候補を申告に結び付けます。申告の無い実行先の候補は
   飛ばします（[柵の訳](#柵の訳)）。
7. **セッション。** harness2 が候補を 1 つ選び、`--session-root` の下の作業ディレクトリを付けて
   `run_session` を呼びます。試す候補ごとに 1 回です。コネクターは jev-mcp の `run` に、その
   ディレクトリ・報告名・実行先・モデル・`edit-only` の設定を渡します。jev-mcp はそこで実行先を
   起動します。セッションは読む・書くだけで、コマンドの道具を持ちません。答えは作業ディレクトリの
   `answer.md` に書き、`.last.md` は書きません。コネクターは始める前に古い `answer.md` を消し、
   今回のものを返します。
8. **改善役の記録。** harness2 が結果をまた `record` に渡します。使える答えが返れば `合格`、
   選択が使えなければ `使えず`、それ以外は `差し戻し` です。
9. **verify。** 選ばれたあと、コネクターが選ばれた実行の worktree で、PBI の検証コマンドを
   jev-mcp の `verify` で回します。結果の項目・通った数・全体の数を受け取ります。
10. **submit。** コネクターが、選ばれた実行の diff を、ソースの触らない範囲の確認
    （`docs/pbi/tools/unchanged.py`）のあとでソースのリポジトリに当て、状態・通った数・
    変わったファイル・未解決・詳細・artifacts を `submit_result` で返します。

既知の制限:

- `record` のトークンと費用は、Jev がリポジトリの `docs/pbi/<実行先>/` のログから読みます。
  改善役のセッションは自分の作業ディレクトリにログを書くので、これらの値は空か古い値に
  なることがあります。
- 記録の番号がぶつかったとき、コネクターは番号を進めて `record` を 1 回だけ呼び直します。
  その番号もぶつかると、その試行は記録されません。
- `run` に渡す報告名は、最後に `routes` を呼んだときのものです。タスクを順に回す harness2-intake
  の使い方が前提で、タスクを並列に回す呼び出し元では使わないでください。

### 準備

入れるもの:

- harness2。このパッケージのインストールで依存として入ります。
- このパッケージ（`harness2-jev-connector`）。
- jev-mcp。Atelier スキルに入っている `jev_mcp.py` サーバー (`skills/atelier/jev_mcp.py`)。
- Claude Code。`claude` の実行先として使います。
- opencode。実行役と `opencode` の実行先として使います。

stdio で起動すると、モデルのコネクターは、自分が起動する jev-mcp を分離できます。子の環境から
harness2 の資格情報を外し、opencode の設定の変数を空のディレクトリに向けます。これが効いている
ときだけ、opencode の柵を申告できます。同じ環境で、`ATELIER_HOME` により Atelier の home も元の場所に
残ります。タスクのコネクターにも同じ root を渡します。

```sh
harness2-intake --connector jev-mcp \
  --connector-opt command="python <path-to>/jev_mcp.py --root <repo> --root <work-root>" \
  --model-connector jev \
  --model-connector-opt command="python <path-to>/jev_mcp.py --root <repo> --root <work-root>" \
  --model-connector-opt repo=<repo> \
  --repo <repo> --work-root <work-root> --session-root <work-root>/_sessions
```

HTTP では、jev-mcp は別のプロセスで、その環境をコネクターは選べません。harness2 の資格情報を
含めない環境で自分で起動し、両方のコネクターに同じエンドポイントを渡します。

```sh
harness2-intake --connector jev-mcp \
  --connector-opt url=http://127.0.0.1:7830/mcp \
  --model-connector jev \
  --model-connector-opt url=http://127.0.0.1:7830/mcp \
  --model-connector-opt repo=<repo> \
  --repo <repo> --work-root <work-root> --session-root <work-root>/_sessions
```

HTTP では opencode を申告できません（[opencode の分離](#opencode-の分離)）。

`--repo` と `--work-root` の両方を jev-mcp の `--root` に渡し、`--session-root` はそのどれかの
root の下に置きます。既定のセッションの root は `<work-root>/_sessions` で、`--work-root` の下
です。`run` はどの `--root` の下でもない作業ディレクトリを受け付けません。残りは下の
`--root` の決まりにあります。

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

### 柵の訳

Jev の設定は、そのまま harness2 の柵ではありません。コネクターが訳します。訳はこの表で全部です。

| Jev の実行先と設定 | `capabilities` | `workdir_only` | `asks_human` | `harness_credentials` |
| --- | --- | --- | --- | --- |
| `claude`、`edit-only` | `read`・`write`・`search` | true | false | false |
| `opencode`、`edit-only`（stdio で分離が効くとき） | `read`・`write`・`search` | true | false | false |

`edit-only` は、セッションにファイルの道具だけを与えます。読む・書くだけで、コマンドの道具を
持ちません。harness2 の言葉では、読む・書く・探すだけで、作業ディレクトリの中でだけ動き、人に
聞かず、harness2 の資格情報を持ちません。コネクターは `claude` と `opencode` に同じ柵を申告し、
違うのは実行先の名前だけです。

`default` は申告しません。その設定はコマンドの道具を持つので、harness2 の改善役の上限に収まり
ません。実行先と設定の組み合わせがこの表に無い候補は、改善役の候補から外れます。テンプレートの
改善役の手順に `default` を書くと、ファイルを読むときに断られます。

`claude` の申告は、jev-mcp が harness2 の資格情報を持たない環境で動くことを前提にします。stdio
では、子の環境から資格情報を外します。HTTP の jev-mcp は、起動した環境がそのまま実行先に渡る
ので、harness2 の資格情報を含めない環境で起動します。`opencode` は、コネクターが stdio で
jev-mcp を起動して分離が効いているときだけ申告します。HTTP では、jev-mcp の環境をコネクターが
決められないので、申告しません。

### opencode の分離

opencode は利用者全体の設定とその MCP サーバーも読みます。改善役のセッションにそれらを入れない
ため、モデルのコネクターは、stdio で jev-mcp を起動するときに子を分離します。
`isolate_opencode`（既定は `true`）と `command=` のとき、子の環境は次のように変わります。

- `XDG_CONFIG_HOME` と `OPENCODE_CONFIG_DIR` を空の一時ディレクトリに向けます。コネクターが
  それを作り、`close` で消します。
- `OPENCODE_CONFIG_CONTENT` と `OPENCODE_PERMISSION` を子の環境から外します。
- `ATELIER_HOME` を、利用者の環境が指す Atelier の home に固定します。`$XDG_CONFIG_HOME/atelier`
  (無ければ `~/.config/atelier`) で、そこが無く旧名の `pbi-orchestration-jev` だけがあれば旧名です。
  `ATELIER_HOME`・`PBI_HOME`・`PBI_TEMPLATES_HOME` のどれかが設定されていれば、何も固定しません。
- harness2 の資格情報を外します。名前が `_KEY`・`_TOKEN`・`_SECRET` で終わる `HARNESS2_*` の
  変数と、`GOOGLE_APPLICATION_CREDENTIALS`・`GOOGLE_CLOUD_PROJECT`・
  `HARNESS2_VERTEX_PROJECT` です。

opencode の認証とプロバイダーの定義は、ふつう `XDG_DATA_HOME` の下にあります。そこはそのまま
残るので、認証とプロバイダーは `XDG_DATA_HOME` 側か環境変数で用意します。利用者全体の設定の
ディレクトリは、もう読まれません。

分離は、opencode が設定を読む場所をすべて塞ぐわけではありません。opencode は `~/.opencode` や、
セッションのディレクトリから git worktree の根までの `opencode.json`・`.opencode/` も読むこと
があります。テンプレートに opencode を改善役として並べるなら、それらを置かない環境で使い、
`--session-root` はどの git worktree の外にも置きます。

HTTP では、コネクターは jev-mcp の環境を選べないので、opencode を申告しません。その jev-mcp を
分離した環境で起動しても、申告は出ません。

### 詰まったとき

**候補が 0 件。** jev-mcp の `route` が失敗すると、コネクターは
`route for <報告名> (tier <段>) gave no candidates: ...` と警告します。jev-mcp のログでその
エラーを見ます。PBI の `docs/pbi/<報告名>.facts.json` が無いときや、テンプレートの実行役の手順の
`runners` に `opencode` が無いときも、`route` は候補を返しません。harness2 は
`no executor model for substrate opencode` で止まります。

**harness2-intake が起動時に終了コード 2。** 終了コード 2 は引数のエラーで、その 1 つが、
`run_session` を持つのに、条件を満たす柵を申告する実行先が無いモデルのコネクターです。エラーの
行に、実行先ごとの問題が出ます。テンプレートの改善役の手順と設定を見ます。改善役の手順に使える
設定は `edit-only` だけで、opencode は stdio で分離が効いているときだけ申告されます。

**`run` が `--root` の外で断られる。** jev-mcp の `run` は、`--root` のどれかの下の作業
ディレクトリしか受け付けません。`--session-root` を `--root` の下に置きます。既定の
`<work-root>/_sessions` は `--work-root` の下なので、`--work-root` も `--root` に渡します。

**使えない失敗が続く。** `使えず` の結果では次の候補に進み、全部が使えないと
`no fenced improver model: ...` で試行が終わります。Jev の `runners.md` の「使えないとき」に、
実行先ごとに見る場所があります。

**HTTP で opencode が使われない。** HTTP の jev-mcp は環境をコネクターが選べないので、コネクターは
opencode の柵を申告しません。opencode を改善役に使うときは、stdio（`command=`）で起動します。

## Atelier の case の作り直し

Atelier の `export_cases.py` は、判定した PBI ごとに正規化した case を `docs/pbi/cases.jsonl` (と
保全先の `runs.jsonl` の隣) に書きます。タスクのコネクター `atelier-cases` はそれを作り直し、
差し戻された PBI で Atelier の指示を harness2 に磨かせます。case の要素は harness2 の構成要素に
1 つずつ当てます。

| case | harness2 |
| --- | --- |
| `harness.system_prompt` (worker.md) | 土台のハーネスの `systemprompt.md` (`base_harness()`) |
| `harness.guardrails` (効いていた CLAUDE.md) | `guardrails.md`。リポジトリから読み、版が変わっていれば注記し、always-on の上限を超えるものは外す |
| `harness.memory` (ref) | タスクの依頼文の Refs の節。要旨を添える |
| `task` (PBI の節) | タスクの依頼文 |
| `task.verify`・`task.acceptance`・`private.rework` | 非公開の基準。採点者だけが読む |

取り上げる case は、`base_commit` が `--repo` の HEAD で、差し戻されたもの (`include=all` なら
全部)、`depends_on` が無いもの (`dependent=true` なら有っても)、選んだ worker の版 (`worker`、
無ければいちばん新しい版) のものです。元のコミットの worktree で動かします。

```sh
git -C <repo> worktree add <replay-dir> <base_commit>
harness2-intake --connector atelier-cases \
  --connector-opt cases=<path>/cases.jsonl \
  --repo <replay-dir> --work-root <work-root>
```

ソースには何も戻しません。`deliver` は `<out>/<タスク id>.json` (状態、選ばれた実行の summary と
採用したハーネスの場所) と `<out>/<タスク id>.diff` を書きます。`out` の既定は最初の cases の
ファイルの隣の `replay/` です。採点者は選ばれた実行の worktree で `verify <n>` の基準を走らせ、
終了コード 0 の数を数えます。

| オプション | 既定値 | 意味 |
| --- | --- | --- |
| `cases` | 必須 | `cases.jsonl` のパス。カンマで複数 |
| `out` | 最初のファイルの隣の `replay/` | 結果の置き場所 |
| `include` | `sent-back` | `sent-back` か `all` |
| `worker` | いちばん新しい版 | 作り直す worker.md の版 (16 進 12 文字) |
| `dependent` | `false` | `depends_on` のある case も取る |

採用したハーネス (結果の `harness_dir`) を worker.md と CLAUDE.md と比べ、Atelier の何を直すかを
決めます。直すのは人で、プルリクエストで直します。

## 差し戻しの集計

`atelier-rework-report` は Atelier の `runs.jsonl` (`--archive` なら保全先のすべて) を読み、
一発合格率・差し戻し率・停止率、差し戻しの原因 (指示の側の `spec`・`convention`・`scope`・
`memory` と、`model`・`env`)、shared-memory の違反・Refs の読み漏れ・Do not touch の変更の
割合、それらの worker.md の版別と最初のモデル別の表、指示の側の原因の新しい行 (作り直す候補)
を出します。`--json` で同じものを JSON で出します。

時間とトークンも出します。判定した PBI (「使えず」以外の結果が 1 つでもあるもの) ごとに、
使えず・差し戻しも含めた全実行の秒・ステップ・トークンを足し、worker.md の版別・最初のモデル別・
最初の tier 別に中央値を並べます。差し戻しと使えずで使ったトークンの割合 (全体の割合には
「使えず」だけの PBI も入れます) と、入力のうちキャッシュから読んだ割合も出します。`runs.jsonl` の隣に
Atelier の `export_cases.py` が書いた `cases.jsonl` があれば、Targets の数・Refs の数・worker.md・
PBI・Refs の要旨の文字数でも分け、重い PBI を大きさと一緒に並べます。大きくて差し戻された PBI は
分ける候補、文章が長いのに一発合格した PBI は常に読ませる指示や Refs を削る候補です。

```sh
atelier-rework-report --archive
```

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
