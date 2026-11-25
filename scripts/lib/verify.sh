#!/usr/bin/env bash
# Shared helpers for the stage verification scripts.
# The caller must define ROOT, git_commit and started before using header().

strip_ansi() { sed -u -r -e 's/\x1b\[[0-9;]*[A-Za-z]//g' -e 's/\r//g'; }

run() {
  printf '+'; printf ' %q' "$@"; printf '\n'
  local rc=0
  "$@" || rc=$?
  printf 'exit_code: %s\n' "$rc"
  return "$rc"
}

assert_eq() {
  local expected="$1" actual="$2" label="$3"
  if [[ "$expected" != "$actual" ]]; then
    printf 'ASSERTION FAILED: %s (expected=%q actual=%q)\n' "$label" "$expected" "$actual" >&2
    return 1
  fi
  printf 'assertion: %s = %q\n' "$label" "$actual"
}

assert_file_sha() {
  local expected="$1" file="$2" label="$3" actual
  actual="$(sha256sum "$file" | awk '{print $1}')"
  assert_eq "$expected" "$actual" "$label"
}

verify_header() {
  local title="$1" phase="$2" fixture="$3" script="$4"
  cat <<EOF
# $title verification log
phase: $phase
started_at_utc: $started
git_commit: $git_commit
script_sha256: $(sha256sum "$script" | awk '{print $1}')
fixture: $fixture

EOF
}
