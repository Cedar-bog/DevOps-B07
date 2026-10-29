#!/usr/bin/env bash
set -Eeuo pipefail

# E3-B07 DRAFT 验证：本地两层判据 + 远程服务器 docker 失败/参考成功两相。
# 连接信息从仓库根 .env 读取；每次运行写入 evidence/draft/<UTC 时间戳>/，不覆盖历史。
# 各相输出同时打印到终端并写入日志（日志剥离 ANSI 转义码）。
# 用法：bash scripts/verify-draft.sh

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$ROOT/fixtures/draft"
EVIDENCE="$ROOT/evidence/draft"

ENV_FILE="$ROOT/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

: "${HUAWEI_HOST:?请在 .env 设置 HUAWEI_HOST}"
: "${HUAWEI_USER:?请在 .env 设置 HUAWEI_USER}"
: "${HUAWEI_PASSWORD:?请在 .env 设置 HUAWEI_PASSWORD}"
HUAWEI_PORT="${HUAWEI_PORT:-22}"

command -v sshpass >/dev/null 2>&1 || { echo "缺少 sshpass" >&2; exit 1; }

RUN_ID="$(date -u '+%Y-%m-%dT%H-%M-%SZ')"
RUN_DIR="$EVIDENCE/$RUN_ID"
if [[ -e "$RUN_DIR" ]]; then
  echo "证据目录已存在，拒绝覆盖：$RUN_DIR" >&2
  exit 1
fi
mkdir -p "$RUN_DIR"

