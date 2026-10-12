#!/usr/bin/env bash
set -Eeuo pipefail

# Reproduce E3-B07-006 without modifying the checked-in fixture.
# Run from any directory: bash scripts/verify-md-fixer.sh

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$ROOT/fixtures/md-fixer"
EVIDENCE="$ROOT/evidence/md-fixer"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/md-fixer.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
trap 'printf "Verification failed (exit %s). See %s for the command output.\n" "$?" "$EVIDENCE" >&2' ERR

mkdir -p "$EVIDENCE"

git_commit="$(git -C "$ROOT" rev-parse HEAD)"
run_started="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

header() {
  local phase="$1"
  cat <<EOF
# E3-B07-006 MDFixer verification log
phase: $phase
started_at_utc: $run_started
repository: $(git -C "$ROOT" config --get remote.origin.url || true)
git_commit: $git_commit
script_sha256: $(sha256sum "$ROOT/scripts/verify-md-fixer.sh" | awk '{print $1}')
fixture: fixtures/md-fixer
host: $(uname -a)
make: $(make --version | sed -n '1p')
compiler: $(cc --version | sed -n '1p')
git: $(git --version)

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

assert_file_sha() {
  local expected="$1"
  local file="$2"
  local label="$3"
  local actual
  actual="$(sha256sum "$file" | awk '{print $1}')"
  assert_eq "$expected" "$actual" "$label"
}

copy_sources() {
  local destination="$1"
  mkdir -p "$destination"
  run_cmd cp "$FIXTURE/main.c" "$destination/main.c"
  run_cmd cp "$FIXTURE/config.h" "$destination/config.h"
  run_cmd cp "$FIXTURE/unused.h" "$destination/unused.h"
}

phase_apply() {
  local log="$EVIDENCE/apply.log"
  {
    header "apply"
    for style in target macro hybrid implicit; do
      local style_dir="$FIXTURE/styles/$style"
      printf '\n## style: %s\n' "$style"
      (cd "$style_dir" && run_cmd git apply --check reference.patch)
      printf 'result: reference.patch accepted by git apply --check\n'
    done
  } >"$log" 2>&1
}

phase_before() {
  local log="$EVIDENCE/before.log"
  local destination="$WORK/before"
  {
    header "before"
    copy_sources "$destination"
    run_cmd cp "$FIXTURE/Makefile" "$destination/Makefile"
    cd "$destination"
    printf '\n## baseline build with missing dependency\n'
    run_cmd make clean
    run_cmd make
    printf '+ ./hello\n'
    local initial_output
    initial_output="$(./hello)"
    printf 'stdout: %s\n' "$initial_output"
    assert_eq "1" "$initial_output" "baseline output"
    local before_mtime
    before_mtime="$(stat -c %Y main.o)"
    printf 'main.o_mtime_before_header_change: %s\n' "$before_mtime"
    run_cmd sleep 1
    run_cmd sed -i 's/#define VALUE 1/#define VALUE 2/' config.h
    run_cmd make
    printf '+ ./hello\n'
    local stale_output
    stale_output="$(./hello)"
    printf 'stdout_after_header_change: %s\n' "$stale_output"
    assert_eq "1" "$stale_output" "unfixed output remains stale"
    local after_mtime
    after_mtime="$(stat -c %Y main.o)"
    printf 'main.o_mtime_after_header_change: %s\n' "$after_mtime"
    assert_eq "$before_mtime" "$after_mtime" "unfixed object was not rebuilt"
    printf 'result: missing dependency reproduced\n'
  } >"$log" 2>&1
}

prepare_patched_style() {
  local style="$1"
  local destination="$WORK/patched-$style"
  copy_sources "$destination"
  run_cmd cp "$FIXTURE/styles/$style/Makefile.before" "$destination/Makefile.before"
  run_cmd cp "$FIXTURE/styles/$style/reference.patch" "$destination/reference.patch"
  (
    cd "$destination"
    run_cmd git init -q
    run_cmd git apply --check reference.patch
    run_cmd git apply reference.patch
    run_cmd cp Makefile.before Makefile
  )
}

phase_after() {
  local log="$EVIDENCE/after.log"
  {
    header "after"
    local style destination initial_output fixed_output before_mtime after_mtime
    for style in target macro hybrid implicit; do
      printf '\n## style: %s\n' "$style"
      prepare_patched_style "$style"
      destination="$WORK/patched-$style"
      cd "$destination"
      run_cmd sed -i 's/#define VALUE 1/#define VALUE 2/' config.h
      run_cmd make clean
      run_cmd make
      printf '+ ./hello\n'
      initial_output="$(./hello)"
      printf 'stdout_initial: %s\n' "$initial_output"
      assert_eq "2" "$initial_output" "$style clean patched output with changed header"
      run_cmd sleep 1
      run_cmd sed -i 's/#define VALUE 2/#define VALUE 3/' config.h
      before_mtime="$(stat -c %Y main.o)"
      run_cmd make
      printf '+ ./hello\n'
      fixed_output="$(./hello)"
      printf 'stdout_after_first_header_change: %s\n' "$fixed_output"
      assert_eq "3" "$fixed_output" "$style patched output after first change"
      after_mtime="$(stat -c %Y main.o)"
      printf 'main.o_mtime_before_make: %s\n' "$before_mtime"
      printf 'main.o_mtime_after_make: %s\n' "$after_mtime"
      if [[ "$before_mtime" == "$after_mtime" ]]; then
        printf 'ASSERTION FAILED: %s object did not rebuild\n' "$style" >&2
        return 1
      fi
      printf 'result: clean patch application and automatic rebuild verified\n'
    done
  } >"$log" 2>&1
}

phase_reapply() {
  local log="$EVIDENCE/reapply.log"
  {
    header "reapply"
    local style output before_mtime after_mtime
    for style in target macro hybrid implicit; do
      local destination="$WORK/patched-$style"
      printf '\n## style: %s\n' "$style"
      cd "$destination"
      run_cmd sleep 1
      run_cmd sed -i 's/#define VALUE 3/#define VALUE 4/' config.h
      before_mtime="$(stat -c %Y main.o)"
      run_cmd make
      printf '+ ./hello\n'
      output="$(./hello)"
      printf 'stdout_after_second_header_change: %s\n' "$output"
      assert_eq "4" "$output" "$style patched output after second change"
      after_mtime="$(stat -c %Y main.o)"
      printf 'main.o_mtime_before_make: %s\n' "$before_mtime"
      printf 'main.o_mtime_after_make: %s\n' "$after_mtime"
      if [[ "$before_mtime" == "$after_mtime" ]]; then
        printf 'ASSERTION FAILED: %s object did not rebuild on reapply\n' "$style" >&2
        return 1
      fi
      printf 'result: repeated header change rebuilt without make clean\n'
    done
  } >"$log" 2>&1
}

phase_rejected() {
  local log="$EVIDENCE/rejected.log"
  local destination="$WORK/rejected"
  {
    header "rejected"
    copy_sources "$destination"
    run_cmd cp "$FIXTURE/Makefile" "$destination/Makefile"
    cd "$destination"
    run_cmd git init -q
    run_cmd cp Makefile Makefile.bak
    local before_sha restored_sha output candidate_rc
    before_sha="$(sha256sum Makefile | awk '{print $1}')"
    printf 'sample_copy_makefile_sha_before_candidate: %s\n' "$before_sha"
    cat > invalid.patch <<'PATCH'
--- Makefile
+++ Makefile
@@ -14,1 +14,1 @@
-main.o: main.c missing.h
+main.o: main.c config.h
PATCH
    printf '%s\n' 'invalid candidate intentionally targets a line absent from the sample'
    candidate_rc=0
    run_cmd git apply --check invalid.patch || candidate_rc=$?
    printf 'invalid_candidate_check_exit_code: %s\n' "$candidate_rc"
    assert_eq "1" "$candidate_rc" "invalid candidate rejected"
    assert_file_sha "$before_sha" Makefile "sample copy unchanged after rejection"
    run_cmd cp Makefile.bak Makefile
    restored_sha="$(sha256sum Makefile | awk '{print $1}')"
    printf 'sample_copy_makefile_sha_after_restore: %s\n' "$restored_sha"
    assert_eq "$before_sha" "$restored_sha" "sample copy restored from backup"
    run_cmd make clean
    run_cmd make
    printf '+ ./hello\n'
    output="$(./hello)"
    printf 'stdout_after_restore: %s\n' "$output"
    assert_eq "1" "$output" "restored sample remains buildable"
    printf '\n## applicable candidate that leaves config.h undeclared\n'
    # Generate the candidate from the actual copy so CRLF checkouts work too.
    printf '+ sed (append an unrelated dependency) > Makefile.candidate\n'
    sed 's/main.o: main.c unused.h/main.o: main.c unused.h README.md/' Makefile > Makefile.candidate
    printf '+ diff -u --label a/Makefile --label b/Makefile Makefile Makefile.candidate > ineffective.patch\n'
    candidate_rc=0
    diff -u --label a/Makefile --label b/Makefile Makefile Makefile.candidate > ineffective.patch || candidate_rc=$?
    assert_eq "1" "$candidate_rc" "diff generated a changed candidate"
    run_cmd sed -n 'l' ineffective.patch
    run_cmd cp "$FIXTURE/README.md" README.md
    run_cmd git apply --check ineffective.patch
    run_cmd git apply ineffective.patch
    run_cmd make clean
    run_cmd make
    run_cmd sleep 1
    run_cmd sed -i 's/#define VALUE 1/#define VALUE 2/' config.h
    run_cmd make
    printf '+ ./hello\n'
    output="$(./hello)"
    printf 'candidate_stdout_after_header_change: %s\n' "$output"
    assert_eq "1" "$output" "ineffective candidate reproduces stale output"
    printf 'decision: REJECTED; required output is 2, observed output is 1\n'
    run_cmd cp Makefile.bak Makefile
    assert_file_sha "$before_sha" Makefile "Makefile restored after behavioral rejection"
    run_cmd make clean
    run_cmd make
    printf '+ ./hello\n'
    output="$(./hello)"
    printf 'stdout_after_behavioral_rejection_restore: %s\n' "$output"
    assert_eq "2" "$output" "restored copy builds the updated source"
    printf 'result: invalid and ineffective candidates rejected; sample copy recovered\n'
  } >"$log" 2>&1
}

phase_apply
phase_before
phase_after
phase_reapply
phase_rejected

printf 'MDFixer verification complete. Evidence written to %s\n' "$EVIDENCE"
