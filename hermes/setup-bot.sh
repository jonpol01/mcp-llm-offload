#!/usr/bin/env bash
# Set up a dedicated Hermes profile as an offload bot: SOUL.md says what to do and what to
# hand back, deny-floor.txt blocks the commands no task should reach, and an API server key
# and port let the plugin reach it. Run it on the bot's machine, as the user the bot runs as.
#
#   hermes/setup-bot.sh <profile> --port <n> [--clone-from <profile>] [--host <addr>]
#       Create <profile> as a Hermes clone of your active profile, so it uses the LLM and
#       credentials your Hermes already has; --clone-from clones another profile instead.
#       Give it its own API key and port, install the rules, check them.
#       --host 0.0.0.0 when Claude Code runs on another machine.
#   hermes/setup-bot.sh <profile>            Reinstall the rules into an existing profile, check.
#   hermes/setup-bot.sh <profile> --check    Check only; changes nothing.
#
# Needs `hermes` and `python3` on PATH. Never the default profile: give the bot its own.
set -euo pipefail

kit=$(cd "$(dirname "$0")" && pwd)
usage="usage: setup-bot.sh <profile> [--port <n>] [--clone-from <profile>] [--host <addr>] [--check]"
profile=${1:?$usage}
shift
port="" clone_from="" host="" check_only=""
while [ $# -gt 0 ]; do
  case "$1" in
    --port) port=${2:?$usage}; shift 2 ;;
    --clone-from) clone_from=${2:?$usage}; shift 2 ;;
    --host) host=${2:?$usage}; shift 2 ;;
    --check) check_only=1; shift ;;
    *) echo "$usage" >&2; exit 2 ;;
  esac
done

die() { echo "$*" >&2; exit 1; }
hp() { hermes -p "$profile" "$@"; }

[ "$profile" != default ] || die "Give the bot its own profile rather than 'default': name a new one and this creates it."
case "$port" in ''|*[!0-9]*) [ -z "$port" ] || die "--port takes a number." ;; esac

if hp config path >/dev/null 2>&1; then
  [ -z "$clone_from" ] || die "Profile '$profile' already exists; --clone-from only applies when creating one."
  created=""
else
  [ -z "$check_only" ] || die "Profile '$profile' does not exist."
  [ -n "$port" ] || die "A new bot needs --port <n>, a port no other profile uses."
  created=1
fi

