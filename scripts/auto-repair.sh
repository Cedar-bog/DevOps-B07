#!/usr/bin/env bash
# Run one complete DRAFT repair on the remote server.
#
# Requirements:
#   - the server is reachable through scripts/remote.sh
#   - the server has Docker
#   - the local .env contains LLM_API_URL, LLM_API_KEY and LLM_MODEL
#
# The LLM key is sent to the server only for this run, written to a mode-600
# temporary .env under the remote run directory, and removed on exit.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE="$ROOT/scripts/remote.sh"
ENV_FILE="$ROOT/.env"
FIXTURE="${FIXTURE:-fixtures/draft}"
EXPECTED_OUTPUT="${EXPECTED_OUTPUT:-hello E3}"
MAX_ITERATIONS="${MAX_ITERATIONS:-3}"
BUILD_TIMEOUT="${BUILD_TIMEOUT:-600}"
VERIFY_TIMEOUT="${VERIFY_TIMEOUT:-120}"
APT_MIRROR="${APT_MIRROR:-repo.huaweicloud.com}"

if [ -n "$APT_MIRROR" ] && ! [[ "$APT_MIRROR" =~ ^[A-Za-z0-9.-]+$ ]]; then
  printf 'ERROR: APT_MIRROR must be a hostname, for example repo.huaweicloud.com\n' >&2
  exit 2
fi

RUN_ID="$(date -u '+%Y-%m-%dT%H-%M-%SZ')"
RUN_DIR="$ROOT/work/$RUN_ID"
STAGE="/tmp/e6-repair-$RUN_ID"
LLM_ENV_TMP=""
CLEANED=0

log() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

cleanup() {
  local rc=$?
  trap - EXIT
  if [ -n "$LLM_ENV_TMP" ]; then
    rm -f "$LLM_ENV_TMP" 2>/dev/null || true
  fi
  if [ -n "$STAGE" ] && [ -x "$REMOTE" ]; then
    "$REMOTE" --shell "rm -rf '$STAGE'" >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap cleanup EXIT

[ -f "$ENV_FILE" ] || fail "missing $ENV_FILE"
[ -d "$ROOT/$FIXTURE" ] || fail "fixture not found: $FIXTURE"
[ -x "$REMOTE" ] || fail "remote wrapper is not executable: scripts/remote.sh"
[ ! -e "$RUN_DIR" ] || fail "evidence directory already exists: work/$RUN_ID"
mkdir -p "$RUN_DIR"

log "[1/7] 校验参数 ... OK"
log "      fixture: $FIXTURE"
log "      max_iterations: $MAX_ITERATIONS"
log "      build_timeout: ${BUILD_TIMEOUT}s"
log "      verify_timeout: ${VERIFY_TIMEOUT}s"
if [ -n "$APT_MIRROR" ]; then
  log "      apt_mirror: $APT_MIRROR"
fi

# Extract only LLM settings from the local .env; never upload the whole file.
LLM_ENV_TMP="$(mktemp "${TMPDIR:-/tmp}/e6-llm.XXXXXX")"
chmod 600 "$LLM_ENV_TMP"
grep -E '^[[:space:]]*(export[[:space:]]+)?(LLM_API_URL|LLM_API_KEY|LLM_MODEL|LLM_TIMEOUT|LLM_MAX_TOKENS|LLM_TEMPERATURE)[[:space:]]*=' \
  "$ENV_FILE" >"$LLM_ENV_TMP" || true
for key in LLM_API_URL LLM_API_KEY LLM_MODEL; do
  grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$LLM_ENV_TMP" \
    || fail "local .env is missing $key"
done

log "[2/7] 上传代码与 fixture ... RUNNING"
if ! tar -C "$ROOT" -cf - services/draft "$FIXTURE" requirements-dev.lock \
  | "$REMOTE" --shell "umask 077; rm -rf '$STAGE' && mkdir -p '$STAGE' && tar -C '$STAGE' -xf -"; then
  fail "upload failed"
fi
log "      status: OK"

log "[3/7] 写入临时 LLM 配置 ... RUNNING"
if ! cat "$LLM_ENV_TMP" \
  | "$REMOTE" --shell "umask 077; cat > '$STAGE/.env'; chmod 600 '$STAGE/.env'"; then
  fail "failed to write temporary LLM configuration"
fi
log "      status: OK"
log "      mode: 0600"
log "      model: configured"

log "[4/7] 服务器执行真实自动修复 ... RUNNING"
set +e
"$REMOTE" --shell "cd '$STAGE' && PYTHONPATH=services/draft python3 -u -m draft.cli repair \
  --fixture-dir '$FIXTURE' \
  --out 'work/$RUN_ID' \
  --max-iterations '$MAX_ITERATIONS' \
  --build-timeout '$BUILD_TIMEOUT' \
  --verify-timeout '$VERIFY_TIMEOUT' \
  --expected-output '$EXPECTED_OUTPUT' \
  --apt-mirror '$APT_MIRROR' \
  --progress" 2>&1 \
  | tee "$RUN_DIR/run.log" \
  | python3 "$ROOT/scripts/progress_filter.py"
RC=${PIPESTATUS[0]}
set -e
log "      exit_code: $RC"

log "[5/7] 拉回证据 ... RUNNING"
mkdir -p "$RUN_DIR"
if "$REMOTE" --shell "test -d '$STAGE/work/$RUN_ID'" >/dev/null 2>&1; then
  "$REMOTE" --shell "tar -C '$STAGE/work/$RUN_ID' --exclude='./project' -cf - ." | tar -C "$RUN_DIR" -xf -
fi
log "      status: OK"

log "[6/7] 清理服务器临时目录 ... RUNNING"
"$REMOTE" --shell "rm -rf '$STAGE'" >/dev/null 2>&1 || true
STAGE=""
CLEANED=1
log "      status: OK"

log "[7/7] 完成"
RESULT_FILE="$RUN_DIR/result.json"
if [ -f "$RESULT_FILE" ]; then
  python3 - "$RESULT_FILE" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path, encoding="utf-8"))
initial = (data.get("initial") or {}).get("build") or {}
rounds = data.get("rounds") or []
last_build = (rounds[-1].get("build") if rounds else {}) or {}
verify = (data.get("final") or {}).get("verify") or {}
print(f"      status: {data.get('status')}")
print(f"      initial_failure: {initial.get('failure_class')}")
print(f"      rounds: {len(rounds)}")
print(f"      build_exit_code: {last_build.get('exit_code')}")
print(f"      output_matches: {verify.get('output_matches')}")
print(f"      trace_id: {data.get('trace_id')}")
image = (data.get("final") or {}).get("image_id")
if image:
    print(f"      image_id: {image[:19]}")
PY
else
  log "      result: missing"
fi
log "      result: work/$RUN_ID/result.json"
log "      run_log: work/$RUN_ID/run.log"

[ "$CLEANED" -eq 1 ] || fail "cleanup did not complete"
[ "$RC" -eq 0 ]
