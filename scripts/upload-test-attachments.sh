#!/usr/bin/env bash
# Upload test evidence files as attachments to Azure DevOps Work Items
# Then remove local folders on success.

set -euo pipefail

# --- Config ---
ORG_URL="https://dev.azure.com/tr-ggo"
PROJECT="Mastersaf%20Fiscal%20Solutions"
API_VER="api-version=7.0"
TESTS_DIR="C:/Claude/Taxone_support/taxone-support-dev/tests"

# --- Auth ---
if [[ -z "${ADO_PAT:-}" ]]; then
  echo "ERRO: \$ADO_PAT nao esta definida. Abortando."
  exit 1
fi
AUTH=$(printf '%s' ":$ADO_PAT" | base64 -w0)

# --- Counters ---
total_files=0
ok_files=0
fail_files=0
ok_wis=0
fail_wis=0

# --- Process each WI directory ---
for wi_dir in "$TESTS_DIR"/*/; do
  [[ -d "$wi_dir" ]] || continue
  WI_ID=$(basename "$wi_dir")
  echo ""
  echo "========== WI $WI_ID =========="

  wi_ok=true

  for filepath in "$wi_dir"*; do
    [[ -f "$filepath" ]] || continue
    filename=$(basename "$filepath")
    total_files=$((total_files + 1))

    # Step 1: Upload blob
    encoded_name=$(python -c "import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))" "$filename")

    upload_response=$(curl -s -w "\n%{http_code}" --max-time 30 \
      -X POST \
      -H "Authorization: Basic $AUTH" \
      -H "Content-Type: application/octet-stream" \
      --data-binary "@$filepath" \
      "${ORG_URL}/${PROJECT}/_apis/wit/attachments?fileName=${encoded_name}&${API_VER}")

    http_code=$(echo "$upload_response" | tail -1)
    body=$(echo "$upload_response" | sed '$d')

    if [[ "$http_code" != "201" ]]; then
      echo "  [FAIL] $filename — upload retornou HTTP $http_code"
      wi_ok=false
      fail_files=$((fail_files + 1))
      continue
    fi

    att_url=$(echo "$body" | python -c "import sys,json; print(json.load(sys.stdin)['url'])" 2>/dev/null)
    if [[ -z "$att_url" ]]; then
      echo "  [FAIL] $filename — nao conseguiu extrair URL do attachment"
      wi_ok=false
      fail_files=$((fail_files + 1))
      continue
    fi

    # Step 2: Link attachment to Work Item
    patch_body=$(python -c "
import json, sys
print(json.dumps([{
    'op': 'add',
    'path': '/relations/-',
    'value': {
        'rel': 'AttachedFile',
        'url': sys.argv[1],
        'attributes': {'comment': 'Evidencia de teste'}
    }
}]))
" "$att_url")

    link_response=$(curl -s -w "\n%{http_code}" --max-time 30 \
      -X PATCH \
      -H "Authorization: Basic $AUTH" \
      -H "Content-Type: application/json-patch+json" \
      -d "$patch_body" \
      "${ORG_URL}/${PROJECT}/_apis/wit/workitems/${WI_ID}?${API_VER}")

    link_code=$(echo "$link_response" | tail -1)

    if [[ "$link_code" != "200" ]]; then
      echo "  [FAIL] $filename — link retornou HTTP $link_code"
      wi_ok=false
      fail_files=$((fail_files + 1))
      continue
    fi

    echo "  [OK]   $filename"
    ok_files=$((ok_files + 1))
  done

  # Step 3: Remove local folder only if all files succeeded
  if $wi_ok; then
    rm -rf "$wi_dir"
    echo "  -> Pasta removida."
    ok_wis=$((ok_wis + 1))
  else
    echo "  -> Pasta PRESERVADA (houve falhas)."
    fail_wis=$((fail_wis + 1))
  fi
done

# --- Summary ---
echo ""
echo "=============================="
echo "  RESUMO"
echo "=============================="
echo "  Arquivos: $ok_files OK / $fail_files FAIL (total: $total_files)"
echo "  WIs:      $ok_wis OK / $fail_wis FAIL"
echo "=============================="

if [[ $fail_files -gt 0 ]]; then
  echo ""
  echo "Pastas restantes em $TESTS_DIR:"
  ls "$TESTS_DIR" 2>/dev/null || echo "  (vazio)"
  exit 1
fi
