#!/usr/bin/env bash
# Release the plugin through GitHub: bump the version, validate, commit, tag, push.
#
#   scripts/dev/release.sh                 patch bump (0.1.12 -> 0.1.13)
#   scripts/dev/release.sh minor|major     0.1.12 -> 0.2.0 | 1.0.0
#   scripts/dev/release.sh 0.3.0           an exact version
#   scripts/dev/release.sh ... --no-push   commit and tag only
#
# Why the bump matters: plugin.json carries a `version`, and Claude Code only
# delivers an update to installed copies when that string changes. Pushing
# commits without a bump leaves every user on the old copy and
# `claude plugin update dapilot@dapilot` reports "already at the latest version".
#
# The release commit holds the bump alone, so the tree must be clean: commit the
# work first, then release. The tag is v<version> on that commit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MANIFEST="$ROOT/plugins/dapilot/.claude-plugin/plugin.json"
cd "$ROOT"

PUSH=1; PART="patch"
for a in "$@"; do
  case "$a" in
    --no-push) PUSH=0 ;;
    patch|minor|major) PART="$a" ;;
    [0-9]*.[0-9]*.[0-9]*) PART="$a" ;;
    -h|--help) sed -n 2,16p "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

command -v claude >/dev/null || { echo "claude is not on PATH" >&2; exit 2; }
if [ -n "$(git status --porcelain)" ]; then
  echo "the working tree is not clean; commit or stash first so the release commit holds only the bump" >&2
  git status --short | head -20 >&2
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
OLD="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" "$MANIFEST")"
NEW="$(python3 - "$OLD" "$PART" <<'EOF'
import sys
old, part = sys.argv[1], sys.argv[2]
a, b, c = (int(x) for x in old.split("."))
if part == "patch": c += 1
elif part == "minor": b, c = b + 1, 0
elif part == "major": a, b, c = a + 1, 0, 0
else:
    a, b, c = (int(x) for x in part.split("."))
    if (a, b, c) <= tuple(int(x) for x in old.split(".")):
        sys.exit(f"{part} is not above the current {old}")
print(f"{a}.{b}.{c}")
EOF
)"

if git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null; then
  echo "tag v$NEW already exists" >&2; exit 1
fi

python3 - "$MANIFEST" "$NEW" <<'EOF'
import json, sys
p, v = sys.argv[1], sys.argv[2]
m = json.load(open(p)); m["version"] = v
with open(p, "w") as f:
    json.dump(m, f, indent=2); f.write("\n")
EOF

claude plugin validate --strict plugins/dapilot >/dev/null || { git checkout -- "$MANIFEST"; echo "plugin validation failed; run: claude plugin validate --strict plugins/dapilot" >&2; exit 1; }
claude plugin validate --strict . >/dev/null || { git checkout -- "$MANIFEST"; echo "marketplace validation failed; run: claude plugin validate --strict ." >&2; exit 1; }

git add "$MANIFEST"
git commit -q -m "dapilot $NEW"
git tag -a "v$NEW" -m "dapilot $NEW"
echo "dapilot $OLD -> $NEW on $BRANCH, tag v$NEW"

if [ "$PUSH" = 1 ]; then
  git push origin "$BRANCH" "v$NEW"
  echo "pushed. Installed copies pick it up with: claude plugin update dapilot@dapilot"
else
  echo "not pushed. When ready: git push origin $BRANCH v$NEW"
fi

# Refresh a CLI install made from this checkout so the next session loads these files.
if claude plugin list --json 2>/dev/null | grep -q '"id": *"dapilot@dapilot"'; then
  claude plugin update dapilot@dapilot 2>&1 | tail -1
fi
