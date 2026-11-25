#!/usr/bin/env bash
# Docker CLI wrapper that runs against the runtime server from .env.
set -euo pipefail

exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/remote.sh" docker "$@"
