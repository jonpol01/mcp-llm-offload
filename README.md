<p align="right"><b>日本語</b> · <a href="README.en.md">English</a></p>

# mcp-llm-offload

> Claude（や任意の MCP クライアント）の**軽量な LLM 作業**を、自分で管理するモデル — **ローカル** LLM（LM Studio・Ollama・llama.cpp）や **OpenAI 互換の任意プロバイダ**（OpenRouter・xAI Grok・OpenAI・Groq・Together など）— にオフロードする MCP サーバーです。安価で重要度の低い処理に、フロンティアモデルのクォータを浪費せずに済みます。

[![CI](https://github.com/seaosinc/mcp-llm-offload/actions/workflows/ci.yml/badge.svg)](https://github.com/seaosinc/mcp-llm-offload/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-8A2BE2.svg)](https://modelcontextprotocol.io)
[![Code style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#コントリビュート)

<p align="center">
  <img src="assets/flow.svg" alt="イベントが小さなローカル LLM のワーカーを起動し、メモリストアやツール（n8n・http）を使って Slack・Linear・GitHub・Discord に投稿する。Claude はループに含まれない" width="680">
</p>

フロンティアモデルは強力ですが、エージェントの日常作業の多くは*軽量*です。ログの要約、チケットの分類、テキストからのフィールド抽出、一文の言い換え——こうした処理にフロンティアモデルの料金（とクォータ）を払うのは無駄です。`mcp-llm-offload` は、これらのタスクを**あなたが選んだ**バックエンドへ転送する MCP ツールを少数だけ公開します。バックエンドは環境変数で切り替えられ、**呼び出しごと**に上書きすることも可能です。

この README には、始めるのに必要なことだけを置いています。詳しい説明はすべて [wiki](https://github.com/seaosinc/mcp-llm-offload/wiki) にあります。

## オフロードするもの、Claude に残すもの

| 作業 | 行き先 |
|---|---|
| ログ、diff、長いスレッドの要約 | **オフロード先** — `summarize(path=…)` なら中身がコンテキストに入らない |
| 分類、抽出、翻訳、言い換え | **オフロード先** |
| コミットメッセージ、PR の説明、変更ログ、擬似データ | **オフロード先** |
| PR / Issue のトリアージ、レビュースレッドの読み込み | **Hermes ボット** — `delegate` 経由 |
| 返信の下書き、コメントの投稿、Issue のラベル付け・クローズ | **Hermes ボット** — `delegate` 経由 |
| 人間が承認した PR の作成 | **Hermes ボット** — `delegate` 経由\* |
| コードの作成・変更 | **Claude** |
| diff の本格的なバグレビュー、レビュアーの指摘が正しいかの判断 | **Claude** |
| アーキテクチャ、セキュリティ、API 設計 | **Claude** |
| テスト・ビルド・リンターの実行、作業ツリーの編集 | **Claude** |
| `git push` / `commit` / `clone`、1 行で済む `gh` コマンド | **Claude** |
| PR を開く前の承認 | **あなた** |
| 自分のものではないプロジェクトへの公開返信 | **あなた** — ボットはあなたのアカウントで投稿します |

**\*** PR の本文がすでに手元のマシンにあるなら、自分で開いてください。ボットはあなたのファイルを読めないため、委譲すると本文をまるごとタスクに貼ることになり、コマンドを実行するより高くつきます。

どの行も判断基準は 1 つです。**ローカル実行、またはコードの判断が必要か？** 必要なら Claude に残します。「オフロード先」がどこに解決されるか、フォールバックが効く範囲、ワークフロー単位の判断表は wiki の [Tiering](https://github.com/seaosinc/mcp-llm-offload/wiki/Tiering) にあります。

## インストール

### Claude Code プラグイン（推奨）

```bash
/plugin marketplace add seaosinc/mcp-llm-offload
/plugin install mcp-llm-offload@mcp-llm-offload
```

Claude Code がプロバイダー、モデル、（稼働させている場合は）Hermes ボットの URL・キー・名前を尋ねます。機密としてマークされた値はキーチェーンに保存されます。後から変更するには `/plugin configure mcp-llm-offload@mcp-llm-offload` を実行します。

知っておくこと:

- **ツール名が名前空間付きになります。** `mcp__plugin_mcp-llm-offload_offload__*` および `mcp__plugin_mcp-llm-offload_agent__*` です。ツール名を明示的に書いているもの（サブエージェントの `tools:`、`CLAUDE.md` の振り分け規則、フック）はこの形に直さないと、何も呼ばないまま静かに失敗します。実際の名前は `/mcp` で確認できます。
- **サブエージェントは同梱されません。** 同梱の `llm-offloader` エージェントは名前空間なしのツール名を前提とするため、必要なら [`agents/`](agents/) から手で入れます。手順は [Installation](https://github.com/seaosinc/mcp-llm-offload/wiki/Installation)。
- `uv` が `PATH` 上に必要で、通信先のバックエンド（起動中の LM Studio などのローカルサーバー、またはホスト型プロバイダの API キー）も必要です。

### 手動（`claude mcp add`）

```bash
git clone https://github.com/seaosinc/mcp-llm-offload.git
claude mcp add offload \
  -e LLM_PROVIDER=lmstudio \
  -e LLM_MODEL=gemma-4-e2b-it \
  -- uv run /absolute/path/to/mcp-llm-offload/llm_offload_mcp.py
```

ここで指定したサーバー名がツールの接頭辞（`mcp__offload__ask` …）になります。同梱サブエージェントは名前 **`offload`** を前提とします。OpenRouter / Grok の例、JSON 形式の設定（`.mcp.json`・Claude Desktop）、`uv` なしでの起動は [Installation](https://github.com/seaosinc/mcp-llm-offload/wiki/Installation) にあります。

### 動作確認

Claude Code で `health` ツールを実行（または Claude に頼む）してください。解決されたプロバイダ・ベース URL・バックエンドが報告するモデル一覧が表示されます。

## ツール

| ツール | シグネチャ | 用途 |
|--------|-----------|------|
| `ask` | `ask(prompt, system?, path?, provider?, model?, temperature?, max_tokens?)` | 自由形式の軽量生成。`path` でファイルを文脈として渡せる。 |
| `summarize` | `summarize(text?, max_words?, style?, path?, provider?, model?)` | `text` またはファイル/glob（`path`）の忠実な要約。 |
| `classify` | `classify(labels[], text?, path?, provider?, model?)` | `text` またはファイルの単一ラベル分類。`labels` のいずれかを返す。 |
| `extract` | `extract(instructions, text?, path?, schema?, provider?, model?)` | `text`/ファイルからの構造化抽出 → きれいな JSON。任意の `schema`、不正な JSON は 1 回ローカル修復。 |
| `translate` | `translate(target, text?, path?, style?, provider?, model?)` | `text` またはファイル/glob を `target` 言語へ翻訳（書式を保持）。 |
| `rewrite` | `rewrite(text?, tone?, path?, provider?, model?)` | 文章の推敲・簡潔化（PR 説明・コミット本文・ドキュメント）。 |
| `commit_message` | `commit_message(text?, path?, style?, provider?, model?)` | diff（`text` または diff ファイルの `path`）から Conventional Commits メッセージを生成。 |
| `mock_data` | `mock_data(spec, count?, fmt?, provider?, model?)` | 仕様から擬似データ（JSON/CSV/SQL/NDJSON）を生成（小さな入力 → 大きな出力）。 |
| `pr_description` | `pr_description(text?, path?, intent?, provider?, model?)` | diff から PR 説明を生成（事実の記述のみ、正しさは主張しない）。 |
| `changelog` | `changelog(text?, path?, style?, version?, provider?, model?)` | git log を Added/Changed/Fixed のリリースノートにまとめる。 |
| `map` | `map(op, path, …op 引数)` | glob の**各**ファイルに 1 つの op を実行 → `{file: result}`。N 回でなく 1 回の呼び出し。 |
| `health` | `health(provider?)` | 到達性チェックとバックエンドのモデル一覧。 |

生成系ツールはいずれも `provider` と `model` を受け取り、その 1 回の呼び出しに限り既定を上書きできます。`summarize`・`classify`・`extract` は `path`（ファイルパスや glob）を受け取り、サーバーが自分で読み込むため、呼び出し側はパスだけを送ります。オフロードが実際にトークンを節約するのはこの形——**大きな入力を `path` で渡す**か、**小さなプロンプトから大きな出力を得る**ときです。ツール別の実測値は [Token-savings](https://github.com/seaosinc/mcp-llm-offload/wiki/Token-savings)。

## 設定

設定はすべて環境変数で行います。既定（ローカル LM Studio）で問題なければ、必須の変数はありません。

| 変数 | 説明 | 既定値 |
|------|------|--------|
| `LLM_PROVIDER` | 既定のプロバイダ名。`lmstudio` `ollama` `llamacpp` `openrouter` `grok` `openai` `groq` `together` `deepinfra` `mistral`、または `<NAME>_BASE_URL` を設定した任意の名前。 | *(下の解決順)* |
| `LLM_MODEL` | 既定のモデル ID（プロバイダの呼称どおり）。 | *(未設定)* |
| `<PROVIDER>_BASE_URL` / `<PROVIDER>_API_KEY` | プロバイダごとのエンドポイントとキー（例: `LMSTUDIO_BASE_URL`、`OPENROUTER_API_KEY`）。 | プリセット / 慣例の環境変数 |
| `HERMES_BASE_URL` / `HERMES_API_KEY` / `HERMES_BOT` | Hermes ボットのゲートウェイ（`/v1` で終わる）、そのプロファイルの `API_SERVER_KEY`、ボット（プロファイル）名。 | *(未設定)* |
| `LLM_FALLBACK_PROVIDER` | 1 つ目が到達不能・タイムアウト・過負荷・クォータ切れのときに使う 2 つ目のバックエンド。 | *(なし)* |
| `OFFLOAD_ROUTING` | `single`、または軽い処理と重い処理を別のバックエンドへ送る `spread`。 | `single` |

`provider` を指定しない呼び出しは **`LLM_PROVIDER` → `hermes`（`HERMES_BASE_URL` があれば）→ `lmstudio`** の順で解決されます。`health` が解決結果とその理由を報告します。全変数、`spread` の振り分け、フォールバックが効く範囲は [Configuration](https://github.com/seaosinc/mcp-llm-offload/wiki/Configuration)、コピペ用のひな形は [`.env.example`](.env.example) を参照してください。

## Hermes ボットへのタスク委譲（agent_mcp.py）

上記のツールは*生成*をオフロードします。`agent_mcp.py` は別の任意のサーバーで、*作業*をオフロードします。タスク全体を、独自のシェル、ファイルシステム、`gh` CLI を持つ [Hermes](https://github.com/NousResearch/hermes-agent) ボットに渡し、ボットが報告した内容だけが返ります。diff、CI ログ、Issue スレッドはあなたのコンテキストに入りません。

**このサーバーは読み取り専用ではありません。** ボットは自身の認証情報でコミット・push・コメントができます。このサーバーは意図的に独自の安全策を追加しません — ボット自身の `SOUL.md` と `approvals.deny` が下限です。

```bash
claude mcp add agent \
  -e HERMES_BASE_URL=http://192.168.1.50:8649/v1 \
  -e HERMES_API_KEY=... \
  -e HERMES_BOT=offload \
  -- uv run /absolute/path/to/agent_mcp.py
```

`HERMES_BASE_URL` はボットのゲートウェイ（`/v1` で終わる）、`HERMES_API_KEY` はそのプロファイルの `API_SERVER_KEY`、`HERMES_BOT` はプロファイル名です。

| Tool | |
|---|---|
| `delegate` | タスクをボットに渡し、その報告を返します。オプションの `bot`、`path`、`system`。 |
| `bots` | このエンドポイントが提供するボット名を一覧します。 |
| `health` | キーを表示せずに、エンドポイントとその設定を確認します。 |

ボット名は実行前にエンドポイントと照合されます。Hermes は未知のモデル名を拒否せず自身のプロファイルで応答するため、チェックしないタイプミスは静かに別のエージェントへタスクを渡してしまうからです。ボットは `ask(provider="hermes")` のように offload ツールのプロバイダとしても使えます。

### ボットにルールを与える

プラグインがボットに伝えるのは、タスクごとの「やること」だけです。「やってはいけないこと」はボット側の `SOUL.md` と `approvals` にあり、新規のプロファイルにはどちらもありません。`gh` にログイン済みのユーザーで動く新規プロファイルは、PR のマージも `main` への push も確認なしで実行します。[`hermes/`](hermes/) はその穴を塞ぐキットです。ボットのマシンで、ボットを動かしているユーザーとして:

```bash
hermes/setup-bot.sh offload --port 8650    # 専用プロファイルを作り、SOUL と deny フロアを入れ、46 コマンドで検査
hermes/setup-bot.sh offload --check        # 検査のみ。何も変更しません
```

新しいプロファイルはあなたの Hermes プロファイルの複製（`hermes profile create --clone`）なので、LLM と認証情報はそのまま使われ、キットがモデルを選ぶことはありません。**GitHub へのアクセスだけは、キットでは設定しません。** ボットのシステムユーザーで一度 `gh auth login` をしてください（Hermes はボットのコマンドから `GH_TOKEN` を取り除くため、`.env` のトークンは効きません）。フロアの中身、gh ログイン、ブランチ保護、`unattended_mode` の扱いは [Hermes-bot](https://github.com/seaosinc/mcp-llm-offload/wiki/Hermes-bot) にあります。

## ドラフトテキストの送信（post_mcp.py）

offload ツールはテキストを返すだけです。届けたいものが呼び出し元のモデルを経由して戻るなら、それは本プロジェクトが避けようとしているコストそのものです。`post_mcp.py` は任意のコンパニオンで、Discord・Slack・Telegram・Linear・GitHub のコメント・汎用 webhook へ送ります。宛先とシークレットは環境からのみ読み、`dry_run` で送らずに確認できます。`examples/ninja.py` は Claude を一切介さないループの例です。詳細は [post_mcp](https://github.com/seaosinc/mcp-llm-offload/wiki/post_mcp)。

## ドキュメント

| wiki | 内容 |
|---|---|
| [Overview](https://github.com/seaosinc/mcp-llm-offload/wiki/Overview) | 機能一覧と仕組み |
| [Installation](https://github.com/seaosinc/mcp-llm-offload/wiki/Installation) | プラグインの注意点、手動登録の全例、サブエージェント |
| [Providers](https://github.com/seaosinc/mcp-llm-offload/wiki/Providers) | 対応プロバイダの表と推奨ローカルモデル |
| [Configuration](https://github.com/seaosinc/mcp-llm-offload/wiki/Configuration) | 全環境変数、プロバイダの解決順、`spread`、フォールバック |
| [Token-savings](https://github.com/seaosinc/mcp-llm-offload/wiki/Token-savings) | ファイル入力と、ツール別の実測削減率 |
| [Tiering](https://github.com/seaosinc/mcp-llm-offload/wiki/Tiering) | ローカル → Sonnet → フロンティアの 3 層と、ワークフロー単位の判断表 |
| [Hermes-bot](https://github.com/seaosinc/mcp-llm-offload/wiki/Hermes-bot) | ボットをバックエンドとして動かす手順、SOUL と deny フロア、gh ログイン |
| [post_mcp](https://github.com/seaosinc/mcp-llm-offload/wiki/post_mcp) | 宛先の設定とツール |
| [Troubleshooting](https://github.com/seaosinc/mcp-llm-offload/wiki/Troubleshooting) | 症状と対処 |
| [Development](https://github.com/seaosinc/mcp-llm-offload/wiki/Development) | lint・スモークテスト・CI、リポジトリ内の `.mcp.json` について |

## 開発

```bash
uvx ruff@0.15.0 check .   # lint
uv run --with 'mcp<2' --with httpx python -c \
  "import importlib.util as u; s=u.spec_from_file_location('m','llm_offload_mcp.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print('ok', m.mcp.name)"
```

CI（GitHub Actions）は、push と PR のたびに同じ lint とインポートのスモークテストを実行します。

## コントリビュート

Issue・PR を歓迎します。サーバーは単一ファイル・プロバイダ中立を保ってください。新しいプロバイダは通常 `PROVIDERS` レジストリに 1 行追加するだけです。

## ライセンス

[MIT](LICENSE) © Seaos Inc
