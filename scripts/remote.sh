#!/usr/bin/env bash
# Run a command on the runtime server described by .env.
# Usage: scripts/remote.sh [--tty] [--shell] <command...>
#   default: each argument is quoted and passed as a separate argv entry
#   --shell: the single argument is executed by the remote shell
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "remote: missing $ENV_FILE (copy .env.example first)" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

: "${HUAWEI_HOST:?remote: HUAWEI_HOST is not set in .env}"
: "${HUAWEI_USER:?remote: HUAWEI_USER is not set in .env}"
: "${HUAWEI_PASSWORD:?remote: HUAWEI_PASSWORD is not set in .env}"
PORT="${HUAWEI_PORT:-22}"

command -v sshpass >/dev/null 2>&1 || {
  echo "remote: sshpass is required" >&2
  exit 2
}

OPTS=(-p "$PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o ServerAliveInterval=30)
SHELL_MODE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tty) OPTS+=(-tt); shift ;;
    --shell) SHELL_MODE=1; shift ;;
    *) break ;;
  esac
done

if [[ $# -eq 0 ]]; then
  echo "remote: no command given" >&2
  exit 2
fi

if [[ "$SHELL_MODE" -eq 1 ]]; then
  COMMAND="$*"
else
  printf -v COMMAND '%q ' "$@"
  COMMAND="${COMMAND% }"
fi

exec sshpass -p "$HUAWEI_PASSWORD" ssh "${OPTS[@]}" "$HUAWEI_USER@$HUAWEI_HOST" "$COMMAND"
