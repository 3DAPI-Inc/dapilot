#!/bin/bash
# Generate openapi.json from the 3DAPI public API without a database or AWS.
# Usage: dump-openapi.sh <path-to-3dapi/public-api/v1> <out.json>
set -euo pipefail
API="$1"; OUT="$2"; HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$API"
KEYS=$(grep -rhoE "(getOrThrow|\.get)(<[^>]*>)?\(\s*['\"][A-Z][A-Z0-9_]+['\"]" src | grep -oE "[A-Z][A-Z0-9_]+" | sort -u)
ENVARGS=()
for k in $KEYS; do
  case $k in
    *URL*) v="http://127.0.0.1:1/x" ;;
    *SECRET*) v="dummy-secret-for-offline-openapi-dump-0123456789" ;;
    ENVIRONMENT) v="development" ;;
    *ENABLED) v="false" ;;
    *) v="x" ;;
  esac
  ENVARGS+=("$k=$v")
done
env NODE_PATH="$PWD/node_modules" "${ENVARGS[@]}" \
  DATABASE_URL=postgres://u:p@127.0.0.1:1/x \
  PUBLIC_ID_SECRET=dummy-secret-for-offline-openapi-dump-0123456789 \
  NODE_ENV=development AWS_REGION=eu-central-1 \
  ./node_modules/.bin/ts-node --project tsconfig.json --transpile-only -r tsconfig-paths/register "$HERE/dump-openapi.ts" "$OUT"
