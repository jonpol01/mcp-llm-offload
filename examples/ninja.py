#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "httpx>=0.27"]
# ///
"""ninja.py — the autonomous offload→post loop (Scenario C): local model drafts, script posts.

There is NO Claude and NO MCP client here. MCP is only the wrapper that lets Claude call these
tools interactively; for an unattended loop you import the two servers as plain libraries and call
their functions directly. The SCRIPT is the orchestrator (the role Claude plays in a chat); the
local model only does the drafting. Run it from cron/launchd on hermes for a status pipeline that
costs zero frontier-model tokens.

    input (gathered locally) → llm_offload_mcp.summarize (local LLM) → post_mcp.post (deliver)

Env:
    offload side : LLM_PROVIDER (default lmstudio), LLM_MODEL, LMSTUDIO_BASE_URL
    deliver side : the target's vars, e.g. DISCORD_WEBHOOK_URL / LINEAR_API_KEY+to / GITHUB_TOKEN
    this script  : NINJA_TARGET (default 'discord'), NINJA_TO (for linear/github), NINJA_DRY_RUN=1
"""
import asyncio
import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


offload = _load("llm_offload_mcp")   # the read-only drafting server, used as a library
post = _load("post_mcp")             # the delivery server, used as a library

TARGET = os.environ.get("NINJA_TARGET", "discord")
TO = os.environ.get("NINJA_TO")      # issue ref for linear/github targets
DRY = os.environ.get("NINJA_DRY_RUN") == "1"


async def main() -> int:
    # 1. Gather input locally. The big payload never leaves the box (and never costs frontier tokens).
    log = subprocess.run(["git", "-C", ROOT, "log", "--oneline", "-10"],
                         capture_output=True, text=True).stdout.strip()
    if not log:
        print("nothing to report")
        return 0

    # 2. The local model drafts — reusing the offload server's `summarize`, pointed at your LM Studio.
    summary = await offload.summarize(text=log, max_words=60, style="a 2-sentence devlog update")
    if summary.startswith("Error:"):
        print("draft failed:", summary)
        return 1
    print("── drafted by local model ──\n" + summary + "\n")

    # 3. Deliver — reusing post_mcp's adapters (dry_run-safe, secrets read only from env).
    result = await post.post(target=TARGET, text=summary, title="devlog", to=TO, dry_run=DRY)
    print("── delivery ──\n" + result)
    return 0 if not result.startswith("Error:") else 1


sys.exit(asyncio.run(main()))