if [ -n "$port" ]; then
  # Two gateways on one port means one of them is silently not serving.
  root=$(dirname "$(hermes -p default config path)")
  for f in "$root/.env" "$root"/profiles/*/.env; do
    case "$f" in "$root/profiles/$profile/.env") continue ;; esac
    if grep -qx "API_SERVER_PORT=$port" "$f" 2>/dev/null; then
      die "Port $port is already used by $(dirname "$f"). Pick another."
    fi
  done
fi

if [ -n "$created" ]; then
  # Hermes' own clone: the bot starts with the LLM and credentials your Hermes already
  # uses, and Hermes leaves messaging channels behind. Nothing here picks a model.
  if [ -n "$clone_from" ]; then
    hermes profile create "$profile" --clone-from "$clone_from"
  else
    hermes profile create "$profile" --clone
  fi
fi

conf=$(hp config path)
dir=$(dirname "$conf")
envf=$(hp config env-path)

# Set KEY=VALUE in the profile's .env, replacing an existing line; the file stays 0600.
set_env() {
  python3 - "$envf" "$1" "$2" <<'PY'
import os, sys
path, key, value = sys.argv[1:]
lines = open(path).read().splitlines() if os.path.exists(path) else []
lines = [l for l in lines if not l.startswith(key + "=")] + [f"{key}={value}"]
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write("\n".join(lines) + "\n")
os.chmod(path, 0o600)
PY
}

if [ -z "$check_only" ]; then
  if ! grep -q '^API_SERVER_KEY=' "$envf" 2>/dev/null && { [ -n "$created" ] || [ -n "$port" ]; }; then
    set_env API_SERVER_KEY "$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
  [ -z "$port" ] || set_env API_SERVER_PORT "$port"
  [ -z "$host" ] || set_env API_SERVER_HOST "$host"

  if [ -f "$dir/SOUL.md" ] && ! cmp -s "$kit/SOUL.md" "$dir/SOUL.md"; then
    cp "$dir/SOUL.md" "$dir/SOUL.md.bak-$(date +%Y%m%d-%H%M%S)"
  fi
  cp "$kit/SOUL.md" "$dir/SOUL.md"
  # Keep the profile's own deny rules and append the floor's.
  merged=$(hp config get approvals.deny --json | python3 -c '
import json, sys
current = json.load(sys.stdin) or []
floor = [l.strip() for l in open(sys.argv[1]) if l.strip() and not l.lstrip().startswith("#")]
print(json.dumps(current + [r for r in floor if r not in current]))' "$kit/deny-floor.txt")
  hp config set approvals.deny "$merged" >/dev/null
  echo "Installed SOUL.md and the deny floor into $dir"
fi

# --- Check -------------------------------------------------------------------
# Hermes' own dry run decides each verdict; nothing below is executed. "ask" counts as
# blocked only while unattended_mode is deny: a job from the API has nobody to ask.
unattended=$(hp config get approvals.unattended_mode 2>/dev/null || echo deny)
fail=0

verdict() {
  local out
  out=$(hp approvals test --json -- "$1" 2>/dev/null) || true
  printf '%s' "$out" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("verdict", "?"))' 2>/dev/null || echo error
}
row() { printf '  %-4s %-13s %s\n' "$1" "$2" "$3"; }

echo
echo "Must be blocked:"
while IFS= read -r cmd; do
  v=$(verdict "$cmd")
  case "$v" in
    *deny*) row ok "$v" "$cmd" ;;
    ask*) if [ "$unattended" = approve ]; then row FAIL "$v" "$cmd"; fail=1; else row ok "$v" "$cmd"; fi ;;
    *) row FAIL "$v" "$cmd"; fail=1 ;;
  esac
done <<'EOF'
gh pr merge 1 --merge
gh pr merge 1 --auto --squash
gh pr review 1 --approve
git push origin main
git push -u origin main
git push origin main --quiet
git push origin HEAD:main
git push origin refs/heads/main
git push origin main && echo pushed
git push --force origin topic
git push origin +topic
git push origin --delete topic
git reset --hard HEAD~1
git rebase -i HEAD~3
gh api -X PUT repos/o/r/pulls/1/merge
gh api repos/o/r/pulls/1/merge -X PUT
gh api --method=PUT repos/o/r/pulls/1/merge
curl -X PUT https://api.github.com/repos/o/r/pulls/1/merge
gh api repos/o/r/pulls/1/reviews -f event=APPROVE
gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: "x"}) { clientMutationId } }'
gh api graphql -f query='mutation { addPullRequestReview(input: {pullRequestId: "x", event: APPROVE}) { clientMutationId } }'
gh alias set m "pr merge"
gh repo delete o/r --yes
gh repo archive o/r --yes
gh repo edit o/r --visibility public
gh api -X PATCH repos/o/r -f archived=true
gh api repos/o/r -X DELETE
gh release create v1.0.0
gh workflow run deploy.yml
gh run rerun 123
gh secret set TOKEN --body x
gh auth token
cat ~/.config/gh/hosts.yml
git commit -m fix -m "Co-Authored-By: Bot <bot@example.com>"
bash -c 'g=gh; $g pr merge 1'
EOF

echo
echo "Must still work:"
while IFS= read -r cmd; do
  v=$(verdict "$cmd")
  if [ "$v" = allow ]; then row ok "$v" "$cmd"; else row FAIL "$v" "$cmd"; fail=1; fi
done <<'EOF'
gh pr view 1
gh pr diff 1
gh pr checks 1
gh pr comment 1 --body "Looks good; one question inline"
gh pr create --title "docs: fix a typo" --body "Small fix"
gh issue comment 5 --body "We should prevent approving blindly"
gh issue edit 5 --add-label bug
gh api repos/o/r/issues/5/comments -f body=thanks
git push origin docs/fix-typo
git push origin maintenance
git commit -m "docs: fix a typo"
EOF

echo
if [ "$unattended" = approve ]; then
  echo "approvals.unattended_mode is 'approve': anything Hermes flags as dangerous runs"
  echo "unattended unless the floor denies it."
fi
if ! hp config get model.default >/dev/null 2>&1; then
  echo "No model is set yet, so the bot cannot answer. Set one: hermes -p $profile model"
fi
if [ -z "$check_only" ]; then
  p=$(sed -n 's/^API_SERVER_PORT=//p' "$envf" 2>/dev/null)
  h=$(sed -n 's/^API_SERVER_HOST=//p' "$envf" 2>/dev/null)
  case "$h" in 0.0.0.0|'::') h="<this machine's address>" ;; '') h=127.0.0.1 ;; esac
  echo
  echo "Next: start its gateway (hermes -p $profile gateway run, or install it as a service),"
  echo "then set the plugin's Hermes fields in Claude Code:"
  echo "  gateway URL  http://$h:${p:-8642}/v1"
  echo "  bot name     $profile"
  echo "  API key      the API_SERVER_KEY line in $envf"
fi
if [ "$fail" = 0 ]; then
  echo
  echo "All checks passed."
else
  echo "Some checks FAILED." >&2
  exit 1
fi
