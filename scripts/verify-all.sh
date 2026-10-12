#!/usr/bin/env bash
set -Eeuo pipefail

# One-shot entry point for the E3-B07 parallel-test baseline.
# Run from anywhere: bash scripts/verify-all.sh
#
#   scripts/verify-draft.sh      E3-B07-001/002/003 (DRAFT sample + docker)
#   scripts/verify-md-fixer.sh   E3-B07-006 (MDFixer behaviour verification)
#
# Requires: git, make, a C compiler, sha256sum, and (for the DRAFT docker phases)
# a reachable Docker engine. On Windows run this from WSL or Git Bash; the DRAFT
# docker phases use the Docker CLI of whatever engine is on PATH.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

printf '== E3-B07 DRAFT ==\n'
bash "$ROOT/scripts/verify-draft.sh"

printf '\n== E3-B07 MDFixer ==\n'
bash "$ROOT/scripts/verify-md-fixer.sh"

printf '\nAll E3-B07 verifications complete.\n'
