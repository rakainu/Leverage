#!/bin/bash
# SessionStart hook — prepares Claude Code on the web to reach the Leverage VPS
# and run trade-data audits.
#
# What it does (remote/web sessions only):
#   1. Installs openssh-client (absent from the base image).
#   2. Writes the dedicated VPS key from the $VPS_SSH_KEY secret to ~/.ssh and
#      registers an `ssh vps` alias (root@46.202.146.30 → srv1370094).
#   3. Pins the VPS host key (known_hosts) so connections aren't TOFU-prompted.
#   4. Installs the Python deps the live-audit scripts need (pandas/numpy).
#
# The private key NEVER lives in the repo — it is only ever read from the
# environment secret $VPS_SSH_KEY and written to ~/.ssh at runtime.
#
# Idempotent and non-interactive. Safe to re-run.
set -euo pipefail

# Local (non-web) sessions already have the user's keys + tooling — skip.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

VPS_HOST="46.202.146.30"
VPS_USER="root"
KEY_PATH="$HOME/.ssh/leverage_web_vps"

log() { echo "[session-start] $*"; }

# --- 1. openssh-client -----------------------------------------------------
if ! command -v ssh >/dev/null 2>&1; then
  log "installing openssh-client…"
  export DEBIAN_FRONTEND=noninteractive
  # Don't gate install on update: this sandbox carries broken third-party PPAs
  # (deadsnakes/php) that 403, making `apt-get update` exit non-zero even when
  # the main Ubuntu repo refreshes fine. Update best-effort, then install.
  apt-get update -qq >/dev/null 2>&1 || true
  if apt-get install -y -qq openssh-client >/dev/null 2>&1; then
    log "openssh-client installed"
  else
    log "ERROR: openssh-client install failed — VPS access unavailable"
  fi
else
  log "openssh-client already present"
fi

# --- 2. VPS key + ssh config ----------------------------------------------
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

if [ -n "${VPS_SSH_KEY:-}" ]; then
  printf '%s\n' "$VPS_SSH_KEY" > "$KEY_PATH"
  chmod 600 "$KEY_PATH"
  log "wrote VPS key to $KEY_PATH"

  # ssh config alias: `ssh vps`
  if ! grep -q "Host vps" "$HOME/.ssh/config" 2>/dev/null; then
    cat >> "$HOME/.ssh/config" <<EOF
Host vps
    HostName $VPS_HOST
    User $VPS_USER
    IdentityFile $KEY_PATH
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
EOF
    chmod 600 "$HOME/.ssh/config"
    log "added 'vps' ssh alias"
  fi

  # --- 3. pin host key (best-effort; needs egress to :22) ----------------
  if ! ssh-keygen -F "$VPS_HOST" >/dev/null 2>&1; then
    if ssh-keyscan -T 5 -t ed25519,rsa "$VPS_HOST" >> "$HOME/.ssh/known_hosts" 2>/dev/null; then
      chmod 600 "$HOME/.ssh/known_hosts"
      log "pinned VPS host key"
    else
      log "WARN: could not reach $VPS_HOST:22 to pin host key (egress not open yet?)"
    fi
  fi
else
  log "WARN: \$VPS_SSH_KEY not set — add it as an environment secret to enable VPS access"
fi

# --- 4. Python audit deps --------------------------------------------------
if ! python3 -c "import pandas, numpy" >/dev/null 2>&1; then
  log "installing pandas/numpy for trade audits…"
  python3 -m pip install -q --no-input pandas numpy >/dev/null 2>&1 || \
    log "WARN: pandas/numpy install failed (audits may not run)"
else
  log "pandas/numpy already present"
fi

log "done"
