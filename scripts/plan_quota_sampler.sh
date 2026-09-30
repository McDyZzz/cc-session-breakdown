#!/bin/bash
# Scope: Stop hook that asks session_breakdown_report.py for a plan-quota sample (`/usage`).
# Called by the plugin's Stop hook (hooks/hooks.json). `--force` skips the 5-minute throttle.
# Reads only the sampling switch in <data dir>/config.json and writes nothing; the Python
# side locks, throttles, samples, and records.
#
# The hook returns at once: it starts `session_breakdown_report.py --sample-if-due` detached.
# That process takes an exclusive flock (the kernel frees it if the process dies), checks
# the 5-minute stamp (`--force` skips it), runs `claude -p "/usage"`, and records the result.
# CCSB_SAMPLING=1 marks the inner `claude -p` run, so its own Stop hook exits here.
# "sampling": false in config.json (written by `ccsb --sampling off`) also exits here, before
# Python starts. The data dir rule matches the Python side: $CCSB_DATA_DIR when set and
# non-empty, else ~/.claude/cc-session-breakdown. The regex tolerates any JSON spacing.

[ "${CCSB_SAMPLING:-}" = "1" ] && exit 0

config="${CCSB_DATA_DIR:-$HOME/.claude/cc-session-breakdown}/config.json"
off_re='"sampling"[[:space:]]*:[[:space:]]*false'
if [ -r "$config" ] && [[ "$(<"$config")" =~ $off_re ]]; then
  exit 0
fi

# Resolve this script's real directory (the plugin folder may be a symlink).
self="${BASH_SOURCE[0]}"
while [ -L "$self" ]; do
  dir="$(cd -P "$(dirname "$self")" && pwd)"
  self="$(readlink "$self")"
  case "$self" in /*) ;; *) self="$dir/$self" ;; esac
done
REPORT="$(cd -P "$(dirname "$self")" && pwd)/session_breakdown_report.py"

force=""
[ "${1:-}" = "--force" ] && force="--force"
# The job leaves the hook's session: setsid(1) when present; else perl's POSIX setsid,
# since macOS lacks setsid(1); else plain nohup in the background.
job=(/usr/bin/env python3 "$REPORT" --sample-if-due $force)
if command -v setsid >/dev/null 2>&1; then
  nohup setsid "${job[@]}" </dev/null >/dev/null 2>&1 &
elif command -v perl >/dev/null 2>&1; then
  nohup perl -e 'use POSIX qw(setsid); setsid(); exec @ARGV' \
    "${job[@]}" </dev/null >/dev/null 2>&1 &
else
  nohup "${job[@]}" </dev/null >/dev/null 2>&1 &
fi
exit 0
