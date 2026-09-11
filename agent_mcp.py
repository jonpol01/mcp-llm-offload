#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "httpx>=0.27"]
# ///
"""agent_mcp — hand a whole task to a Hermes agent instead of doing it yourself.

Where ``llm_offload_mcp`` offloads *generation* to a plain model, this server offloads
*work* to an agent that owns tools — a shell, a filesystem, the ``gh`` CLI, skills,
memory. You describe a task in words; the bot performs it and reports back. The
frontier model never loads the diff, the log or the issue thread it worked from, which
is where the token saving comes from.

That difference is also the risk. A Hermes bot can change things: commit, push, comment
on an issue, edit a file. Nothing here is read-only and the tool annotations say so.
Keep the bot's own permissions (Hermes ``approvals.deny``) as the real safety floor —
this server does not add one.

Endpoint and credentials are read ONLY from the environment, never from tool arguments,
so a prompt cannot redirect a delegation to somewhere else:

    HERMES_BASE_URL   the bot gateway's OpenAI-compatible URL, ending in /v1  (required)
    HERMES_API_KEY    that gateway's API_SERVER_KEY (required when it sets one)
    HERMES_BOT        default bot name — a Hermes profile, e.g. "github"
    HERMES_TIMEOUT    seconds to wait for one run (default 900; agent runs are slow)

Run it:
    uv run agent_mcp.py

Register it as the MCP server name ``agent``.
"""

from __future__ import annotations

import glob as _glob
import json
import os
from typing import Annotated, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

# --- Configuration (environment only — never tool arguments) -----------------

BASE_URL: str = (os.environ.get("HERMES_BASE_URL") or "").rstrip("/")
API_KEY: Optional[str] = os.environ.get("HERMES_API_KEY")
DEFAULT_BOT: Optional[str] = os.environ.get("HERMES_BOT")
TIMEOUT: float = float(os.environ.get("HERMES_TIMEOUT", "900"))
MAX_PATH_FILES: int = int(os.environ.get("OFFLOAD_MAX_FILES", "50"))
MAX_PATH_CHARS: int = int(os.environ.get("OFFLOAD_MAX_CHARS", "100000"))

mcp = FastMCP("agent_mcp")


# --- Helpers -----------------------------------------------------------------

def _resolve_bot(bot: Optional[str]) -> str:
    """Return the bot name to address, or raise with an actionable message."""
    if not BASE_URL:
        raise ValueError(
            "No Hermes endpoint. Set HERMES_BASE_URL to the bot gateway's "
            "OpenAI-compatible URL (the one ending in /v1), e.g. "
            "http://192.168.1.50:8649/v1"
        )
    chosen = bot or DEFAULT_BOT
    if not chosen:
        raise ValueError(
            "No bot named. Pass bot=... or set HERMES_BOT to a Hermes profile "
            "name. Call the `bots` tool to see what this endpoint serves."
        )
    return chosen


def _read_path(pattern: str) -> str:
    """Read a file or glob locally and return it labelled, so only the path crosses the wire."""
    matches = sorted(p for p in _glob.glob(pattern, recursive=True) if os.path.isfile(p))
    if not matches:
        raise ValueError(f"no files matched '{pattern}'")
    if len(matches) > MAX_PATH_FILES:
        raise ValueError(
            f"'{pattern}' matched {len(matches)} files; the cap is {MAX_PATH_FILES}. "
            "Narrow the glob or raise OFFLOAD_MAX_FILES."
        )
    chunks: List[str] = []
    total = 0
    for name in matches:
        try:
            body = open(name, "r", encoding="utf-8", errors="replace").read()
        except OSError as e:
            raise ValueError(f"could not read '{name}': {e}") from e
        total += len(body)
        if total > MAX_PATH_CHARS:
            raise ValueError(
                f"'{pattern}' exceeds {MAX_PATH_CHARS} characters. Narrow the glob "
                "or raise OFFLOAD_MAX_CHARS."
            )
        chunks.append(f"----- file: {name} -----\n{body}")
    return "\n\n".join(chunks)


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    return headers


def _explain(e: Exception) -> str:
    """Turn a transport or HTTP failure into something the caller can act on."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code in (401, 403):
            return (
                f"Error: the Hermes gateway rejected the credentials ({code}). "
                "HERMES_API_KEY must match that profile's API_SERVER_KEY."
            )
        if code == 404:
            return (
                f"Error: {BASE_URL} has no such route (404). Check that the URL ends "
                "in /v1 and that the gateway's api_server platform is enabled."
            )
        body = (e.response.text or "")[:300]
        return f"Error: Hermes returned HTTP {code}: {body}"
    if isinstance(e, httpx.ConnectError):
        return (
            f"Error: could not reach {BASE_URL}. Is the bot's gateway running, and is "
            "its api_server listening on that host and port?"
        )
    if isinstance(e, httpx.ReadTimeout):
        return (
            f"Error: the bot did not finish within {TIMEOUT:.0f}s. Agent runs are slow; "
            "raise HERMES_TIMEOUT, or split the task into smaller ones."
        )
    return f"Error: {type(e).__name__}: {e}"


async def _get_models() -> List[str]:
    """Bot names this endpoint serves. Raises on transport or HTTP failure."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{BASE_URL}/models", headers=_headers())
        resp.raise_for_status()
        return [m.get("id", "") for m in (resp.json().get("data") or [])]


