#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0", "httpx>=0.27"]
# ///
"""llm_post_mcp — optional companion to llm_offload_mcp: deliver text to outbound targets.

Where llm_offload_mcp is read-only (it only reads files and calls a local LLM), this
server has external side effects: it POSTs a message to a destination you configured —
Discord, Slack, Telegram, a Linear issue comment, a GitHub issue/PR comment, or a generic
webhook (n8n and friends). It does no LLM work and costs ~no tokens; its job is reach.

The intended pairing: llm_offload_mcp drafts cheaply on a local model, this server delivers
the draft. A no-Claude daemon (offload to draft + post to deliver, on a cron) is therefore
entirely free of frontier-model quota.

Targets (set the env for the ones you use; a target is "configured" once its vars exist):
    discord    DISCORD_WEBHOOK_URL
    slack      SLACK_WEBHOOK_URL
    telegram   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   (chat id overridable per call via `to`)
    linear     LINEAR_API_KEY                         (per call: to=<issue id or identifier, e.g. HAL-123>)
    github     GITHUB_TOKEN                           (per call: to=<owner/repo#number>)
    webhook    WEBHOOK_URL (+ optional WEBHOOK_AUTH header value)  — generic; covers n8n, etc.

Extra / named targets: set <NAME>_KIND=<one of the kinds above> plus that kind's vars under
the <NAME>_ prefix, e.g. DEVLOG_KIND=discord and DEVLOG_WEBHOOK_URL=... → post(target="devlog").
This lets you have several Discord channels, two Linear workspaces, and so on.

Safety:
    * Destination URLs and credentials are read ONLY from the environment, never from tool
      arguments — a caller can choose among the targets you configured but cannot post to an
      arbitrary URL.
    * Every tool accepts dry_run=true to render the exact request (secrets redacted) without
      sending.
    * Secrets are never echoed back in results, previews, or the targets listing.

Configuration:
    POST_TIMEOUT     request timeout in seconds (default: 30)
    POST_MAX_CHARS   max chars read from a `path` body (default: 100000)

Run:
    uv run post_mcp.py          # self-installs deps via the inline metadata above
    # or: pip install mcp httpx && python post_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Annotated, List, Optional
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field

TIMEOUT: float = float(os.environ.get("POST_TIMEOUT", "30"))
MAX_BODY_CHARS: int = int(os.environ.get("POST_MAX_CHARS", "100000"))

# A target NAME maps to a kind via <NAME>_KIND; if unset, the name itself when it is a
# built-in kind, otherwise 'webhook'. Each kind has its own adapter below.
BUILTIN_KINDS = ("discord", "slack", "telegram", "linear", "github", "webhook")

# Per-kind soft length limits (None = no cap enforced here).
LIMITS = {"discord": 2000, "telegram": 4096}

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
IDENT_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)-(\d+)")
GH_RE = re.compile(r"([^/\s]+)/([^#\s]+)#(\d+)")

mcp = FastMCP("llm_post_mcp")


# --- Helpers -----------------------------------------------------------------

def _prefix(name: str) -> str:
    """Env-var prefix for a target name, e.g. 'my-bot' -> 'MY_BOT'."""
    return re.sub(r"[^A-Z0-9]", "_", name.upper())


def _kind_of(name: str) -> str:
    """Resolve a target name to its adapter kind."""
    explicit = os.environ.get(f"{_prefix(name)}_KIND")
    if explicit:
        return explicit.lower()
    low = name.lower()
    return low if low in BUILTIN_KINDS else "webhook"


def _compose(title: Optional[str], text: str, *, markdown: bool) -> str:
    """Prepend an optional title as a heading."""
    if not title:
        return text
    return f"**{title}**\n\n{text}" if markdown else f"{title}\n\n{text}"


def _truncate(text: str, limit: Optional[int]) -> str:
    """Trim to `limit` chars with a visible marker (never silently drop content)."""
    if not limit or len(text) <= limit:
        return text
    marker = f"\n…[truncated {len(text) - limit} chars]"
    return text[: max(0, limit - len(marker))] + marker


def _resolve_body(text: Optional[str], path: Optional[str]) -> str:
    """Return the message body from exactly one of `text` or `path` (a single local file)."""
    if path:
        if text:
            raise ValueError("provide either text or path, not both.")
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise ValueError(f"could not read '{path}': {e}") from e
        if len(content) > MAX_BODY_CHARS:
            raise ValueError(
                f"body from '{path}' exceeds {MAX_BODY_CHARS} chars. Shorten it or raise POST_MAX_CHARS."
            )
        return content
    if text:
        return text
    raise ValueError("provide either text or path.")


def _redact_url(url: Optional[str]) -> Optional[str]:
    """Keep scheme + host only; webhook tokens live in the path/query, so drop those."""
    if not url:
        return None
    try:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}/…"
    except ValueError:
        return "…"


def _preview(kind: str, method: str, url: str, body: object, headers: Optional[dict] = None) -> dict:
    """Render a dry-run request with secrets redacted."""
    safe_headers = {
        k: ("…redacted…" if k.lower() in ("authorization", "x-api-key") else v)
        for k, v in (headers or {}).items()
    }
    return {
        "dry_run": True,
        "kind": kind,
        "method": method,
        "url": _redact_url(url),
        "headers": safe_headers or None,
        "body": body,
    }


def _http_err(e: httpx.HTTPStatusError) -> str:
    code = e.response.status_code
    body = (e.response.text or "")[:300]
    if code in (401, 403):
        return f"Error: {code} authentication failure — check this target's token/key (and its scopes)."
    if code == 404:
        return "Error: 404 — endpoint or resource not found. Check the webhook URL / issue reference."
    if code == 429:
        return "Error: 429 rate-limited — back off and retry, or post to a different target."
    return f"Error: HTTP {code}. Body: {body}"


def _net_err(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return f"Error: request timed out after {TIMEOUT:.0f}s."
    return "Error: could not reach the target endpoint. Check the URL and your network."


async def _post_json(url: str, headers: Optional[dict], json_body: object) -> httpx.Response:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=json_body)
        resp.raise_for_status()
        return resp


# --- Target adapters ---------------------------------------------------------
# Each takes a uniform signature and returns a normalized result dict. Adapters read
# their own config from the environment by prefix; they must check dry_run BEFORE any
# network call.

async def _send_discord(name, prefix, text, to, title, username, dry_run):
    url = os.environ.get(f"{prefix}_WEBHOOK_URL")
    if not url:
        raise ValueError(f"target '{name}' (discord) needs {prefix}_WEBHOOK_URL set to a Discord webhook URL.")
    content = _truncate(_compose(title, text, markdown=True), LIMITS["discord"])
    body: dict = {"content": content}
    if username:
        body["username"] = username
    send_url = url + ("&" if "?" in url else "?") + "wait=true"
    if dry_run:
        return _preview("discord", "POST", send_url, body)
    resp = await _post_json(send_url, None, body)
    ctype = resp.headers.get("content-type", "")
    data = resp.json() if ctype.startswith("application/json") else {}
    return {"ok": True, "target": name, "kind": "discord", "status": resp.status_code, "id": data.get("id")}


async def _send_slack(name, prefix, text, to, title, username, dry_run):
    url = os.environ.get(f"{prefix}_WEBHOOK_URL")
    if not url:
        raise ValueError(f"target '{name}' (slack) needs {prefix}_WEBHOOK_URL set to a Slack incoming-webhook URL.")
    body: dict = {"text": _compose(title, text, markdown=True)}
    if username:
        body["username"] = username
    if dry_run:
        return _preview("slack", "POST", url, body)
    resp = await _post_json(url, None, body)
    return {"ok": True, "target": name, "kind": "slack", "status": resp.status_code}


async def _send_telegram(name, prefix, text, to, title, username, dry_run):
    token = os.environ.get(f"{prefix}_BOT_TOKEN")
    chat = to or os.environ.get(f"{prefix}_CHAT_ID")
    if not token:
        raise ValueError(f"target '{name}' (telegram) needs {prefix}_BOT_TOKEN.")
    if not chat:
        raise ValueError(f"target '{name}' (telegram) needs a chat id: pass to=... or set {prefix}_CHAT_ID.")
    content = _truncate(_compose(title, text, markdown=False), LIMITS["telegram"])
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = {"chat_id": chat, "text": content}
    if dry_run:
        return _preview("telegram", "POST", url, {"chat_id": chat, "text": content})
    resp = await _post_json(url, None, body)
    data = resp.json()
    return {
        "ok": bool(data.get("ok")),
        "target": name,
        "kind": "telegram",
        "status": resp.status_code,
        "id": (data.get("result") or {}).get("message_id"),
    }


async def _linear_resolve(api: str, headers: dict, to: str) -> str:
    """Resolve a Linear issue UUID or human identifier (e.g. HAL-123) to its UUID."""
    if UUID_RE.fullmatch(to):
        return to
    m = IDENT_RE.fullmatch(to.strip())
    if not m:
        raise ValueError(f"'{to}' is not a Linear issue UUID or identifier (e.g. HAL-123).")
    team_key, number = m.group(1).upper(), int(m.group(2))
    # team_key is [A-Za-z0-9]+ and number is an int — safe to inline (no injection surface).
    query = (
        '{ issues(filter: { team: { key: { eq: "%s" } }, number: { eq: %d } }, first: 1) '
        "{ nodes { id } } }" % (team_key, number)
    )
    resp = await _post_json(api, headers, {"query": query})
    data = resp.json()
    if data.get("errors"):
        raise ValueError(f"Linear lookup error: {json.dumps(data['errors'])[:300]}")
    nodes = (((data.get("data") or {}).get("issues") or {}).get("nodes")) or []
    if not nodes:
        raise ValueError(f"no Linear issue found for identifier '{to}'.")
    return nodes[0]["id"]


async def _send_linear(name, prefix, text, to, title, username, dry_run):
    key = os.environ.get(f"{prefix}_API_KEY")
    if not key:
        raise ValueError(f"target '{name}' (linear) needs {prefix}_API_KEY.")
    if not to:
        raise ValueError(f"target '{name}' (linear) needs to=<issue id or identifier, e.g. HAL-123>.")
    api = "https://api.linear.app/graphql"
    headers = {"Authorization": key, "Content-Type": "application/json"}
    body_md = _compose(title, text, markdown=True)
    if dry_run:
        return _preview("linear", "POST", api, {"issue": to, "body": body_md}, headers)
    issue_id = await _linear_resolve(api, headers, to)
    mutation = (
        "mutation($id:String!,$body:String!)"
        "{ commentCreate(input:{issueId:$id,body:$body}) { success comment { id url } } }"
    )
    resp = await _post_json(api, headers, {"query": mutation, "variables": {"id": issue_id, "body": body_md}})
    data = resp.json()
    if data.get("errors"):
        raise ValueError(f"Linear API error: {json.dumps(data['errors'])[:300]}")
    cc = (data.get("data") or {}).get("commentCreate") or {}
    if not cc.get("success"):
        raise ValueError(f"Linear commentCreate did not succeed: {json.dumps(data)[:300]}")
    comment = cc.get("comment") or {}
    return {
        "ok": True,
        "target": name,
        "kind": "linear",
        "status": resp.status_code,
        "url": comment.get("url"),
        "id": comment.get("id"),
    }


async def _send_github(name, prefix, text, to, title, username, dry_run):
    token = os.environ.get(f"{prefix}_TOKEN")
    if not token:
        raise ValueError(f"target '{name}' (github) needs {prefix}_TOKEN.")
    if not to:
        raise ValueError(f"target '{name}' (github) needs to=<owner/repo#number>.")
    m = GH_RE.fullmatch(to.strip())
    if not m:
        raise ValueError(f"github target needs to='owner/repo#number', got '{to}'.")
    owner, repo, number = m.group(1), m.group(2), m.group(3)
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body_md = _compose(title, text, markdown=True)
    if dry_run:
        return _preview("github", "POST", url, {"body": body_md}, headers)
    resp = await _post_json(url, headers, {"body": body_md})
    data = resp.json()
    return {
        "ok": True,
        "target": name,
        "kind": "github",
        "status": resp.status_code,
        "url": data.get("html_url"),
        "id": data.get("id"),
    }


async def _send_webhook(name, prefix, text, to, title, username, dry_run):
    url = os.environ.get(f"{prefix}_URL")
    if not url:
        raise ValueError(
            f"target '{name}' (webhook) needs {prefix}_URL set to the endpoint (optionally {prefix}_AUTH)."
        )
    headers = {"Content-Type": "application/json"}
    auth = os.environ.get(f"{prefix}_AUTH")
    if auth:
        headers["Authorization"] = auth
    body: dict = {"text": text}
    if title:
        body["title"] = title
    if dry_run:
        return _preview("webhook", "POST", url, body, headers)
    resp = await _post_json(url, headers, body)
    detail = (resp.text or "")[:200]
    return {"ok": True, "target": name, "kind": "webhook", "status": resp.status_code, "detail": detail or None}


SENDERS = {
    "discord": _send_discord,
    "slack": _send_slack,
    "telegram": _send_telegram,
    "linear": _send_linear,
    "github": _send_github,
    "webhook": _send_webhook,
}


async def _dispatch(target, text, to, title, username, dry_run) -> dict:
    """Route to the right adapter and normalize all failures into an {error: 'Error: ...'} dict."""
    kind = _kind_of(target)
    sender = SENDERS.get(kind)
    if not sender:
        return {
            "ok": False,
            "target": target,
            "error": f"Error: unknown kind '{kind}' for target '{target}'. "
            f"Set {_prefix(target)}_KIND to one of {list(BUILTIN_KINDS)}.",
        }
    try:
        return await sender(target, _prefix(target), text, to, title, username, dry_run)
    except ValueError as e:
        return {"ok": False, "target": target, "error": f"Error: {e}"}
    except httpx.HTTPStatusError as e:
        return {"ok": False, "target": target, "error": _http_err(e)}
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return {"ok": False, "target": target, "error": _net_err(e)}
    except Exception as e:  # never raise to the client
        return {"ok": False, "target": target, "error": f"Error: unexpected {type(e).__name__}: {e}"}


def _describe(name: str) -> dict:
    """Report a target's kind, whether it's configured, and its (redacted) destination."""
    prefix = _prefix(name)
    kind = _kind_of(name)
    info: dict = {"name": name, "kind": kind, "configured": False, "destination": None, "needs": []}
    if kind in ("discord", "slack"):
        url = os.environ.get(f"{prefix}_WEBHOOK_URL")
        info["needs"] = [f"{prefix}_WEBHOOK_URL"]
        info["configured"], info["destination"] = bool(url), _redact_url(url)
    elif kind == "webhook":
        url = os.environ.get(f"{prefix}_URL")
        info["needs"] = [f"{prefix}_URL", f"{prefix}_AUTH (optional)"]
        info["configured"], info["destination"] = bool(url), _redact_url(url)
    elif kind == "telegram":
        info["needs"] = [f"{prefix}_BOT_TOKEN", f"{prefix}_CHAT_ID (or per-call to=)"]
        info["configured"] = bool(os.environ.get(f"{prefix}_BOT_TOKEN"))
        info["destination"] = "api.telegram.org" if info["configured"] else None
    elif kind == "linear":
        info["needs"] = [f"{prefix}_API_KEY", "per-call to=<issue id/identifier>"]
        info["configured"] = bool(os.environ.get(f"{prefix}_API_KEY"))
        info["destination"] = "api.linear.app" if info["configured"] else None
    elif kind == "github":
        info["needs"] = [f"{prefix}_TOKEN", "per-call to=<owner/repo#number>"]
        info["configured"] = bool(os.environ.get(f"{prefix}_TOKEN"))
        info["destination"] = "api.github.com" if info["configured"] else None
    else:
        info["needs"] = [f"{prefix}_KIND=<discord|slack|telegram|linear|github|webhook>"]
    return info


# --- Tools -------------------------------------------------------------------

_TARGET_HELP = (
    "Destination name. Built-ins: discord, slack, telegram, linear, github, webhook. "
    "Or any custom name you configured via <NAME>_KIND. Only configured targets work; "
    "URLs/secrets come from the environment, never from this argument."
)
_PATH_HELP = (
    "Optional path to a local file whose contents become the message body (read by the "
    "server, so the body need not pass through the caller's tokens). Provide this OR text."
)
_DRY_HELP = "If true, return the exact request (secrets redacted) without sending it."


@mcp.tool(
    name="post",
    annotations={
        "title": "Post a message to an outbound target",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def post(
    target: Annotated[str, Field(description=_TARGET_HELP, min_length=1)],
    text: Annotated[Optional[str], Field(description="The message body. Provide this or `path`.")] = None,
    path: Annotated[Optional[str], Field(description=_PATH_HELP)] = None,
    to: Annotated[Optional[str], Field(description="Destination within the target: Linear issue id/identifier (HAL-123), GitHub owner/repo#number, or a Telegram chat id. Ignored for webhook/discord/slack.")] = None,
    title: Annotated[Optional[str], Field(description="Optional heading prepended to the body where the target supports it.")] = None,
    username: Annotated[Optional[str], Field(description="Optional sender name override (Discord/Slack webhooks only).")] = None,
    dry_run: Annotated[bool, Field(description=_DRY_HELP)] = False,
) -> str:
    """Send a message to one configured outbound target (Discord, Slack, Telegram, Linear, GitHub, or webhook).

    This has an external side effect. Destination URLs and credentials are taken only from the
    server's environment, so you can reach the targets the operator configured but not arbitrary
    URLs. Use dry_run=true first when the wording or destination is unconfirmed.

    Returns:
        str: a JSON result (with a delivery url/id where the target provides one), or an
        'Error: ...' string.
    """
    try:
        body = _resolve_body(text, path)
    except ValueError as e:
        return f"Error: {e}"
    result = await _dispatch(target, body, to, title, username, dry_run)
    if result.get("error"):
        return result["error"]
    return json.dumps(result, indent=2)


@mcp.tool(
    name="post_many",
    annotations={
        "title": "Fan a message out to several targets",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def post_many(
    targets: Annotated[List[str], Field(description="Target names to deliver the same body to.", min_length=1, max_length=20)],
    text: Annotated[Optional[str], Field(description="The message body. Provide this or `path`.")] = None,
    path: Annotated[Optional[str], Field(description=_PATH_HELP)] = None,
    title: Annotated[Optional[str], Field(description="Optional heading prepended to the body where supported.")] = None,
    dry_run: Annotated[bool, Field(description=_DRY_HELP)] = False,
) -> str:
    """Deliver the same body to several targets at once (e.g. announce to Discord + Slack + a webhook).

    Per-target `to` is not supported here, so it suits webhook/discord/slack/telegram targets;
    for Linear/GitHub (which need an issue reference) use `post`.

    Returns:
        str: a JSON object mapping each target to its result or 'Error: ...'.
    """
    try:
        body = _resolve_body(text, path)
    except ValueError as e:
        return f"Error: {e}"
    results = await asyncio.gather(*(_dispatch(t, body, None, title, None, dry_run) for t in targets))
    return json.dumps({t: r for t, r in zip(targets, results)}, indent=2)


@mcp.tool(
    name="targets",
    annotations={
        "title": "List configured outbound targets",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def targets() -> str:
    """List the built-in and custom targets, whether each is configured, and what each still needs.

    Secrets are never shown — only redacted destination hosts. Call this before posting when you
    are unsure a target is set up.

    Returns:
        str: a JSON array of target descriptors.
    """
    names = list(BUILTIN_KINDS)
    for key in os.environ:
        if key.endswith("_KIND"):
            custom = key[:-5].lower()
            if custom and custom not in names:
                names.append(custom)
    return json.dumps([_describe(n) for n in names], indent=2)


if __name__ == "__main__":
    mcp.run()
