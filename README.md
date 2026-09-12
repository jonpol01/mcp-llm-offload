<p align="right"><b>日本語</b> · <a href="README.en.md">English</a></p>

# mcp-llm-offload

> Claude（や任意の MCP クライアント）の**軽量な LLM 作業**を、自分で管理するモデル — **ローカル** LLM（LM Studio・Ollama・llama.cpp）や **OpenAI 互換の任意プロバイダ**（OpenRouter・xAI Grok・OpenAI・Groq・Together など）— にオフロードする MCP サーバーです。安価で重要度の低い処理に、フロンティアモデルのクォータを浪費せずに済みます。

[![CI](https://github.com/jonpol01/mcp-llm-offload/actions/workflows/ci.yml/badge.svg)](https://github.com/jonpol01/mcp-llm-offload/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-8A2BE2.svg)](https://modelcontextprotocol.io)
[![Code style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#コントリビュート)

<p align="center">
  <img src="assets/flow.svg" alt="イベントが小さなローカル LLM のワーカーを起動し、メモリストアやツール（n8n・http）を使って Slack・Linear・GitHub・Discord に投稿する。Claude はループに含まれない" width="680">
</p>

## なぜ

フロンティアモデルは強力ですが、エージェントの日常作業の多くは*軽量*です。ログの要約、チケットの分類、テキストからのフィールド抽出、一文の言い換え——こうした処理にフロンティアモデルの料金（とクォータ）を払うのは無駄です。

`mcp-llm-offload` は、これらのタスクを**あなたが選んだ**バックエンドへ転送する MCP ツールを少数だけ公開します。LM Studio・Ollama・llama.cpp・OpenRouter・Grok・OpenAI・Groq・Together はすべて同じ `/v1/chat/completions` API を話すため、この小さなサーバー 1 つですべてに対応できます。バックエンドは環境変数で切り替えられ、**呼び出しごと**に上書きすることも可能です。

## 機能

- 🔀 **プロバイダ非依存** — サーバーは 1 つ、相手は任意の OpenAI 互換エンドポイント。主要なものはプリセット済み、それ以外は自分で追加できます。
- 🏠 **ローカルファースト** — 既定はローカルの LM Studio。ローカルバックエンドなら API キー不要です。
- 🎯 **目的特化のツール** — `ask`・`summarize`・`classify`・`extract`・`health`。素のチャット中継ではなく、軽量タスク向けに整形されています。
- 🧭 **呼び出しごとのルーティング** — 各ツールは `provider` と `model` を任意で受け取ります。安価な処理はローカルへ、*少しだけ*難しい処理は再設定なしで Grok / OpenRouter へ回せます。
- 📂 **ファイル入力** — `summarize`/`classify`/`extract` は `path`（ファイルまたは glob）を受け取り、サーバーがローカルで読み込みます。呼び出し側はパスだけを送るため、*大きな*入力のオフロードで実際にトークンを節約できます。
- 🩺 **実用的なエラー** — 接続・タイムアウト・認証・モデル 404・レート制限の失敗は、スタックトレースではなく「次にこうすればよい」という平易な文字列で返ります。
- 📦 **単一ファイル・インストール不要** — [PEP 723](https://peps.python.org/pep-0723/) のインライン依存により `uv run llm_offload_mcp.py` だけで動きます。
- 🤖 **Claude Code サブエージェント同梱** — 軽量作業を自動で振り分ける `llm-offloader` エージェントを任意で利用できます。

## 推奨ローカルモデル

軽量なオフロード作業に大きなモデルは要りません。要約・分類・短い書き換えには `0.6b`〜`2b` クラスの指示チューニング済みモデルで十分です。おすすめの既定値:

| モデル | 使いどころ |
|--------|-----------|
| `gemma-4-e2b-it` | **第一候補。** 最速。分類・要約・短い質問に最適。 |
| `gemma-4-e4b-it` | 少し難しい言い換えや雑な入力に強く、それでも安価。 |

Apple Silicon では LM Studio の MLX ビルド（例: `gemma-4-e2b-it-mlx`）を推奨します。同クラスの Qwen・Llama・Phi 系でも同等に動作します。バックエンドが提供する ID を `LLM_MODEL` に設定してください。

## 対応プロバイダ

| プロバイダ   | 既定のエンドポイント                     | API キー環境変数      | モデル例 |
|--------------|------------------------------------------|-----------------------|----------|
| `lmstudio`   | `http://localhost:1234/v1`               | —（不要）             | `gemma-4-e2b-it` |
| `ollama`     | `http://localhost:11434/v1`              | —（不要）             | `llama3.1` |
| `llamacpp`   | `http://localhost:8080/v1`               | —（不要）             | *読み込み中のモデル* |
| `openrouter` | `https://openrouter.ai/api/v1`           | `OPENROUTER_API_KEY`  | `meta-llama/llama-3.3-70b-instruct` |
| `grok`       | `https://api.x.ai/v1`                     | `XAI_API_KEY`         | `grok-2-latest` |
| `openai`     | `https://api.openai.com/v1`              | `OPENAI_API_KEY`      | `gpt-4o-mini` |
| `groq`       | `https://api.groq.com/openai/v1`         | `GROQ_API_KEY`        | `llama-3.1-8b-instant` |
| `together`   | `https://api.together.xyz/v1`            | `TOGETHER_API_KEY`    | `meta-llama/Llama-3.3-70B-Instruct-Turbo` |
| `deepinfra`  | `https://api.deepinfra.com/v1/openai`    | `DEEPINFRA_API_KEY`   | *DeepInfra 参照* |
| `mistral`    | `https://api.mistral.ai/v1`              | `MISTRAL_API_KEY`     | `mistral-small-latest` |
| *その他すべて* | `<NAME>_BASE_URL` を設定                | `<NAME>_API_KEY`      | *— 任意の OpenAI 互換サービス* |

> カスタムプロバイダは好きな名前で使えます。`FOO_BASE_URL`（必要なら `FOO_API_KEY`）を設定し、ツールを `provider="foo"` で呼び出してください。

## 仕組み

```
Claude Code ──stdio──▶ mcp-llm-offload ──HTTP /v1/chat/completions──▶ バックエンド
 (フロンティア)          (このサーバー)                                (ローカル / Grok / OpenRouter …)
```

このサーバーは薄く行儀のよい MCP フロントエンドです。使用するバックエンドとモデルを解決し（呼び出し → 環境変数 → プリセットの順）、テンプレート互換性を最大化するためにシステム指示をユーザーターンに畳み込み、エンドポイントを呼び出して、きれいなテキスト（または `Error: …` 文字列）を返します。

上の図は、これによって実現できる全体像です。小さなローカルモデルが自律的な「忍者」として日常的な雑務を端から端まで処理し、そのために Claude が一切呼ばれない、という構図です。

## Claude Code プラグインとしてインストールする

プラグインはサーバーと、それらが必要とするプロンプトをまとめて同梱しているため、手作業で登録するものはありません。

```bash
/plugin marketplace add jonpol01/mcp-llm-offload
/plugin install mcp-llm-offload@mcp-llm-offload
```

その後、Claude Code が設定を尋ねます。対象はプロバイダー、モデル、そして（稼働させている場合は）Hermes ボットの URL、キー、名前です。機密としてマークされた値は `settings.json` ではなくキーチェーンに保存されます。後から変更するには、次を実行します。

```bash
/plugin configure mcp-llm-offload@mcp-llm-offload
```

この方法を選ぶ前に、次の 2 点を把握してください。

- **プラグインにはサブエージェントは同梱されていません。** Claude Code はプラグインの MCP サーバーに名前空間を付けるため、同梱の `llm-offloader` エージェント（フロントマターが名前空間なしの `mcp__offload__*` ツール名を固定しています）は、使えるツールがない状態で読み込まれてしまいます。そのため同梱せず、必要な場合はエージェントを手作業でインストールしてください（後述）。
- `uv` は引き続き `PATH` 上に必要であり、通信先のバックエンドも必要です。

## クイックスタート

### 1. 前提条件

- [`uv`](https://docs.astral.sh/uv/)（推奨）。または `pip` の使える Python 3.10+。
- バックエンド: 起動中のローカルサーバー（例: [LM Studio](https://lmstudio.ai/) → **Developer ▸ Start Server**）、**または**ホスト型プロバイダの API キー。

### 2. 取得

```bash
git clone https://github.com/jonpol01/mcp-llm-offload.git
cd mcp-llm-offload
```

起動を確認します（MCP を stdio で提供するため、クライアントを待って待機します。`Ctrl-C` で終了）:

```bash
uv run llm_offload_mcp.py
```

> `uv` がない場合は `pip install 'mcp<2' httpx` のあと `python llm_offload_mcp.py`。

### 3. Claude Code への登録

ここで**指定したサーバー名がツールの接頭辞**（`mcp__<name>__ask` …）になります。同梱サブエージェントは名前 **`offload`** を前提とするため、エージェントを編集しない限りこの名前を使ってください。

**ローカル LM Studio**（別マシンで動かす場合は LAN ホストを指定）:

```bash
claude mcp add offload \
  -e LLM_PROVIDER=lmstudio \
  -e LMSTUDIO_BASE_URL=http://localhost:1234/v1 \
  -e LLM_MODEL=gemma-4-e2b-it \
  -- uv run /absolute/path/to/llm_offload_mcp.py
```

**OpenRouter:**

```bash
claude mcp add offload \
  -e LLM_PROVIDER=openrouter \
  -e OPENROUTER_API_KEY=sk-or-... \
  -e LLM_MODEL=meta-llama/llama-3.3-70b-instruct \
  -- uv run /absolute/path/to/llm_offload_mcp.py
```

**xAI Grok:**

```bash
claude mcp add offload \
  -e LLM_PROVIDER=grok \
  -e XAI_API_KEY=xai-... \
  -e LLM_MODEL=grok-2-latest \
  -- uv run /absolute/path/to/llm_offload_mcp.py
```

JSON 形式の MCP 設定（`.mcp.json`、Claude Desktop など）でも同等です:

```json
{
  "mcpServers": {
    "offload": {
      "command": "uv",
      "args": ["run", "/absolute/path/to/llm_offload_mcp.py"],
      "env": {
        "LLM_PROVIDER": "lmstudio",
        "LMSTUDIO_BASE_URL": "http://localhost:1234/v1",
        "LLM_MODEL": "gemma-4-e2b-it"
      }
    }
  }
}
```

### 4. 動作確認

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

生成系ツールはいずれも `provider` と `model` を受け取り、その 1 回の呼び出しに限り既定を上書きできます。

### ファイル入力（オフロードが実際に節約になる箇所）

`summarize`・`classify`・`extract` は、インラインの `text` の代わりに `path`（ファイルパスや glob。例: `logs/run.txt`、`src/**/*.py`）を受け取れます。`ask` は `path` を追加の文脈として受け取ります。サーバーがファイルを自分で読み込むため、呼び出し側はパスだけを送ります。大きな入力では、ペイロードを転送するためにオーケストレータの出力トークンを払わずに済み、これがまさに狙いです。

- glob が複数一致した場合は、各ファイル名のヘッダ付きで連結されます。
- 上限: `OFFLOAD_MAX_FILES`（既定 50）と `OFFLOAD_MAX_CHARS`（既定 100000）。超過時は明確なエラーを返します。
- 読み込みはサーバープロセスのファイル権限で行われます。**クラウド**プロバイダを指定している場合、ファイル内容はそのプロバイダへ送信される点に注意してください。重要なファイルはローカルバックエンドで処理してください。

## トークン削減

オフロードがフロンティアのトークンを節約できるのは特定の形のときだけですが、得をするときは大きく得をします。原則は、呼び出し側が**送るものも受け取るものも少ない**ときに節約になる、です。つまり**生成**（小さなプロンプト → 大きな出力）と、**`path` によるファイル入力**（ペイロードではなくパスだけを送る）。小さな入力をインラインで丸投げすると、自分でやるより*高くつき*ます——それはフロンティアモデルで、バッチで、あるいは自律実行で。

| ツール | 得をする条件 | 例 | フロンティア → オフロード* | 削減 |
|--------|-------------|----|---------------------------|------|
| `summarize` | 大きいファイルを `path` で | 3k トークンのログ → 60 トークンの要約 | 3,300 → 185 | **約 94%** |
| `extract` | 大きいソースを `path` で | 1.5k トークンの文書 → JSON | 1,750 → 175 | **約 90%** |
| `translate` | テキスト/ファイルを `path` で | 1k トークンの文書 | 6,000 → 1,125 | **約 81%** |
| `mock_data` | 仕様 → データ | JSON 50 件 | 10,000 → 2,075 | **約 79%** |
| `commit_message` | diff を `path` で | 500 トークンの diff | 700 → 165 | **約 76%** |
| `pr_description` | diff を `path` で | 500 トークンの diff → 説明 | 1,500 → 325 | **約 78%** |
| `changelog` | git log（inline/`path`） | コミット 30 件 → 整理されたノート | 1,550 → 375 | **約 76%** |
| `map` | glob を 1 回で | ログ 30 件 → 要約 30 件 | 30 回 → 1 回 | **往復が約 30 分の 1** |
| `ask` | 小さなプロンプト → 大きな出力 | 30 → 600 トークン | 3,030 → 750 | **約 75%** |
| `rewrite` | それなりの長さの文章 | 200 トークンの段落 | 1,200 → 325 | **約 73%** |
| `classify` | 大きいファイル/バッチ | 短いメッセージ → インラインで | 60 → 302 | ✗ 小 · 約 96% 大 |
| `health` | 診断用 | — | — | 該当なし |

<sub>* 重み付けユニット（出力は入力の約 5 倍で計上、実コスト比に基づく）。フロンティアモデルがインラインで処理する場合との比較。削減量は規模に比例し、`path` で渡すファイルが大きいほど、呼び出し側がそれを読み込まないため削減も大きくなります。フロンティアモデルを介さない（自律実行）場合、削減は 100% です。</sub>

## 設定

設定はすべて環境変数で行います。既定（ローカル LM Studio）で問題なく、`model` を呼び出しごとに渡すなら、必須の変数はありません。

| 変数 | 説明 | 既定値 |
|------|------|--------|
| `LLM_PROVIDER` | 既定のプロバイダ名（表を参照）。 | *(下の優先順位を参照)* |
| `LLM_MODEL` | 既定のモデル ID（プロバイダの呼称どおり）。 | *(未設定)* |
| `LLM_TIMEOUT` | リクエストのタイムアウト（秒）。 | `300` |
| `OFFLOAD_MAX_FILES` | `path` の glob が一致できる最大ファイル数。 | `50` |
| `OFFLOAD_MAX_CHARS` | `path` から読み込む最大総文字数。 | `100000` |
| `<PROVIDER>_BASE_URL` | プロバイダのエンドポイント上書き（例: `LMSTUDIO_BASE_URL`）。 | プリセット |
| `<PROVIDER>_API_KEY` | プロバイダの API キー（例: `OPENROUTER_API_KEY`）。 | 慣例の環境変数 / `LLM_API_KEY` |
| `<PROVIDER>_MODEL` | 特定プロバイダの既定モデル。 | `LLM_MODEL` |
| `LLM_BASE_URL` / `LLM_API_KEY` | 既定プロバイダ向けの汎用フォールバック。 | — |
| `OPENROUTER_REFERER` / `OPENROUTER_TITLE` | OpenRouter のランキング用ヘッダ（任意）。 | — |
| `HERMES_BASE_URL` | `/v1` で終わる Hermes ボットのゲートウェイです。設定すると、デフォルトのプロバイダーが `hermes` になります。 | *(未設定)* |
| `HERMES_API_KEY` | その Hermes プロファイルの `API_SERVER_KEY` です。 | *(未設定)* |
| `HERMES_BOT` | ボット（プロファイル）名です。`hermes` のモデルとして使うため、二重に設定する必要はありません。 | `HERMES_MODEL` |

### 呼び出しが使うプロバイダー

`provider` を指定しない呼び出しは、次の順で解決されます。

1. **`LLM_PROVIDER`** が設定されている場合 — 明示的な選択が常に優先されます。
2. **`hermes`** — `HERMES_BASE_URL` が設定されている場合。ボットの設定は意図的な行為なので、ローカルのフォールバックより優先されます。LM Studio *と* ボットの両方を動かしている場合、別途指定しない限り作業はボットに渡ります。
3. **`lmstudio`** — それ以外。あくまでフォールバックの推測です。

空文字列は未設定として扱われるため、未設定の値をそのまま通す設定（プラグインがそうします）は、設定していない場合とまったく同じ動作になります。

`health` は解決したプロバイダーとその理由を報告するので、推測する必要はありません。

コピペ用のひな形は [`.env.example`](.env.example) を参照してください。

## Claude Code サブエージェント（任意）

[`agents/llm-offloader.md`](agents/llm-offloader.md) は、軽量作業をこのサーバーへ積極的に振り分け、重い処理や正確性が重要な処理はメインエージェントへ戻す、すぐ使えるサブエージェントです。小さなディスパッチモデル（`sonnet`、より安く済ませるなら `haiku`）で動くため*振り分け*はフロンティアモデルよりずっと安く、*実作業*はあなたのバックエンドに載ります。

```bash
# ユーザー全体
cp agents/llm-offloader.md ~/.claude/agents/
# またはプロジェクト単位
mkdir -p .claude/agents && cp agents/llm-offloader.md .claude/agents/
```

> `tools:` は `mcp__offload__*` を参照するため、サーバーを名前 **`offload`** で登録しておく必要があります。

## ティアリング: ローカル → Sonnet → フロンティア

オフローダーはシンプルなコスト階層の**ローカル層**です。同梱の `mid-tier` サブエージェントと組み合わせると、フロンティアモデルに対して 3 層のルーティングが得られます。

| 層 | 実行先 | 用途 |
|----|--------|------|
| **ローカル** | オフロードのバックエンド（0.6〜4B のローカルモデル、または任意のプロバイダ） | 軽量・非クリティカルな作業 — 要約 / 分類 / 翻訳 / 抽出、コミットメッセージ、擬似データ、ファイル横断の `map` |
| **ミッド** | **Sonnet**（[`agents/mid-tier.md`](agents/mid-tier.md)） | ローカルモデルの能力を超えるがフロンティアモデルまでは不要な作業 — 文書全体を読んで抽出、軽い分析、低リスク / 定型コード、機械的リファクタ |
| **フロンティア** | メインモデル（例: Opus） | 正確性が重要、または難しい作業 — 本質的なロジック、アーキテクチャ、セキュリティ、多段推論 |

`mid-tier` 層は**バックエンド不要**です — Claude（Sonnet）上で直接動くため、ローカルや OpenAI 互換のオフロードプロバイダが未設定でも機能します。よいパターン: フロンティアモデルが大きな機械的読み取り（例: 複数ファイルの API 仕様からの抽出）を `mid-tier` に委譲し、**実装に使う部分だけをスポットチェック**する — まとまった作業は安く、要となる詳細は検証済みのまま。

```bash
cp agents/mid-tier.md ~/.claude/agents/
```

## Hermes ボットへのタスク委譲（agent_mcp.py）

上記のツールは*生成*をオフロードします — テキストを入れて、テキストが出てきます。`agent_mcp.py` は別の、オプションのサーバーで、*作業*をオフロードします。タスク全体を、独自のシェル、ファイルシステム、`gh` CLI を持つ [Hermes](https://github.com/NousResearch/hermes-agent) ボットに渡し、ボットが報告した内容を返します。

同じ発想を一歩進めたものです。`summarize(path=...)` はファイルをコンテキストから外します。`delegate` はタスク全体をコンテキストから外します — ボットが diff、CI ログ、Issue スレッドを読み、あなたには結論だけが届きます。

**このサーバーは読み取り専用ではありません。** Hermes ボットは自身の認証情報で動作します。コミット、push、コメントが可能です。ツールにはその旨が注釈されており、このサーバーは意図的に独自の安全策を追加しません — ボット自身の Hermes `approvals.deny` ルールが下限です。すべてのタスクを、名前付きのリポジトリとパスにスコープしてください。

```bash
export HERMES_BASE_URL=http://192.168.1.50:8649/v1   # the bot's gateway, ending in /v1
export HERMES_API_KEY=...                            # that profile's API_SERVER_KEY
export HERMES_BOT=github                             # a Hermes profile name
uv run agent_mcp.py
```

MCP サーバー名 `agent` として登録し、設定は環境変数で渡します。

```bash
claude mcp add agent \
  -e HERMES_BASE_URL=http://192.168.1.50:8649/v1 \
  -e HERMES_API_KEY=...  \
  -e HERMES_BOT=github \
  -- uv run /absolute/path/to/agent_mcp.py
```

`HERMES_API_KEY` は、宛先となる Hermes プロファイルの `API_SERVER_KEY` です（そのプロファイルの `.env` にあります）。`HERMES_BOT` はプロファイル名です。一覧は `bots` で確認できます。

| Tool | |
|---|---|
| `delegate` | タスクをボットに渡し、その報告を返します。オプションの `bot`、`path`、`system`。 |
| `bots` | このエンドポイントが提供するボット名を一覧します。 |
| `health` | キーを表示せずに、エンドポイントとその設定を確認します。 |

エンドポイントとキーは環境変数からのみ読み取り、ツール引数からは決して読み取りません。そのため、プロンプトによって委譲先を別の場所へ向けることはできません。ボット名は実行前にエンドポイントと照合されます。Hermes は未知のモデル名を拒否せず、自身のプロファイルで応答するため、チェックしないタイプミスは、静かに別のエージェントへタスクを渡してしまいます。

Hermes ボットは OpenAI のチャット API も話すため、上記のツールの通常のプロバイダーとしてもすでに動作します — `HERMES_BASE_URL` と `HERMES_API_KEY` を設定すれば `ask(provider="hermes")` です。そこでは安価なモデルを選んでください。それらのツールは読み取り専用と注釈されていますが、背後のエージェントは操作できてしまいます。

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `could not reach the endpoint` | バックエンド未起動 / URL 誤り。LM Studio は **Start Server**、LAN 利用なら `0.0.0.0` にバインドし、`LMSTUDIO_BASE_URL` を設定。 |
| `401/403 authentication failure` | API キーが未設定/無効。プロバイダの `*_API_KEY` を設定。 |
| `404 … Model '…' may not exist` | モデル ID が誤り、または未読み込み。`health` で実際の提供モデルを確認。 |
| `429 rate-limited` | 時間を置く、または `provider=` で別プロバイダへ回す。 |
| `timed out` | 入力が大きい / モデルが遅い・読み込み中。`LLM_TIMEOUT` を上げる。 |
| サブエージェントにツールが無い | サーバーが `offload` という名前で登録されていない（または未登録）。 |

## 開発

```bash
uvx ruff@0.15.0 check .   # lint
uv run --with 'mcp<2' --with httpx python -c \
  "import importlib.util as u; s=u.spec_from_file_location('m','llm_offload_mcp.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print('ok', m.mcp.name)"
```

CI（GitHub Actions）は、push と PR のたびに同じ lint とインポートのスモークテストを実行します。

Claude Code は、このリポジトリ内で作業しているとき、プラグイン自身の `.mcp.json` を **project** MCP 設定として読み込み、そこから `offload` と `agent` の起動を提案します。これらは辞退してください。そのファイルはプラグインの宣言であり、プロジェクトのセットアップではありません。パスは、インストール済みプラグインに対して Claude Code が `${CLAUDE_PLUGIN_ROOT}` を展開したときだけ解決されます。すでにご自身で `offload` を登録している場合、プロジェクト側のコピーを承認すると、ご自身の登録をシャドウしてしまいます。


## コントリビュート

Issue・PR を歓迎します。サーバーは単一ファイル・プロバイダ中立を保ってください。新しいプロバイダは通常 `PROVIDERS` レジストリに 1 行追加するだけです。

## ライセンス

[MIT](LICENSE) © John Paul Soliva
