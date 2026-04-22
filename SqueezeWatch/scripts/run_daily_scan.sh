#!/usr/bin/env bash
# SqueezeWatch daily scan wrapper.
#
# Invoked from root's crontab on the VPS:
#   30 6 * * * /root/SqueezeWatch/scripts/run_daily_scan.sh >> /var/log/squeezewatch.log 2>&1
#
# Uses absolute paths and sets PATH explicitly so it does not depend on
# an interactive shell. Fails fast on any unset variable or command error.
set -euo pipefail

PROJECT_ROOT="/root/SqueezeWatch"
VENV_PY="${PROJECT_ROOT}/.venv/bin/python"

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PYTHONUNBUFFERED=1

cd "${PROJECT_ROOT}"

echo ""
echo "===== SqueezeWatch scan @ $(date -u '+%Y-%m-%d %H:%M:%S UTC') ====="

exec "${VENV_PY}" -m src.main scan --log-level INFO
