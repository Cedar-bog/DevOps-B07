#!/usr/bin/env bash
set -Eeuo pipefail

# E3-B07 MDFixer 验证：在样例副本上应用四种参考补丁并观察行为，验证无效候选被拒绝。
# 每次运行写入 evidence/md-fixer/<UTC 时间戳>/，不覆盖历史。
# 各相输出同时打印到终端并写入日志。
# 用法：bash scripts/verify-md-fixer.sh

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$ROOT/fixtures/md-fixer"
EVIDENCE="$ROOT/evidence/md-fixer"

RUN_ID="$(date -u '+%Y-%m-%dT%H-%M-%SZ')"
RUN_DIR="$EVIDENCE/$RUN_ID"
if [[ -e "$RUN_DIR" ]]; then
  echo "证据目录已存在，拒绝覆盖：$RUN_DIR" >&2
  exit 1
fi
mkdir -p "$RUN_DIR"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/md-fixer.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

git_commit="$(git -C "$ROOT" rev-parse HEAD)"
started="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

strip_ansi() { sed -u -r 's/\x1b\[[0-9;]*[A-Za-z]//g'; }

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

header() {
  cat <<EOF
# E3-B07 MDFixer verification log
phase: $1
started_at_utc: $started
git_commit: $git_commit
script_sha256: $(sha256sum "$ROOT/scripts/verify-md-fixer.sh" | awk '{print $1}')
fixture: fixtures/md-fixer

EOF
}

copy_sources() {
  local destination="$1"
  mkdir -p "$destination"
  cp "$FIXTURE/main.c" "$destination/main.c"
  cp "$FIXTURE/config.h" "$destination/config.h"
  cp "$FIXTURE/unused.h" "$destination/unused.h"
}

phase_apply() {
  local log="$RUN_DIR/apply.log" style
  {
    header apply
    printf '## 四种参考补丁可对各自 Makefile.before 干净应用\n'
    for style in target macro hybrid implicit; do
      printf '\n## style: %s\n' "$style"
      cd "$FIXTURE/styles/$style"
      run git apply --check reference.patch
    done
    printf '\nresult: 四份 reference.patch 均通过 git apply --check\n'
  } 2>&1 | strip_ansi | tee "$log"
}

phase_before() {
  local log="$RUN_DIR/before.log" dest="$WORK/before" out before_mtime after_mtime
  {
    header before
    printf '## 修复前：只改头文件不重建，输出旧值\n'
    copy_sources "$dest"
    cp "$FIXTURE/Makefile" "$dest/Makefile"
    cd "$dest"
    run make clean
    run make
    out="$("./hello")"
    printf 'stdout: %s\n' "$out"
    assert_eq "1" "$out" "baseline 输出"
    before_mtime="$(stat -c %Y main.o)"
    printf 'main.o_mtime_before: %s\n' "$before_mtime"
    run sleep 1
    run sed -i 's/#define VALUE 1/#define VALUE 2/' config.h
    run make
    out="$("./hello")"
    printf 'stdout_after_header_change: %s\n' "$out"
    assert_eq "1" "$out" "未修复时仍输出旧值"
    after_mtime="$(stat -c %Y main.o)"
    printf 'main.o_mtime_after: %s\n' "$after_mtime"
    assert_eq "$before_mtime" "$after_mtime" "未修复时 main.o 未重建"
    printf 'result: 缺失 config.h 依赖已复现\n'
  } 2>&1 | strip_ansi | tee "$log"
}

prepare_patched_style() {
  local style="$1" destination="$WORK/patched-$style"
  copy_sources "$destination"
  cp "$FIXTURE/styles/$style/Makefile.before" "$destination/Makefile.before"
  cp "$FIXTURE/styles/$style/reference.patch" "$destination/reference.patch"
  cd "$destination"
  run git init -q
  run git config core.autocrlf false
  run git apply --check reference.patch
  run git apply reference.patch
  run cp Makefile.before Makefile
}

phase_after() {
  local log="$RUN_DIR/after.log" style out bm am
  {
    header after
    printf '## 应用参考补丁后：clean 构建输出新值，再改头文件自动重建\n'
    for style in target macro hybrid implicit; do
      printf '\n## style: %s\n' "$style"
      prepare_patched_style "$style"
      case "$style" in
        target)   grep -q '^main.o: main.c config.h$' Makefile ;;
        macro)    grep -q '^DEPS    := main.c config.h$' Makefile ;;
        hybrid)   grep -q '^HEADERS := config.h$' Makefile ;;
        implicit) grep -q '^-include \$(DEPS)$' Makefile ;;
      esac || { printf 'ASSERTION FAILED: %s 声明风格被改变\n' "$style" >&2; return 1; }
      printf 'assertion: %s 声明风格保持不变\n' "$style"
      run sed -i 's/#define VALUE 1/#define VALUE 2/' config.h
      run make clean
      run make
      out="$("./hello")"
      printf 'stdout_initial: %s\n' "$out"
      assert_eq "2" "$out" "$style clean 后输出新值"
      run sleep 1
      run sed -i 's/#define VALUE 2/#define VALUE 3/' config.h
      bm="$(stat -c %Y main.o)"
      run make
      out="$("./hello")"
      printf 'stdout_after_header_change: %s\n' "$out"
      assert_eq "3" "$out" "$style 再次改头文件自动重建"
      am="$(stat -c %Y main.o)"
      if [[ "$bm" == "$am" ]]; then
        printf 'ASSERTION FAILED: %s main.o 未重建\n' "$style" >&2
        return 1
      fi
      printf 'result: %s 补丁有效\n' "$style"
    done
  } 2>&1 | strip_ansi | tee "$log"
}

