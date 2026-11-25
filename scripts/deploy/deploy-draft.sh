#!/usr/bin/env bash
# Build and deploy the DRAFT HTTP service on the runtime server.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE="$ROOT/scripts/remote/remote.sh"
ENV_FILE="$ROOT/.env"
RUN_ID="$(date -u '+%Y-%m-%dT%H-%M-%SZ')"
RUN_DIR="$ROOT/work/deploy-draft/$RUN_ID"
REMOTE_DIR="/opt/devops-b07/draft"
STAGE="$REMOTE_DIR/stage-$RUN_ID"
DATA_DIR="$REMOTE_DIR/data"
TAG="$(git -C "$ROOT" rev-parse HEAD)"

log() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[ -f "$ENV_FILE" ] || fail "missing $ENV_FILE"
[ -x "$REMOTE" ] || fail "missing executable $REMOTE"
[ -f "$ROOT/services/draft/Dockerfile" ] || fail "missing services/draft/Dockerfile"
[ -f "$ROOT/deploy/draft/compose.yaml" ] || fail "missing deploy/draft/compose.yaml"
[ ! -e "$RUN_DIR" ] || fail "evidence directory already exists: $RUN_DIR"
mkdir -p "$RUN_DIR"

log "==> deploy DRAFT $TAG"

log "==> upload"
if ! tar -C "$ROOT" -cf - services/draft fixtures/draft requirements-dev.lock deploy/draft/compose.yaml \
  | "$REMOTE" --shell "rm -rf '$STAGE' && mkdir -p '$STAGE' && tar -C '$STAGE' -xf -"; then
  fail "upload failed"
fi

log "==> build image"
set +e
{
  printf '# DRAFT image build\n'
  printf 'commit: %s\n' "$TAG"
  printf 'started_at_utc: %s\n\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  "$REMOTE" --shell "cd '$STAGE' && docker build --progress=plain -f services/draft/Dockerfile -t 'b07-draft:$TAG' ."
} 2>&1 | tee "$RUN_DIR/build.log"
BUILD_RC=${PIPESTATUS[0]}
set -e
if [ "$BUILD_RC" -ne 0 ]; then
  "$REMOTE" --shell "rm -rf '$STAGE'" >/dev/null 2>&1 || true
  fail "image build failed"
fi

PREVIOUS_TAG="$("$REMOTE" --shell "cat '$REMOTE_DIR/current_tag' 2>/dev/null || true" | tr -d '\r\n')"
log "==> previous tag: ${PREVIOUS_TAG:-none}"

log "==> start service"
if ! "$REMOTE" --shell "mkdir -p '$DATA_DIR' && cp '$STAGE/deploy/draft/compose.yaml' '$REMOTE_DIR/compose.yaml' && cd '$REMOTE_DIR' && IMAGE_TAG='$TAG' DATA_DIR='$DATA_DIR' docker compose -p b07-draft -f compose.yaml up -d --remove-orphans"; then
  "$REMOTE" --shell "rm -rf '$STAGE'" >/dev/null 2>&1 || true
  fail "compose start failed"
fi

HEALTH_OK=0
for _ in $(seq 1 30); do
  STATUS="$("$REMOTE" --shell "docker inspect --format '{{.State.Health.Status}}' b07-draft 2>/dev/null || true" | tr -d '\r\n')"
  printf 'health: %s\n' "${STATUS:-unknown}" | tee -a "$RUN_DIR/health.log"
  if [ "$STATUS" = "healthy" ]; then
    HEALTH_OK=1
    break
  fi
  sleep 2
done

if [ "$HEALTH_OK" -ne 1 ]; then
  if [ -n "$PREVIOUS_TAG" ]; then
    log "==> health failed; rollback to $PREVIOUS_TAG"
    "$REMOTE" --shell "cd '$REMOTE_DIR' && IMAGE_TAG='$PREVIOUS_TAG' DATA_DIR='$DATA_DIR' docker compose -p b07-draft -f compose.yaml up -d --remove-orphans" >>"$RUN_DIR/health.log" 2>&1 || true
  else
    "$REMOTE" --shell "cd '$REMOTE_DIR' && docker compose -p b07-draft -f compose.yaml down" >>"$RUN_DIR/health.log" 2>&1 || true
  fi
  "$REMOTE" --shell "rm -rf '$STAGE'" >/dev/null 2>&1 || true
  fail "service did not become healthy"
fi

"$REMOTE" --shell "printf '%s\n' '$TAG' > '$REMOTE_DIR/current_tag'"
"$REMOTE" --shell "rm -rf '$STAGE'" >/dev/null 2>&1 || true

cat >"$RUN_DIR/meta.json" <<EOF
{
  "run_id": "$RUN_ID",
  "commit": "$TAG",
  "image": "b07-draft:$TAG",
  "previous": "$PREVIOUS_TAG",
  "health": "healthy",
  "remote_dir": "$REMOTE_DIR",
  "data_dir": "$DATA_DIR"
}
EOF

log "==> deployed b07-draft:$TAG"
log "==> evidence: ${RUN_DIR#"$ROOT/"}"
