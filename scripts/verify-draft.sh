#!/usr/bin/env bash
set -Eeuo pipefail

# Reproduce the E3-B07 DRAFT baseline without modifying the checked-in fixture.
# Run from anywhere: bash scripts/verify-draft.sh
#
# Phases:
#   build      E3-B07-001  local `make` + `./hello` (two-layer success criteria)
#   broken     E3-B07-002  docker build of Dockerfile.broken must fail (make: not found)
#   reference  E3-B07-003  docker build + docker run of Dockerfile.reference must succeed
#
# The two docker phases are skipped with a warning when no Docker engine is reachable.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$ROOT/fixtures/draft"
EVIDENCE="$ROOT/evidence/draft"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/draft.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

DOCKER="${DOCKER:-docker}"
BROKEN_TAG="nju-e3-draft-broken:verify"
REFERENCE_TAG="nju-e3-draft-reference:verify"

mkdir -p "$EVIDENCE"

git_commit="$(git -C "$ROOT" rev-parse HEAD)"
run_started="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

docker_available() {
  command -v "$DOCKER" >/dev/null 2>&1 && "$DOCKER" info >/dev/null 2>&1
}

docker_line() {
  if docker_available; then
    printf 'docker: %s\n' "$("$DOCKER" --version 2>/dev/null | sed -n '1p')"
  else
    printf 'docker: unavailable (broken/reference phases will be skipped)\n'
  fi
}

header() {
  local phase="$1"
  cat <<EOF
# E3-B07 DRAFT verification log
phase: $phase
started_at_utc: $run_started
repository: $(git -C "$ROOT" config --get remote.origin.url || true)
git_commit: $git_commit
script_sha256: $(sha256sum "$ROOT/scripts/verify-draft.sh" | awk '{print $1}')
fixture: fixtures/draft
host: $(uname -a)
make: $(make --version | sed -n '1p')
compiler: $(cc --version | sed -n '1p')
git: $(git --version)
$(docker_line)

EOF
}

run_cmd() {
  printf '+ %q' "$1"
  local argument
  for argument in "${@:2}"; do
    printf ' %q' "$argument"
  done
  printf '\n'
  local rc=0
  "$@" || rc=$?
  printf 'exit_code: %s\n' "$rc"
  return "$rc"
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  if [[ "$expected" != "$actual" ]]; then
    printf 'ASSERTION FAILED: %s (expected=%q actual=%q)\n' "$label" "$expected" "$actual" >&2
    return 1
  fi
  printf 'assertion: %s = %q\n' "$label" "$actual"
}

phase_build() {
  local log="$EVIDENCE/build.log"
  local destination="$WORK/build"
  {
    header "build"
    printf '\n## E3-B07-001 local build and two-layer success criteria\n'
    mkdir -p "$destination"
    run_cmd cp "$FIXTURE/main.c" "$destination/main.c"
    run_cmd cp "$FIXTURE/Makefile" "$destination/Makefile"
    cd "$destination"
    run_cmd make clean
    run_cmd make
    printf '+ test -x ./hello\n'
    test -x ./hello
    printf 'assertion: ./hello exists and is executable\n'
    printf '+ ./hello\n'
    local output
    output="$(./hello)"
    printf 'stdout: %s\n' "$output"
    assert_eq "hello E3" "$output" "draft local output"
    run_cmd make clean
    printf 'result: layer 1 (build) and layer 2 (functional) both satisfied\n'
  } >"$log" 2>&1
}

phase_broken() {
  local log="$EVIDENCE/broken.log"
  {
    header "broken"
    printf '\n## E3-B07-002 docker build must fail on the toolchain-less image\n'
    local rc=0
    run_cmd "$DOCKER" build --progress=plain --no-cache \
      -f "$FIXTURE/Dockerfile.broken" -t "$BROKEN_TAG" "$FIXTURE" || rc=$?
    if [[ "$rc" -eq 0 ]]; then
      printf 'ASSERTION FAILED: broken docker build unexpectedly succeeded\n' >&2
      return 1
    fi
    printf 'assertion: broken docker build exit code is non-zero (got %s)\n' "$rc"
    if grep -q "make: not found" "$log"; then
      printf 'assertion: build log contains "make: not found"\n'
    else
      printf 'ASSERTION FAILED: build log does not contain "make: not found"\n' >&2
      return 1
    fi
    run_cmd "$DOCKER" rmi -f "$BROKEN_TAG"
    printf 'result: missing-toolchain failure reproduced and locatable\n'
  } >"$log" 2>&1
}

phase_reference() {
  local log="$EVIDENCE/reference.log"
  {
    header "reference"
    printf '\n## E3-B07-003 docker build must succeed and run hello E3\n'
    local rc=0
    run_cmd "$DOCKER" build --progress=plain --no-cache \
      -f "$FIXTURE/Dockerfile.reference" -t "$REFERENCE_TAG" "$FIXTURE" || rc=$?
    assert_eq "0" "$rc" "reference docker build exit code"
    local image_id
    image_id="$("$DOCKER" image inspect --format '{{.Id}}' "$REFERENCE_TAG")"
    printf 'image_id: %s\n' "$image_id"
    printf '+ %s run --rm %s\n' "$DOCKER" "$REFERENCE_TAG"
    local output
    output="$("$DOCKER" run --rm "$REFERENCE_TAG")"
    printf 'stdout: %s\n' "$output"
    assert_eq "hello E3" "$output" "reference container stdout"
    run_cmd "$DOCKER" rmi -f "$REFERENCE_TAG"
    printf 'result: reference toolchain build and container run verified\n'
  } >"$log" 2>&1
}

phase_build
if docker_available; then
  phase_broken
  phase_reference
else
  printf 'docker engine unavailable; skipped E3-B07-002/003 docker phases.\n' >&2
  printf 'Start a Docker engine (Docker Desktop or `dockerd`) and re-run to refresh evidence.\n' >&2
fi

printf 'DRAFT verification complete. Evidence written to %s\n' "$EVIDENCE"
