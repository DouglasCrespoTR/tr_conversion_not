#!/usr/bin/env bash
# Pre-commit hook: blocks commits containing hardcoded secrets.
# Install: cp scripts/pre-commit-secrets.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
# Or:      git config core.hooksPath scripts/hooks/

set -euo pipefail

# Patterns that indicate hardcoded credentials (applied to staged diff only)
PATTERNS=(
  'password\s*=\s*"[^"]{3,}"'
  "password\s*=\s*'[^']{3,}'"
  'passwd\s*=\s*"[^"]{3,}"'
  "passwd\s*=\s*'[^']{3,}'"
  'secret\s*=\s*"[^"]{3,}"'
  "secret\s*=\s*'[^']{3,}'"
  'api_key\s*=\s*"[^"]{3,}"'
  "api_key\s*=\s*'[^']{3,}'"
  'apikey\s*=\s*"[^"]{3,}"'
  "apikey\s*=\s*'[^']{3,}'"
  'token\s*=\s*"[A-Za-z0-9+/=_-]{20,}"'
  "token\s*=\s*'[A-Za-z0-9+/=_-]{20,}'"
  'Bearer [A-Za-z0-9+/=_-]{20,}'
  'Basic [A-Za-z0-9+/=]{20,}'
  'PRIVATE KEY-----'
)

# Files/patterns to skip (these legitimately mention "password" as variable names)
SKIP_FILES=("*.md" "*.example" "*.sample" "*.txt" "pre-commit-secrets.sh" "validate_agents.py")

FOUND=0

# Build grep exclude args
EXCLUDE_ARGS=""
for skip in "${SKIP_FILES[@]}"; do
  EXCLUDE_ARGS="$EXCLUDE_ARGS -- ':!$skip'"
done

# Get staged diff (only added/modified lines)
DIFF=$(git diff --cached --diff-filter=ACMR -U0 -- . ':!*.md' ':!*.example' ':!*.sample' ':!*.txt' ':!**/pre-commit-secrets.sh' ':!**/validate_agents.py' 2>/dev/null || true)

if [ -z "$DIFF" ]; then
  exit 0
fi

for pattern in "${PATTERNS[@]}"; do
  MATCHES=$(echo "$DIFF" | grep -inE "^\+" | grep -iE "$pattern" 2>/dev/null || true)
  if [ -n "$MATCHES" ]; then
    if [ "$FOUND" -eq 0 ]; then
      echo ""
      echo "==============================================="
      echo "  BLOCKED: Possible hardcoded secrets detected"
      echo "==============================================="
    fi
    FOUND=1
    echo ""
    echo "Pattern: $pattern"
    echo "$MATCHES" | head -5
  fi
done

if [ "$FOUND" -ne 0 ]; then
  echo ""
  echo "-----------------------------------------------"
  echo "Use environment variables instead of hardcoded values."
  echo "If this is a false positive, commit with: git commit --no-verify"
  echo "-----------------------------------------------"
  exit 1
fi

exit 0
