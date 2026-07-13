#!/usr/bin/env bash
set -euo pipefail

fail=0

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  tracked_candidates=$(git ls-files --cached --others --exclude-standard)
else
  tracked_candidates=$(find . -type f     -not -path './.git/*'     -not -name '.env'     -not -name '.env.*'     -not -name 'docker-compose.override.yml'     -not -path './data/profile/*'     -not -path './data/now/*'     -not -path './outputs/*'     -not -path './temp/*'     -not -path './.tools/*'     -not -path './.venv/*'     -not -path './.venv-extract/*'     -not -path './frontend/node_modules/*'     | sed 's#^./##')
fi

if printf '%s
' "$tracked_candidates" | grep -E '(^|/)\.env($|\.)' | grep -Ev '\.env(\.stateful\.production)?\.example$' >/dev/null; then
  echo 'ERROR: local .env file would be included.' >&2
  fail=1
fi

if printf '%s
' "$tracked_candidates" | grep -E '(^|/)data/(profile|now)/' >/dev/null; then
  echo 'ERROR: private data directory would be included.' >&2
  fail=1
fi

generated_candidates=$(printf '%s
' "$tracked_candidates" | grep -E '(^|/)(outputs|temp)/' | grep -v '^outputs/\.gitkeep$' || true)
if [ -n "$generated_candidates" ]; then
  echo 'ERROR: generated output/temp directory would be included.' >&2
  printf '%s
' "$generated_candidates" >&2
  fail=1
fi

scan_paths=$(printf '%s
' "$tracked_candidates" | grep -E '\.(py|ts|tsx|js|json|md|yml|yaml|example|css|html|txt|sh)$' || true)
if [ -n "$scan_paths" ]; then
  secret_pattern='sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|xox[baprs]-[A-Za-z0-9-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}|BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY|TELEGRAM_BOT_TOKEN=[0-9]{6,}:[A-Za-z0-9_-]{20,}'
  if printf '%s
' "$scan_paths" | xargs grep -nE "$secret_pattern" 2>/dev/null; then
    echo 'ERROR: possible secret value found.' >&2
    fail=1
  fi

  scan_without_audit=$(printf '%s
' "$scan_paths" | grep -v '^scripts/publish_audit.sh$' || true)
  personal_pattern='G''uy[[:space:]]*V''initzky|G''uyV''initzky|T''echnion|D''ean|91''\.3|87''\.5|93''\.6|90''\.8'
  if [ -n "$scan_without_audit" ] && printf '%s
' "$scan_without_audit" | xargs grep -nE "$personal_pattern" 2>/dev/null; then
    echo 'ERROR: personal publish-blocking string found in public candidate files.' >&2
    fail=1
  fi
fi

if [ "$fail" -eq 0 ]; then
  echo 'Publish audit passed.'
fi

exit "$fail"
