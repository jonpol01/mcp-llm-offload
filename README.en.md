<p align="right"><a href="README.md">日本語</a> · <b>English</b></p>

# mcp-llm-offload

> An MCP server that offloads **light LLM work** from Claude (or any MCP client) to a model you control — a **local** LLM (LM Studio, Ollama, llama.cpp) or **any OpenAI-compatible provider** (OpenRouter, xAI Grok, OpenAI, Groq, Together…). Save frontier-model quota on the cheap, non-critical stuff.

[![CI](https://github.com/seaosinc/mcp-llm-offload/actions/workflows/ci.yml/badge.svg)](https://github.com/seaosinc/mcp-llm-offload/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-8A2BE2.svg)](https://modelcontextprotocol.io)
[![Code style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

<p align="center">
  <img src="assets/flow.svg" alt="Events trigger small local-LLM workers that use a memory store and tools (n8n, http) to post to Slack, Linear, GitHub and Discord — all without Claude in the loop" width="680">
</p>

Frontier models are powerful, but a lot of day-to-day agent work is *light*: summarizing a log, classifying a ticket, extracting fields from text, rephrasing a sentence. Paying frontier-model rates (and quota) for that is wasteful. `mcp-llm-offload` exposes a handful of MCP tools that forward those tasks to a backend of **your** choosing. Switch backends with an environment variable, or override **per call**.

This README only has what you need to get started. All the details are in the [wiki](https://github.com/seaosinc/mcp-llm-offload/wiki).

## What gets offloaded, and what stays with Claude

| Work | Goes to |
|---|---|
| Summarize a log, a diff or a long thread | **Offload backend**, via `summarize(path=…)` so the file never enters your context |
| Classify, extract, translate, rewrite | **Offload backend** |
| Commit message, PR description, changelog, mock data | **Offload backend** |
| Triage PRs and issues, read review threads | **Hermes bot**, via `delegate` |
| Draft a reply, post a comment, label or close an issue | **Hermes bot**, via `delegate` |
| Open a PR a human has approved | **Hermes bot**, via `delegate`\* |
| Write or change code | **Claude** |
| Review a diff for real bugs, or judge whether a reviewer is right | **Claude** |
| Architecture, security, API design | **Claude** |
| Run tests, builds or linters, or edit the working tree | **Claude** |
| `git push` / `commit` / `clone`, one-line `gh` calls | **Claude** |
| Approve a PR before it opens | **You** |
| Public replies on someone else's project | **You** — the bot posts under your account |

**\*** When the PR text already exists on your machine, open it yourself. The bot cannot read your files, so delegating means pasting the whole text into the task, which costs more than the command.

One rule decides every row: **does the work need local execution or code judgement?** If it does, it stays with Claude. Where "offload backend" resolves, what the fallback covers, and the per-workflow tables are in the wiki's [Tiering](https://github.com/seaosinc/mcp-llm-offload/wiki/Tiering-en).

## Installation

### Claude Code plugin (recommended)

```bash
/plugin marketplace add seaosinc/mcp-llm-offload
/plugin install mcp-llm-offload@mcp-llm-offload
```

Claude Code then asks for the configuration — provider, model, and, if you run one, the Hermes bot's URL, key and name. Values marked sensitive go to your keychain. Change them later with `/plugin configure mcp-llm-offload@mcp-llm-offload`.

Things to know:

- **Under a plugin install the tools are renamed.** They become `mcp__plugin_mcp-llm-offload_offload__*` and `mcp__plugin_mcp-llm-offload_agent__*`. Anything that names the tools explicitly — a subagent's `tools:` list, a `CLAUDE.md` routing rule, a hook — has to use the namespaced form or it will silently call nothing. Run `/mcp` to see the live names.
- **The plugin ships no subagent.** The bundled `llm-offloader` agent assumes the unnamespaced `mcp__offload__*` tool names, so install it by hand from [`agents/`](agents/) if you want it. The steps are in [Installation](https://github.com/seaosinc/mcp-llm-offload/wiki/Installation-en).
- `uv` still has to be on `PATH`, and you still need a backend to talk to (a running local server such as LM Studio, or an API key for a hosted provider).

### Manual (`claude mcp add`)

```bash
git clone https://github.com/seaosinc/mcp-llm-offload.git
claude mcp add offload \
  -e LLM_PROVIDER=lmstudio \
  -e LLM_MODEL=gemma-4-e2b-it \
  -- uv run /absolute/path/to/mcp-llm-offload/llm_offload_mcp.py
```

The server name you choose here becomes the tool prefix (`mcp__offload__ask` …). The bundled subagent expects the name **`offload`**. OpenRouter / Grok examples, JSON config (`.mcp.json`, Claude Desktop), and running without `uv` are in [Installation](https://github.com/seaosinc/mcp-llm-offload/wiki/Installation-en).

### Verify

In Claude Code, run the `health` tool (or ask Claude to). You should see the resolved provider, base URL, and the list of models the backend reports.

## Tools

| Tool | Signature | Purpose |
|------|-----------|---------|
| `ask` | `ask(prompt, system?, path?, provider?, model?, temperature?, max_tokens?)` | Free-form light generation; `path` folds in a file as context. |
| `summarize` | `summarize(text?, max_words?, style?, path?, provider?, model?)` | Faithful summary of `text` or a file/glob (`path`). |
| `classify` | `classify(labels[], text?, path?, provider?, model?)` | Single-label classification of `text` or a file; returns one of `labels`. |
| `extract` | `extract(instructions, text?, path?, schema?, provider?, model?)` | Structured extraction → clean JSON; optional `schema`, with one local repair retry on bad JSON. |
| `translate` | `translate(target, text?, path?, style?, provider?, model?)` | Translate `text` or a file/glob into `target`, preserving formatting. |
| `rewrite` | `rewrite(text?, tone?, path?, provider?, model?)` | Polish/tighten prose — PR descriptions, commit bodies, docs. |
| `commit_message` | `commit_message(text?, path?, style?, provider?, model?)` | Conventional-commit message from a diff (`text` or a diff file via `path`). |
| `mock_data` | `mock_data(spec, count?, fmt?, provider?, model?)` | Generate fake JSON/CSV/SQL/NDJSON from a spec (small in → big out). |
| `pr_description` | `pr_description(text?, path?, intent?, provider?, model?)` | Draft a PR description from a diff; descriptive only, never claims correctness. |
| `changelog` | `changelog(text?, path?, style?, version?, provider?, model?)` | Group a git log into Added/Changed/Fixed release notes. |
| `map` | `map(op, path, …op args)` | Run one op (summarize/classify/extract/translate/rewrite) on **each** file of a glob → `{file: result}`. One call, not N. |
| `health` | `health(provider?)` | Reachability check + lists the backend's models. |

Every generation tool accepts `provider` and `model` to override the configured default for that single call. `summarize`, `classify`, and `extract` take a `path` (file path or glob) and the server reads it itself, so the caller sends only the path. Offloading actually saves tokens in this shape — **a large input via `path`**, or **a small prompt that produces a large output**. Measured numbers per tool are in [Token-savings](https://github.com/seaosinc/mcp-llm-offload/wiki/Token-savings-en).

## Configuration

All configuration is via environment variables — none are required if the defaults (a local LM Studio) suit you.

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_PROVIDER` | Default provider name. `lmstudio` `ollama` `llamacpp` `openrouter` `grok` `openai` `groq` `together` `deepinfra` `mistral`, or any name with `<NAME>_BASE_URL` set. | *(see precedence below)* |
| `LLM_MODEL` | Default model id (as the provider names it). | *(unset)* |
| `<PROVIDER>_BASE_URL` / `<PROVIDER>_API_KEY` | Per-provider endpoint and key (e.g. `LMSTUDIO_BASE_URL`, `OPENROUTER_API_KEY`). | preset / conventional env |
| `HERMES_BASE_URL` / `HERMES_API_KEY` / `HERMES_BOT` | Hermes bot gateway (ending in `/v1`), that profile's `API_SERVER_KEY`, and the bot (profile) name. | *(unset)* |
| `LLM_FALLBACK_PROVIDER` | A second backend to try when the first is unreachable, timing out, overloaded or out of quota. | *(none)* |
| `OFFLOAD_ROUTING` | `single`, or `spread` to send light work and heavy work to different backends. | `single` |

A call that does not name a `provider` resolves **`LLM_PROVIDER` → `hermes` (if `HERMES_BASE_URL` is set) → `lmstudio`**. `health` reports which provider it resolved and why. Every variable, how `spread` splits the work, and what the fallback covers are in [Configuration](https://github.com/seaosinc/mcp-llm-offload/wiki/Configuration-en); a copy-paste starting point is [`.env.example`](.env.example).

## Delegating a task to a Hermes bot (agent_mcp.py)

The tools above offload *generation*. `agent_mcp.py` is a separate, optional server that offloads *work*: it hands a whole task to a [Hermes](https://github.com/NousResearch/hermes-agent) bot, which has its own shell, filesystem and `gh` CLI, and returns what the bot reports back. The diff, the CI log and the issue thread never enter your context.

**This server is not read-only.** A Hermes bot acts with its own credentials: it can commit, push and comment. This server deliberately adds no safety of its own — the bot's own `SOUL.md` and `approvals.deny` are the floor.

```bash
claude mcp add agent \
  -e HERMES_BASE_URL=http://192.168.1.50:8649/v1 \
  -e HERMES_API_KEY=... \
  -e HERMES_BOT=offload \
  -- uv run /absolute/path/to/agent_mcp.py
```

`HERMES_BASE_URL` is the bot's gateway (ending in `/v1`), `HERMES_API_KEY` is that profile's `API_SERVER_KEY`, and `HERMES_BOT` is the profile name.

| Tool | |
|---|---|
| `delegate` | Hand a task to a bot and return its report. Optional `bot`, `path`, `system`. |
| `bots` | List the bot names this endpoint serves. |
| `health` | Check the endpoint and its configuration, without printing the key. |

The bot name is checked against the endpoint before the run: Hermes answers an unknown model name on its own profile rather than refusing it, so an unchecked typo would quietly hand the task to a different agent. A Hermes bot also works as a provider for the offload tools — `ask(provider="hermes")`.

### Giving a bot its rules

The plugin tells a bot what to do one task at a time. It never tells the bot what *not* to do. That lives on the bot, in its `SOUL.md` and its `approvals`, and a fresh profile has neither. A fresh profile whose user is logged in to `gh` would merge a pull request or push to `main` without a prompt. [`hermes/`](hermes/) is a kit that closes that gap. On the bot's machine, as the user the bot runs as:

```bash
hermes/setup-bot.sh offload --port 8650    # create a dedicated profile, install the SOUL and deny floor, check 46 commands
hermes/setup-bot.sh offload --check        # check only; changes nothing
```

The new profile is Hermes' own clone of your active profile (`hermes profile create --clone`), so the bot runs on the LLM your Hermes already uses, with the same credentials; nothing in the kit picks a model. **GitHub access is the one thing the kit does not set up.** As the bot's system user, run `gh auth login` once (Hermes strips `GH_TOKEN` from the bot's commands, so a token in `.env` does not work). The floor, gh login, branch protection, and `unattended_mode` are in [Hermes-bot](https://github.com/seaosinc/mcp-llm-offload/wiki/Hermes-bot-en).

## Delivering drafted text (post_mcp.py)

The offload tools only hand text back. If what you wanted delivered has to travel back through the calling model first, that is the cost this project exists to avoid. `post_mcp.py` is the optional companion that delivers it: Discord, Slack, Telegram, Linear, a GitHub comment, or a generic webhook. Destinations and secrets are read only from the environment, and `dry_run` previews without sending. `examples/ninja.py` is a loop with no Claude in it at all. Details are in [post_mcp](https://github.com/seaosinc/mcp-llm-offload/wiki/post_mcp-en).

## Documentation

| wiki | Contents |
|---|---|
| [Overview](https://github.com/seaosinc/mcp-llm-offload/wiki/Overview-en) | Feature list and how it works |
| [Installation](https://github.com/seaosinc/mcp-llm-offload/wiki/Installation-en) | Plugin caveats, every manual-registration example, the subagent |
| [Providers](https://github.com/seaosinc/mcp-llm-offload/wiki/Providers-en) | Supported-provider table and recommended local models |
| [Configuration](https://github.com/seaosinc/mcp-llm-offload/wiki/Configuration-en) | Every env var, provider precedence, `spread`, fallback |
| [Token-savings](https://github.com/seaosinc/mcp-llm-offload/wiki/Token-savings-en) | File input and measured savings per tool |
| [Tiering](https://github.com/seaosinc/mcp-llm-offload/wiki/Tiering-en) | Local → Sonnet → frontier, and the per-workflow tables |
| [Hermes-bot](https://github.com/seaosinc/mcp-llm-offload/wiki/Hermes-bot-en) | Running a bot as a backend, the SOUL and deny floor, gh login |
| [post_mcp](https://github.com/seaosinc/mcp-llm-offload/wiki/post_mcp-en) | Destination setup and tools |
| [Troubleshooting](https://github.com/seaosinc/mcp-llm-offload/wiki/Troubleshooting-en) | Symptoms and fixes |
| [Development](https://github.com/seaosinc/mcp-llm-offload/wiki/Development-en) | Lint, smoke test, CI, and the in-repo `.mcp.json` |

## Development

```bash
uvx ruff@0.15.0 check .   # lint
uv run --with 'mcp<2' --with httpx python -c \
  "import importlib.util as u; s=u.spec_from_file_location('m','llm_offload_mcp.py'); m=u.module_from_spec(s); s.loader.exec_module(m); print('ok', m.mcp.name)"
```

CI (GitHub Actions) runs the same lint + import smoke test on every push and PR.

## Contributing

Issues and PRs welcome. Keep the server single-file and provider-neutral; new providers are usually just one row in the `PROVIDERS` registry.

## License

[MIT](LICENSE) © Seaos Inc