REMOTE_DIR="/tmp/devops-b07-draft/$RUN_ID"
SSH_OPTS=(-p "$HUAWEI_PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
TARGET="$HUAWEI_USER@$HUAWEI_HOST"
BROKEN_TAG="nju-e3-draft-broken:$RUN_ID"
REFERENCE_TAG="nju-e3-draft-reference:$RUN_ID"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/draft.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

ssh_do() { sshpass -p "$HUAWEI_PASSWORD" ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }

git_commit="$(git -C "$ROOT" rev-parse HEAD)"
started="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# 剥离 ANSI 颜色/控制码，日志保持纯文本
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

header() {
  cat <<EOF
# E3-B07 DRAFT verification log
phase: $1
started_at_utc: $started
git_commit: $git_commit
script_sha256: $(sha256sum "$ROOT/scripts/verify-draft.sh" | awk '{print $1}')
fixture: fixtures/draft

EOF
}

phase_build() {
  local log="$RUN_DIR/build.log" dest="$WORK/build" rc
  set +e
  {
    header build
    printf '## 本地构建与两层成功判据\n'
    mkdir -p "$dest"
    run cp "$FIXTURE/main.c" "$dest/main.c"
    run cp "$FIXTURE/Makefile" "$dest/Makefile"
    cd "$dest"
    run make clean
    run make
    test -x ./hello && printf 'assertion: ./hello 存在且可执行\n'
    local out
    out="$("./hello")"
    printf 'stdout: %s\n' "$out"
    assert_eq "hello E3" "$out" "draft 本地输出"
    run make clean
    printf 'result: 编译通过与功能验证均满足\n'
  } 2>&1 | strip_ansi | tee "$log"
  rc=${PIPESTATUS[0]}
  set -e
  return "$rc"
}

phase_broken() {
  local log="$RUN_DIR/broken.log" rc
  set +e
  {
    header broken
    printf '## 远程 docker build 失败样例\n'
    run ssh_do "cd '$REMOTE_DIR' && docker build --no-cache -f Dockerfile.broken -t $BROKEN_TAG ."
  } 2>&1 | strip_ansi | tee "$log"
  rc=${PIPESTATUS[0]}
  set -e
  printf 'assertion: broken 构建退出码非零（%s）\n' "$rc" | tee -a "$log"
  if [[ "$rc" -eq 0 ]]; then
    printf 'ASSERTION FAILED: 失败样例竟然构建成功\n' >&2
    return 1
  fi
  if grep -q "make: not found" "$log"; then
    printf 'assertion: 日志命中 "make: not found"\n' | tee -a "$log"
  else
    printf 'ASSERTION FAILED: 日志未命中 "make: not found"\n' >&2
    return 1
  fi
  ssh_do "docker rmi -f $BROKEN_TAG" >/dev/null 2>&1 || true
  printf 'result: 缺工具链失败已复现且可定位\n' | tee -a "$log"
  return 0
}

phase_reference() {
  local log="$RUN_DIR/reference.log" rc image_id out
  set +e
  {
    header reference
    printf '## 远程 docker build + run 参考成功\n'
    run ssh_do "cd '$REMOTE_DIR' && docker build --no-cache -f Dockerfile.reference -t $REFERENCE_TAG ."
  } 2>&1 | strip_ansi | tee "$log"
  rc=${PIPESTATUS[0]}
  set -e
  printf 'assertion: reference 构建退出码 = %s\n' "$rc" | tee -a "$log"
  if [[ "$rc" -ne 0 ]]; then
    printf 'ASSERTION FAILED: reference 构建失败\n' >&2
    return 1
  fi
  image_id="$(ssh_do "docker image inspect --format '{{.Id}}' $REFERENCE_TAG")"
  printf 'image_id: %s\n' "$image_id" | tee -a "$log"
  printf '%s' "$image_id" >"$RUN_DIR/.image_id"
  out="$(ssh_do "docker run --rm $REFERENCE_TAG")"
  printf 'stdout: %s\n' "$out" | tee -a "$log"
  if [[ "$out" != "hello E3" ]]; then
    printf 'ASSERTION FAILED: 容器输出 = %q\n' "$out" >&2
    return 1
  fi
  printf 'assertion: reference 容器输出 = hello E3\n' | tee -a "$log"
  ssh_do "docker rmi -f $REFERENCE_TAG" >/dev/null 2>&1 || true
  printf 'result: 参考环境构建成功且容器输出正确\n' | tee -a "$log"
  return 0
}

write_meta() {
  local docker_version base_image_id image_id server_hostname
  docker_version="$(ssh_do 'docker --version' 2>/dev/null || echo unknown)"
  server_hostname="$(ssh_do 'hostname' 2>/dev/null || echo unknown)"
  base_image_id="$(ssh_do "docker image inspect --format '{{.Id}}' python:3.13-slim" 2>/dev/null || echo unknown)"
  image_id="$(cat "$RUN_DIR/.image_id" 2>/dev/null || true)"
  rm -f "$RUN_DIR/.image_id"
  cat >"$RUN_DIR/meta.json" <<EOF
{
  "run_id": "$RUN_ID",
  "started_at_utc": "$started",
  "git_commit": "$git_commit",
  "server_hostname": "$server_hostname",
  "docker_version": "$docker_version",
  "base_image": "python:3.13-slim",
  "base_image_id": "$base_image_id",
  "image_id": "$image_id",
  "remote_dir": "$REMOTE_DIR"
}
EOF
}

ssh_do 'docker info >/dev/null 2>&1' || { echo "远程 docker 不可用（可能需 sudo 或加入 docker 组）" >&2; exit 1; }

printf 'run_id: %s\n' "$RUN_ID"
printf '==> build（本地）\n';                       phase_build
printf '==> upload（tar -> server）\n' "$HUAWEI_HOST"
ssh_do "mkdir -p '$REMOTE_DIR'"
tar -C "$FIXTURE" -cf - . | sshpass -p "$HUAWEI_PASSWORD" ssh "${SSH_OPTS[@]}" "$TARGET" "tar -C '$REMOTE_DIR' -xf -"
printf '==> broken（远程 docker build，预期失败）\n'; phase_broken
printf '==> reference（远程 docker build + run）\n';  phase_reference
printf '==> meta\n';                                 write_meta
ssh_do "rm -rf '$REMOTE_DIR'" >/dev/null 2>&1 || true

printf 'DRAFT 验证完成，证据写入 %s\n' "$RUN_DIR"