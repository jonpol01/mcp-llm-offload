#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0", "httpx>=0.27"]
# ///
"""llm_offload_mcp — offload light LLM work to a local model or any OpenAI-compatible provider.

Fronts an OpenAI-compatible chat-completions endpoint over stdio so Claude Code (or
any MCP client) can delegate cheap, non-critical work — simple Q&A, summarizing,
classifying, extraction, translation, rewriting, commit messages, mock data — to a model you control instead of spending
frontier-model quota.

Because LM Studio, Ollama, llama.cpp, OpenRouter, xAI (Grok), OpenAI, Groq, Together
and friends all speak the same /v1/chat/completions API, one small server talks to
all of them. Pick a default with env vars, or override per call with the `provider`
and `model` tool arguments.

File input (the token-saving bit): summarize/classify/extract accept a `path` (file
or glob) and `ask` accepts a `path` for context. The server reads the file(s) itself,
so the calling model only sends the path — not the payload. For large inputs that is
far cheaper than pasting the text through the orchestrator's output.

Configuration (all optional; sensible defaults target a local LM Studio):
    LLM_PROVIDER   default provider name (default: lmstudio)
    LLM_MODEL      default model id, as the provider names it
    LLM_TIMEOUT    request timeout in seconds (default: 300)
    OFFLOAD_MAX_FILES  max files a glob may match (default: 50)
    OFFLOAD_MAX_CHARS  max total chars read from a path/glob (default: 100000)

  Per-provider overrides (only set what you use):
    <PROVIDER>_BASE_URL   override the endpoint, e.g. LMSTUDIO_BASE_URL=http://192.168.1.50:1234/v1
    <PROVIDER>_API_KEY    API key, e.g. OPENROUTER_API_KEY / XAI_API_KEY / OPENAI_API_KEY
    <PROVIDER>_MODEL      default model for that provider

  Generic fallbacks (apply to the default provider):
    LLM_BASE_URL, LLM_API_KEY

  OpenRouter ranking headers (optional):
    OPENROUTER_REFERER, OPENROUTER_TITLE

Run:
    uv run llm_offload_mcp.py          # self-installs deps via the inline metadata above
    # or: pip install mcp httpx && python llm_offload_mcp.py
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Annotated, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# --- Provider registry -------------------------------------------------------

# Every provider is just an OpenAI-compatible endpoint. `base_url` is the default
# API root (overridable via <NAME>_BASE_URL); `key_env` is the conventional env var
# holding its API key (None => no key needed, e.g. a local server). Anything not
# listed here still works: set <NAME>_BASE_URL (and <NAME>_API_KEY) for any
# OpenAI-compatible service and use that name as the provider.
PROVIDERS: dict[str, dict] = {
    "lmstudio":   {"base_url": "http://localhost:1234/v1",            "key_env": None},
    "ollama":     {"base_url": "http://localhost:11434/v1",           "key_env": None},
    "llamacpp":   {"base_url": "http://localhost:8080/v1",            "key_env": None},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1",        "key_env": "OPENROUTER_API_KEY"},
    "grok":       {"base_url": "https://api.x.ai/v1",                 "key_env": "XAI_API_KEY"},
    "openai":     {"base_url": "https://api.openai.com/v1",           "key_env": "OPENAI_API_KEY"},
    "groq":       {"base_url": "https://api.groq.com/openai/v1",      "key_env": "GROQ_API_KEY"},
    "together":   {"base_url": "https://api.together.xyz/v1",         "key_env": "TOGETHER_API_KEY"},
    "deepinfra":  {"base_url": "https://api.deepinfra.com/v1/openai", "key_env": "DEEPINFRA_API_KEY"},
    "mistral":    {"base_url": "https://api.mistral.ai/v1",           "key_env": "MISTRAL_API_KEY"},
}

DEFAULT_PROVIDER: str = os.environ.get("LLM_PROVIDER", "lmstudio").lower()
DEFAULT_MODEL: Optional[str] = os.environ.get("LLM_MODEL")
TIMEOUT: float = float(os.environ.get("LLM_TIMEOUT", "300"))
MAX_PATH_FILES: int = int(os.environ.get("OFFLOAD_MAX_FILES", "50"))
MAX_PATH_CHARS: int = int(os.environ.get("OFFLOAD_MAX_CHARS", "100000"))

mcp = FastMCP("llm_offload_mcp")


# --- Configuration resolution ------------------------------------------------

def _resolve(
    provider: Optional[str], model: Optional[str], *, require_model: bool = True
) -> tuple[str, str, Optional[str], Optional[str]]:
    """Resolve (provider, base_url, api_key, model) for a single call.

    Precedence:
        provider : arg -> LLM_PROVIDER -> 'lmstudio'
        base_url : <PROVIDER>_BASE_URL -> LLM_BASE_URL (default provider only) -> preset
        api_key  : <PROVIDER>_API_KEY -> preset key_env -> LLM_API_KEY
        model    : arg -> <PROVIDER>_MODEL -> LLM_MODEL

    Raises:
        ValueError: with an actionable message when the base URL or model is missing.
    """
    name = (provider or DEFAULT_PROVIDER).lower()
    spec = PROVIDERS.get(name, {})
    env = name.upper().replace("-", "_")

    base_url = (
        os.environ.get(f"{env}_BASE_URL")
        or (os.environ.get("LLM_BASE_URL") if name == DEFAULT_PROVIDER else None)
        or spec.get("base_url")
    )
    if not base_url:
        raise ValueError(
            f"No base URL for provider '{name}'. It is not a known preset; set "
            f"{env}_BASE_URL to its OpenAI-compatible endpoint (the URL that ends in /v1)."
        )

    key_env = spec.get("key_env")
    api_key = (
        os.environ.get(f"{env}_API_KEY")
        or (os.environ.get(key_env) if key_env else None)
        or os.environ.get("LLM_API_KEY")
    )

    chosen_model = model or os.environ.get(f"{env}_MODEL") or DEFAULT_MODEL
    if require_model and not chosen_model:
        raise ValueError(
            f"No model set for provider '{name}'. Pass model=... or set "
            f"{env}_MODEL (or LLM_MODEL) to a model id the provider serves."
        )
    return name, base_url.rstrip("/"), api_key, chosen_model


# --- Shared helpers ----------------------------------------------------------

def _build_messages(prompt: str, system: Optional[str]) -> List[dict]:
    """Build a chat-completions messages array.

    The system instruction is folded into the user turn rather than sent as a
    separate ``system`` role. Some chat templates (notably Gemma) do not define a
    system role; folding guarantees the request works regardless of the loaded model.
    """
    if system:
        content = f"[Instructions]\n{system}\n\n[Input]\n{prompt}"
    else:
        content = prompt
    return [{"role": "user", "content": content}]


def _read_path(pattern: str) -> str:
    """Read a file path or glob locally and return its text.

    This is what makes offloading large inputs cheap: the calling model sends only
    the path, and the server reads the bytes. Multiple matches are concatenated with
    a filename header each. Reads use the server process's own permissions.

    Raises:
        ValueError: no match, too many files, unreadable file, or over the size cap.
    """
    matches = sorted(p for p in glob.glob(pattern, recursive=True) if os.path.isfile(p))
    if not matches:
        raise ValueError(f"no file matches path '{pattern}'. Pass an existing file path or glob.")
    if len(matches) > MAX_PATH_FILES:
        raise ValueError(
            f"path '{pattern}' matched {len(matches)} files (limit {MAX_PATH_FILES}). "
            "Narrow the glob or raise OFFLOAD_MAX_FILES."
        )
    parts: List[str] = []
    total = 0
    for fp in matches:
        try:
            content = Path(fp).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise ValueError(f"could not read '{fp}': {e}") from e
        total += len(content)
        if total > MAX_PATH_CHARS:
            raise ValueError(
                f"input from '{pattern}' exceeds {MAX_PATH_CHARS} chars. "
                "Narrow the selection or raise OFFLOAD_MAX_CHARS."
            )
        parts.append(f"===== {fp} =====\n{content}" if len(matches) > 1 else content)
    return "\n\n".join(parts)


def _resolve_text(text: Optional[str], path: Optional[str]) -> str:
    """Return the effective input text from exactly one of `text` or `path`."""
    if path:
        if text:
            raise ValueError("provide either text or path, not both.")
        return _read_path(path)
    if text:
        return text
    raise ValueError("provide either text or path.")


def _extra_headers() -> dict:
    """Optional provider niceties (e.g. OpenRouter ranking headers); harmless elsewhere."""
    headers: dict = {}
    referer = os.environ.get("OPENROUTER_REFERER")
    title = os.environ.get("OPENROUTER_TITLE")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    return headers


def _handle_error(e: Exception, base_url: str, model: Optional[str]) -> str:
    """Map exceptions to actionable, agent-readable error strings."""
    if isinstance(e, ValueError):
        return f"Error: {e}"
    if isinstance(e, httpx.ConnectError):
        return (
            f"Error: could not reach the endpoint at {base_url}. If it is local, confirm "
            "the server is running and bound so the client can reach it (LM Studio: "
            "Developer > Start Server, bind to 0.0.0.0 for LAN access). If it is remote, "
            "check the URL and your network."
        )
    if isinstance(e, httpx.TimeoutException):
        return (
            f"Error: request to {base_url} timed out after {TIMEOUT:.0f}s. The model may "
            "still be loading, or the input is large. Raise LLM_TIMEOUT or shorten the prompt."
        )
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        body = (e.response.text or "")[:500]
        if code in (401, 403):
            return (
                f"Error: {code} authentication failure from {base_url}. Check the provider's "
                "API key (e.g. OPENROUTER_API_KEY / XAI_API_KEY / OPENAI_API_KEY)."
            )
        if code == 404:
            return (
                f"Error: 404 from {base_url}. Model '{model}' may not exist on this provider, "
                "or the base URL is wrong (it should end in /v1). Run the health tool to list "
                "available models."
            )
        if code == 429:
            return (
                f"Error: 429 rate-limited by {base_url}. Back off and retry, or switch provider "
                "with the `provider` argument."
            )
        return f"Error: HTTP {code} from {base_url}. Body: {body}"
    return f"Error: unexpected {type(e).__name__}: {e}"


async def _chat(
    base_url: str,
    api_key: Optional[str],
    model: str,
    messages: List[dict],
    temperature: float,
    max_tokens: int,
) -> str:
    """POST to the OpenAI-compatible /chat/completions endpoint and return the text."""
    headers = {"Content-Type": "application/json", **_extra_headers()}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError(f"provider returned no choices: {json.dumps(data)[:300]}")
    content = choices[0].get("message", {}).get("content")
    return (content or "").strip()


async def _complete(
    messages: List[dict],
    provider: Optional[str],
    model: Optional[str],
    temperature: float,
    max_tokens: int,
) -> str:
    """Resolve config, call the model, and return the text or an 'Error: ...' string."""
    try:
        _name, base_url, api_key, mdl = _resolve(provider, model)
    except ValueError as e:
        return f"Error: {e}"
    try:
        return await _chat(base_url, api_key, mdl, messages, temperature, max_tokens)
    except Exception as e:  # converted to an actionable message, never raised to the client
        return _handle_error(e, base_url, mdl)


def _isolate_json(raw: str) -> str:
    """Best-effort: return a clean JSON string from a possibly-noisy reply."""
    s = raw.strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) >= 2:
            s = parts[1]
        s = s.removeprefix("json").strip()
    try:
        return json.dumps(json.loads(s))
    except (ValueError, TypeError):
        pass
    # Fall back to the outermost {...} object or [...] array and try each.
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = s.find(open_c), s.rfind(close_c)
        if start != -1 and end != -1 and end > start:
            candidate = s[start : end + 1]
            try:
                return json.dumps(json.loads(candidate))
            except (ValueError, TypeError):
                continue
    return s


def _unfence(s: str) -> str:
    """Strip a single wrapping ``` code fence (and any language tag) if present."""
    s = s.strip()
    if s.startswith("```") and s.endswith("```") and len(s) > 6:
        inner = s[3:-3]
        if "\n" in inner:
            first, rest = inner.split("\n", 1)
            if first.strip().isalpha():
                inner = rest
        return inner.strip()
    return s


# --- Tools -------------------------------------------------------------------

_PROVIDER_HELP = (
    "Optional provider override; one of the presets "
    "(lmstudio, ollama, llamacpp, openrouter, grok, openai, groq, together, deepinfra, "
    "mistral) or any name you configured via <NAME>_BASE_URL. Defaults to LLM_PROVIDER."
)
_MODEL_HELP = "Optional model id override for this call. Defaults to the provider's configured model."
_PATH_HELP = (
    "Optional path or glob (e.g. 'logs/run.txt' or 'src/**/*.py') whose contents become the "
    "input. The server reads it locally, so only the path is sent — much cheaper than pasting "
    "large text. Provide this OR the inline text argument, not both."
)


@mcp.tool(
    name="ask",
    annotations={
        "title": "Ask the offload model",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def ask(
    prompt: Annotated[str, Field(description="The prompt / question for the model.", min_length=1)],
    system: Annotated[Optional[str], Field(description="Optional steering instruction (persona, format, constraints).")] = None,
    path: Annotated[Optional[str], Field(description="Optional file path or glob to fold in as context (read locally by the server).")] = None,
    provider: Annotated[Optional[str], Field(description=_PROVIDER_HELP)] = None,
    model: Annotated[Optional[str], Field(description=_MODEL_HELP)] = None,
    temperature: Annotated[float, Field(description="Sampling temperature; lower is more deterministic.", ge=0.0, le=2.0)] = 0.7,
    max_tokens: Annotated[int, Field(description="Max tokens to generate; -1 for unbounded (local backends only).", ge=-1, le=32768)] = 1024,
) -> str:
    """Send a free-form prompt to the offload model and return its reply.

    Use this to push light, non-critical generation off frontier-model quota: simple
    Q&A, rewriting, drafting boilerplate, quick reasoning. Pass `path` to give the model
    a local file as context without pasting it. For structured tasks prefer summarize,
    classify, or extract, which constrain the output.

    Returns:
        str: the model's plain-text completion, or an 'Error: ...' string.
    """
    user = prompt
    if path:
        try:
            user = f"{prompt}\n\n----- file: {path} -----\n{_read_path(path)}"
        except ValueError as e:
            return f"Error: {e}"
    return await _complete(_build_messages(user, system), provider, model, temperature, max_tokens)


@mcp.tool(
    name="summarize",
    annotations={
        "title": "Summarize text or a file",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def summarize(
    text: Annotated[Optional[str], Field(description="The text to summarize. Provide this or `path`.")] = None,
    max_words: Annotated[int, Field(description="Approximate upper bound on summary length, in words.", ge=10, le=1000)] = 120,
    style: Annotated[Optional[str], Field(description="Optional style, e.g. 'bullet points', 'one sentence', 'plain language'.")] = None,
    path: Annotated[Optional[str], Field(description=_PATH_HELP)] = None,
    provider: Annotated[Optional[str], Field(description=_PROVIDER_HELP)] = None,
    model: Annotated[Optional[str], Field(description=_MODEL_HELP)] = None,
) -> str:
    """Summarize text (or the contents of a file/glob via `path`) using the offload model.

    Returns:
        str: the summary, or an 'Error: ...' string.
    """
    try:
        src = _resolve_text(text, path)
    except ValueError as e:
        return f"Error: {e}"
    style_hint = f" Format the summary as {style}." if style else ""
    system = (
        f"You are a precise summarizer. Produce a faithful summary in about {max_words} "
        f"words or fewer.{style_hint} Do not invent information that is not present in the input."
    )
    return await _complete(_build_messages(src, system), provider, model, 0.3, max(64, max_words * 3))


@mcp.tool(
    name="classify",
    annotations={
        "title": "Classify text or a file into one label",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def classify(
    labels: Annotated[List[str], Field(description="Allowed category labels to choose from.", min_length=2, max_length=50)],
    text: Annotated[Optional[str], Field(description="The text to classify. Provide this or `path`.")] = None,
    path: Annotated[Optional[str], Field(description=_PATH_HELP)] = None,
    provider: Annotated[Optional[str], Field(description=_PROVIDER_HELP)] = None,
    model: Annotated[Optional[str], Field(description=_MODEL_HELP)] = None,
) -> str:
    """Classify text (or the contents of a file via `path`) into exactly one of `labels`.

    Returns:
        str: the chosen label (verbatim from `labels` when the model's answer matches),
        otherwise the model's raw answer, or an 'Error: ...' string.
    """
    try:
        src = _resolve_text(text, path)
    except ValueError as e:
        return f"Error: {e}"
    label_list = ", ".join(labels)
    system = (
        "You are a single-label text classifier. Read the input and respond with EXACTLY "
        f"ONE of these labels and nothing else: {label_list}."
    )
    raw = await _complete(_build_messages(src, system), provider, model, 0.0, 32)
    if raw.startswith("Error:"):
        return raw

    cleaned = raw.strip().strip(".").lower()
    for label in labels:  # exact match wins
        if label.lower() == cleaned:
            return label
    # Substring fallback: prefer the longest matching label so "not urgent" beats "urgent".
    matches = [label for label in labels if label.lower() in cleaned]
    if matches:
        return max(matches, key=len)
    return raw


@mcp.tool(
    name="extract",
    annotations={
        "title": "Extract structured JSON from text or a file",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def extract(
    instructions: Annotated[str, Field(description="What to extract: plain language or a field list, e.g. 'name, date, total amount'.", min_length=1)],
    text: Annotated[Optional[str], Field(description="Source text to extract from. Provide this or `path`.")] = None,
    path: Annotated[Optional[str], Field(description=_PATH_HELP)] = None,
    provider: Annotated[Optional[str], Field(description=_PROVIDER_HELP)] = None,
    model: Annotated[Optional[str], Field(description=_MODEL_HELP)] = None,
) -> str:
    """Extract structured data as JSON from text (or a file via `path`) using the offload model.

    Returns:
        str: a JSON string (best-effort; fences and stray prose are stripped), or an
        'Error: ...' string.
    """
    try:
        src = _resolve_text(text, path)
    except ValueError as e:
        return f"Error: {e}"
    system = (
        "You are a data extraction engine. Extract the requested fields from the input and "
        "respond with a single valid JSON value only — no markdown, no commentary. "
        f"Fields/instructions: {instructions}"
    )
    raw = await _complete(_build_messages(src, system), provider, model, 0.0, 1024)
    if raw.startswith("Error:"):
        return raw
    return _isolate_json(raw)


@mcp.tool(
    name="translate",
    annotations={
        "title": "Translate text or a file",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def translate(
    target: Annotated[str, Field(description="Target language, e.g. 'Japanese', 'English', 'fr'.", min_length=1)],
    text: Annotated[Optional[str], Field(description="Text to translate. Provide this or `path`.")] = None,
    path: Annotated[Optional[str], Field(description=_PATH_HELP)] = None,
    style: Annotated[Optional[str], Field(description="Optional guidance, e.g. 'formal', 'casual', 'keep code and identifiers unchanged'.")] = None,
    provider: Annotated[Optional[str], Field(description=_PROVIDER_HELP)] = None,
    model: Annotated[Optional[str], Field(description=_MODEL_HELP)] = None,
) -> str:
    """Translate text (or a file/glob via `path`) into `target`, preserving formatting.

    Returns:
        str: the translation, or an 'Error: ...' string.
    """
    try:
        src = _resolve_text(text, path)
    except ValueError as e:
        return f"Error: {e}"
    style_hint = f" {style}." if style else ""
    system = (
        f"You are a professional translator. Translate the input into {target}. Preserve meaning, "
        f"tone, Markdown structure, and code / inline code verbatim.{style_hint} Output only the "
        "translation, with no preamble or commentary."
    )
    return await _complete(_build_messages(src, system), provider, model, 0.2, 4096)


@mcp.tool(
    name="rewrite",
    annotations={
        "title": "Rewrite / polish prose",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def rewrite(
    text: Annotated[Optional[str], Field(description="Text to rewrite. Provide this or `path`.")] = None,
    tone: Annotated[Optional[str], Field(description="Optional goal/tone, e.g. 'more concise', 'formal', 'friendly', 'fix grammar only'.")] = None,
    path: Annotated[Optional[str], Field(description=_PATH_HELP)] = None,
    provider: Annotated[Optional[str], Field(description=_PROVIDER_HELP)] = None,
    model: Annotated[Optional[str], Field(description=_MODEL_HELP)] = None,
) -> str:
    """Rewrite prose to be clearer/tighter — PR descriptions, commit bodies, docs, messages.

    Returns:
        str: the rewritten text, or an 'Error: ...' string.
    """
    try:
        src = _resolve_text(text, path)
    except ValueError as e:
        return f"Error: {e}"
    goal = tone or "clear, correct, and concise"
    system = (
        f"You are a careful editor. Rewrite the input to be {goal}, preserving meaning and any code "
        "or Markdown. Output only the rewritten text, with no preamble or commentary."
    )
    return await _complete(_build_messages(src, system), provider, model, 0.4, 2048)


@mcp.tool(
    name="commit_message",
    annotations={
        "title": "Draft a commit message from a diff",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def commit_message(
    text: Annotated[Optional[str], Field(description="A git diff/patch. Provide this or `path` (e.g. a file you wrote `git diff` to).")] = None,
    path: Annotated[Optional[str], Field(description=_PATH_HELP)] = None,
    style: Annotated[Optional[str], Field(description="Optional style, e.g. 'conventional', 'one line', 'with a short body'.")] = None,
    provider: Annotated[Optional[str], Field(description=_PROVIDER_HELP)] = None,
    model: Annotated[Optional[str], Field(description=_MODEL_HELP)] = None,
) -> str:
    """Draft a Conventional Commits message from a git diff (inline `text` or via `path`).

    Tip: `git diff --staged > /tmp/d.diff` then pass path='/tmp/d.diff' — the diff never has to
    pass through the calling model's output.

    Returns:
        str: the commit message, or an 'Error: ...' string.
    """
    try:
        src = _resolve_text(text, path)
    except ValueError as e:
        return f"Error: {e}"
    style_hint = f" Style: {style}." if style else ""
    system = (
        "You are a commit-message writer. From the git diff, write a Conventional Commits message: a "
        "`type(scope): summary` subject in imperative mood, <= 72 chars, plus a short body only if the "
        f"change is non-trivial.{style_hint} Output only the commit message — no fences, no commentary."
    )
    raw = await _complete(_build_messages(src, system), provider, model, 0.3, 320)
    return raw if raw.startswith("Error:") else _unfence(raw)


@mcp.tool(
    name="mock_data",
    annotations={
        "title": "Generate fake/sample data",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def mock_data(
    spec: Annotated[str, Field(description="What to generate, e.g. '20 users with id, name, email, signup_date'.", min_length=1)],
    count: Annotated[int, Field(description="How many records to produce.", ge=1, le=500)] = 10,
    fmt: Annotated[str, Field(description="Output format: 'json', 'ndjson', 'csv', or 'sql'.")] = "json",
    provider: Annotated[Optional[str], Field(description=_PROVIDER_HELP)] = None,
    model: Annotated[Optional[str], Field(description=_MODEL_HELP)] = None,
) -> str:
    """Generate fake/sample data from a spec. Small prompt, big output — a clear token win.

    For large counts prefer fmt='csv' or 'ndjson' (far more compact); very large JSON can still
    hit the output-token cap and truncate.

    Returns:
        str: the generated data, or an 'Error: ...' string.
    """
    system = (
        f"You are a test-data generator. Produce {count} realistic but entirely FAKE records matching "
        f"this spec, formatted as {fmt}. Vary the values. Output only the data — no prose, no commentary."
    )
    raw = await _complete(_build_messages(spec, system), provider, model, 0.8, min(8192, max(512, count * 150)))
    if raw.startswith("Error:"):
        return raw
    return _isolate_json(raw) if fmt.lower() == "json" else _unfence(raw)


@mcp.tool(
    name="health",
    annotations={
        "title": "Check provider connectivity",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def health(
    provider: Annotated[Optional[str], Field(description=_PROVIDER_HELP)] = None,
) -> str:
    """Check that the resolved provider is reachable and list its models.

    Call this first when diagnosing failures.

    Returns:
        str: JSON with {provider, base_url, api_key_present, configured_model, reachable,
        models, configured_model_loaded}, or an 'Error: ...' string.
    """
    try:
        name, base_url, api_key, mdl = _resolve(provider, None, require_model=False)
    except ValueError as e:
        return f"Error: {e}"
    headers = dict(_extra_headers())
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=min(TIMEOUT, 15)) as client:
            resp = await client.get(f"{base_url}/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return _handle_error(e, base_url, mdl)

    models = [m.get("id") for m in data.get("data", [])]
    return json.dumps(
        {
            "provider": name,
            "base_url": base_url,
            "api_key_present": bool(api_key),
            "configured_model": mdl,
            "reachable": True,
            "models": models,
            "configured_model_loaded": (mdl in models) if mdl else None,
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()