async def _served() -> Optional[List[str]]:
    """Served bot names, or None when the list could not be fetched.

    Worth the extra round-trip before every delegation: Hermes does not reject an
    unknown model name — it answers it on its own profile — so a typo would hand the
    task to a different agent, holding different credentials, and look like success.
    """
    try:
        return await _get_models()
    except Exception:
        return None


async def _run(bot: str, messages: List[dict]) -> str:
    """POST one agent run and return its final report."""
    payload = {"model": bot, "messages": messages, "stream": False}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(f"{BASE_URL}/chat/completions", json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise ValueError(f"bot returned no choices: {json.dumps(data)[:300]}")
    return (choices[0].get("message", {}).get("content") or "").strip()


# --- Tools -------------------------------------------------------------------

@mcp.tool(
    name="delegate",
    annotations={
        "title": "Delegate a task to a Hermes bot",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def delegate(
    task: Annotated[str, Field(
        description="The task, in plain words, written so the bot can act on it without you. "
                    "Name the repo/paths it may touch and say what to report back.",
        min_length=1)],
    bot: Annotated[Optional[str], Field(
        description="Which bot runs it — a Hermes profile name. Defaults to HERMES_BOT.")] = None,
    path: Annotated[Optional[str], Field(
        description="Optional local file or glob folded in as context; read here, so only "
                    "its contents cross the wire, not a path the bot cannot see.")] = None,
    system: Annotated[Optional[str], Field(
        description="Optional steering for this one run (tone, format, hard limits). "
                    "The bot's own persona and rules still apply.")] = None,
) -> str:
    """Hand a task to a Hermes bot and return what it reports back.

    Use this for work that is better *done* than *generated*, and that you would
    otherwise pay frontier tokens to read your way through: triage an issue, review a
    pull request and post the findings, chase a CI failure, summarise a long thread,
    tidy a branch. The bot reads the diff, the log and the thread — you get the report.

    This is not read-only. The bot acts with its own credentials and can commit, push
    and comment. Scope every task to named repos and paths, and rely on the bot's own
    Hermes `approvals.deny` rules as the safety floor.

    The bot name is checked against the endpoint first, because Hermes answers an
    unknown name on its own profile rather than refusing it.

    For plain text work with no side effects — summarise, classify, extract — prefer
    `llm_offload_mcp`, pointing it at a cheap model rather than at an agent.

    Returns:
        str: the bot's final report, or an 'Error: ...' string.
    """
    try:
        chosen = _resolve_bot(bot)
    except ValueError as e:
        return f"Error: {e}"

    served = await _served()
    if served and chosen not in served:
        return (
            f"Error: this endpoint serves no bot named '{chosen}' — it serves: "
            f"{', '.join(served)}. Hermes would not have refused: it answers an unknown "
            "name on its own profile, so the task would have run on a different agent "
            "with different credentials."
        )

    content = task
    if path:
        try:
            content = f"{task}\n\n{_read_path(path)}"
        except ValueError as e:
            return f"Error: {e}"

    messages: List[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})

    try:
        return await _run(chosen, messages)
    except Exception as e:  # reported, never raised at the MCP client
        return _explain(e)


@mcp.tool(
    name="bots",
    annotations={
        "title": "List the bots this endpoint serves",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def bots() -> str:
    """List the bot names this Hermes gateway serves, for use as `delegate(bot=...)`.

    Returns:
        str: one bot name per line, or an 'Error: ...' string.
    """
    if not BASE_URL:
        return ("Error: no Hermes endpoint. Set HERMES_BASE_URL to the gateway's "
                "OpenAI-compatible URL (ending in /v1).")
    try:
        names = await _get_models()
    except Exception as e:
        return _explain(e)
    if not names:
        return "No bots reported by this endpoint."
    default = DEFAULT_BOT or "(unset — pass bot=... or set HERMES_BOT)"
    return "\n".join(names) + f"\n\ndefault (HERMES_BOT): {default}"


@mcp.tool(
    name="health",
    annotations={
        "title": "Check the Hermes endpoint",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def health() -> str:
    """Report whether the configured Hermes endpoint is reachable and how it is set up.

    Returns:
        str: a short status block, or an 'Error: ...' string. Never prints the API key.
    """
    lines = [
        f"base_url : {BASE_URL or '(unset)'}",
        f"api_key  : {'set' if API_KEY else 'not set'}",
        f"bot      : {DEFAULT_BOT or '(unset)'}",
        f"timeout  : {TIMEOUT:.0f}s",
    ]
    if not BASE_URL:
        return "\n".join(lines) + "\n\nError: HERMES_BASE_URL is not set."
    try:
        served = await _get_models()
    except Exception as e:
        return "\n".join(lines) + "\n\n" + _explain(e)
    lines.append(f"reachable: yes — serves {', '.join(served) or '(nothing)'}")
    if DEFAULT_BOT and DEFAULT_BOT not in served:
        lines.append(f"WARNING  : HERMES_BOT '{DEFAULT_BOT}' is not served here.")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
