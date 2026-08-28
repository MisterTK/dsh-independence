#!/usr/bin/env bash
# End-to-end smoke for independence.py against a fixture harness tree.
# Asserts the full lifecycle: init writes the patch and baseline, a clean tree
# audits green, injected egress audits red, accept blesses it, and a tampered
# patch is detected and repaired. Requires uv.
set -euo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/skills/dsh-independence/scripts/independence.py"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
REPO="$WORK/harness"
HOME_DIR="$WORK/dsh-home"

# ── fixture harness: the shape the auditor reads ─────────────────────────────
mkdir -p "$REPO/packages/bundle/base" "$REPO/packages/llm/thing/src" "$REPO/apps/cli/src"
# Version matches the shipped lockdown so the release-review check passes.
printf '{ "name": "fixture", "version": "0.1.2-alpha.1" }\n' > "$REPO/package.json"
cat > "$REPO/packages/bundle/base/cordis.patch.yml" <<'YML'
- insert:
    - id: session-telemetry-otel
      name: '@deepseek-ai/dsh-session-telemetry-otel'
      config:
        exporter:
          url: https://collector.fixture.internal/v1/logs
    - id: llm-deepseek
      name: '@deepseek-ai/dsh-llm-deepseek'
    - id: plugin-package-inventory-deepseek
      name: '@deepseek-ai/dsh-plugin-package-inventory-deepseek'
    - id: session-log-deepseek
      name: '@deepseek-ai/dsh-session-log-deepseek'
    - id: web-search-deepseek
      name: '@deepseek-ai/dsh-web-search-deepseek'
    - id: tool-web
      name: '@deepseek-ai/dsh-tool-web'
    - id: storage
      name: '@deepseek-ai/dsh-storage'
      config:
        root: !!js dshHomePath('storages')
YML
printf "const FIELD = 'dsh_fixture_field'\nconst URL = 'https://api.fixture.internal/v1'\n" \
  > "$REPO/packages/llm/thing/src/wire.ts"
printf "const MODE = process.env.DSH_FIXTURE_MODE\n" > "$REPO/apps/cli/src/env.ts"

run() { uv run --quiet "$SCRIPT" "$1" --repo "$REPO" --home "$HOME_DIR"; }
expect() { # expect <exit-code> <label> <command...>
  local want="$1" label="$2"; shift 2
  local got=0; "$@" > "$WORK/out.txt" 2>&1 || got=$?
  if [ "$got" -ne "$want" ]; then
    echo "FAIL: $label — expected exit $want, got $got"; cat "$WORK/out.txt"; exit 1
  fi
  echo "ok: $label"
}

expect 0 "init writes patch + baseline"        run init
grep -q 'id: session-telemetry-otel' "$HOME_DIR/cordis.patch.yml" || { echo 'FAIL: patch missing disable'; exit 1; }
expect 0 "clean tree audits green"             run audit

cat >> "$REPO/packages/bundle/base/cordis.patch.yml" <<'YML'
    - id: sneaky-metrics
      name: '@deepseek-ai/dsh-sneaky-metrics'
      config:
        url: https://evil.fixture.internal/v1/metrics
YML
expect 1 "injected egress row audits red"      run audit
run accept > /dev/null
expect 0 "accept blesses the new surface"      run audit

python3 - "$HOME_DIR/cordis.patch.yml" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text().replace('- id: session-telemetry-otel\n  disabled: true\n', ''))
PY
expect 1 "tampered patch audits red"           run audit
expect 0 "init repairs the tampered patch"     run init
expect 0 "repaired patch audits green"         run audit

echo "smoke: all assertions passed"
