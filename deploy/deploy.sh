#!/usr/bin/env bash
# VesperClaw VPS deploy (Ubuntu/Debian). Run as root.
#
#   bash deploy/deploy.sh            # first-time setup
#   bash deploy/deploy.sh update     # pull latest + restart services
#
# Idempotent: safe to re-run. Does NOT overwrite an existing .env.
set -euo pipefail

APP_DIR=/opt/vesperclaw
REPO_URL="${REPO_URL:-https://github.com/YOUR_USERNAME/vesperclaw.git}"
ACTION="${1:-setup}"

echo "==> VesperClaw deploy ($ACTION)"

if [ "$ACTION" = "update" ]; then
  cd "$APP_DIR"
  git pull
  "$APP_DIR/.venv/bin/pip" install -q -r requirements.txt
  systemctl restart vesperclaw-loop vesperclaw-dashboard
  echo "==> Updated and restarted."
  exit 0
fi

# ── first-time setup ──
apt-get update -y
apt-get install -y python3-venv python3-pip git

# clone or copy code into place
if [ ! -d "$APP_DIR/.git" ] && [ ! -f "$APP_DIR/main.py" ]; then
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
mkdir -p "$APP_DIR/logs" "$APP_DIR/data"

# python env
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip -q
./.venv/bin/pip install -q -r requirements.txt

# env file (created once; fill in your keys)
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "==> Created $APP_DIR/.env — EDIT IT and add QWEN_API_KEY before services run."
fi

# install + start services
cp "$APP_DIR/deploy/vesperclaw-loop.service" /etc/systemd/system/
cp "$APP_DIR/deploy/vesperclaw-dashboard.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable vesperclaw-loop vesperclaw-dashboard
systemctl restart vesperclaw-loop vesperclaw-dashboard

# open the dashboard port if ufw is active
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 8501/tcp || true
fi

echo "==> Done."
echo "    Dashboard: http://<your-vps-ip>:8501"
echo "    Logs:      journalctl -u vesperclaw-loop -f"
echo "    Edit keys: nano $APP_DIR/.env  (then: systemctl restart vesperclaw-loop)"