phase_reapply() {
  local log="$RUN_DIR/reapply.log" style out bm am
  {
    header reapply
    printf '## 再次改头文件：不 clean 也应自动重建\n'
    for style in target macro hybrid implicit; do
      printf '\n## style: %s\n' "$style"
      cd "$WORK/patched-$style"
      run sleep 1
      run sed -i 's/#define VALUE 3/#define VALUE 4/' config.h
      bm="$(stat -c %Y main.o)"
      run make
      out="$("./hello")"
      printf 'stdout_after_second_header_change: %s\n' "$out"
      assert_eq "4" "$out" "$style 第二次改头文件自动重建"
      am="$(stat -c %Y main.o)"
      if [[ "$bm" == "$am" ]]; then
        printf 'ASSERTION FAILED: %s main.o 未重建\n' "$style" >&2
        return 1
      fi
      printf 'result: %s 不 clean 也能重建\n' "$style"
    done
  } 2>&1 | strip_ansi | tee "$log"
}

phase_rejected() {
  local log="$RUN_DIR/rejected.log" dest="$WORK/rejected" out before_sha car
  {
    header rejected
    printf '## 无效候选的拒绝与恢复\n'
    copy_sources "$dest"
    cp "$FIXTURE/Makefile" "$dest/Makefile"
    cd "$dest"
    run git init -q
    run git config core.autocrlf false
    run cp Makefile Makefile.bak
    before_sha="$(sha256sum Makefile | awk '{print $1}')"
    printf 'makefile_sha_before: %s\n' "$before_sha"

    printf '\n## 候选一：补丁指向样例中不存在的行\n'
    cat > invalid.patch <<'PATCH'
--- Makefile
+++ Makefile
@@ -14,1 +14,1 @@
-main.o: main.c missing.h
+main.o: main.c config.h
PATCH
    car=0
    run git apply --check invalid.patch || car=$?
    printf 'invalid_candidate_check_exit_code: %s\n' "$car"
    assert_eq "1" "$car" "无效候选被拒绝"
    assert_file_sha "$before_sha" Makefile "拒绝后 Makefile 未变"

    printf '\n## 候选二：可应用但没修好缺失依赖\n'
    printf 'unused for ineffective candidate\n' > unrelated.txt
    sed 's/main.o: main.c unused.h/main.o: main.c unused.h unrelated.txt/' Makefile > Makefile.candidate
    car=0
    diff -u --label a/Makefile --label b/Makefile Makefile Makefile.candidate > ineffective.patch || car=$?
    assert_eq "1" "$car" "diff 产生了变更"
    run git apply --check ineffective.patch
    run git apply ineffective.patch
    run make clean
    run make
    run sleep 1
    run sed -i 's/#define VALUE 1/#define VALUE 2/' config.h
    run make
    out="$("./hello")"
    printf 'candidate_stdout_after_header_change: %s\n' "$out"
    assert_eq "1" "$out" "无效候选未修复缺失依赖"
    printf 'decision: REJECTED；期望 2，实际 1\n'

    printf '\n## 恢复\n'
    run cp Makefile.bak Makefile
    assert_file_sha "$before_sha" Makefile "恢复后 Makefile 与原始一致"
    run make clean
    run make
    out="$("./hello")"
    printf 'stdout_after_restore: %s\n' "$out"
    assert_eq "2" "$out" "恢复后构建反映新 config.h"
    printf 'result: 无效候选被拒绝且样例副本可恢复\n'
  } 2>&1 | strip_ansi | tee "$log"
}

write_meta() {
  cat >"$RUN_DIR/meta.json" <<EOF
{
  "run_id": "$RUN_ID",
  "started_at_utc": "$started",
  "git_commit": "$git_commit",
  "host": "$(hostname)",
  "make": "$(make --version | sed -n '1p')",
  "compiler": "$(cc --version | sed -n '1p')",
  "git": "$(git --version)"
}
EOF
}

printf 'run_id: %s\n' "$RUN_ID"
printf '==> apply\n';    phase_apply
printf '==> before\n';   phase_before
printf '==> after\n';    phase_after
printf '==> reapply\n';  phase_reapply
printf '==> rejected\n'; phase_rejected
printf '==> meta\n';     write_meta

printf 'MDFixer 验证完成，证据写入 %s\n' "$RUN_DIR"