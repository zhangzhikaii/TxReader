#!/usr/bin/env bash
# PreToolUse guard for Bash: refuse shell commands that would touch the user's real data
# (the database and screenshots in data/, samples/, seed/, docs/example*.csv), or start the
# app / devtools against the default data directory. Exit 2 blocks the call; stderr goes to Claude.
cmd=$(jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

block() {
  echo "已拦截：这条命令会碰到真实数据（$1）。按 CLAUDE.md 的隐私规定，不要打开 data/、samples/、seed/、docs/example*.csv 或数据库；需要时先问用户。调试用临时目录：TXREADER_DATA=<临时目录>。" >&2
  exit 2
}

# Paths: data/ samples/ seed/ as a path or a bare word, with or without ./ or the repo prefix
if grep -qE '(^|[^A-Za-z0-9_.-])(\./)?(data|samples|seed)(/|$|[[:space:]"'"'"';|&)])' <<<"$cmd"; then
  block "data/、samples/ 或 seed/"
fi
grep -qE 'txreader\.db|\.webp\b' <<<"$cmd" && block "数据库或截图文件"
grep -qE 'example2?\.csv' <<<"$cmd" && block "docs/example*.csv"
grep -qE '(^|[^A-Za-z0-9_])sqlite3?([[:space:]]|$)' <<<"$cmd" && block "sqlite 命令行"

# The app and devtools open data/ unless TXREADER_DATA points elsewhere
if grep -qE 'uvicorn|txreader\.devtools|txreader/devtools' <<<"$cmd" && ! grep -qE 'TXREADER_DATA=' <<<"$cmd"; then
  block "没有设 TXREADER_DATA 就会用 data/ 里的真实数据库"
fi
exit 0
