#!/usr/bin/env bash
# One-command deployment for a fresh Ubuntu 22.04/24.04 VPS (run as root).
#
#   sudo bash deploy/setup_vps.sh <frontend-domain> <api-domain> <email-for-ssl>
#   e.g. sudo bash deploy/setup_vps.sh medicalai.softgoway.com api.medicalai.softgoway.com you@example.com
#
# Safe to re-run: it keeps the existing .env, demo password and database, and
# rebuilds/redeploys the latest code (run `git pull` first to update).
set -euo pipefail

FRONTEND_DOMAIN="${1:?usage: setup_vps.sh <frontend-domain> <api-domain> <email>}"
API_DOMAIN="${2:?usage: setup_vps.sh <frontend-domain> <api-domain> <email>}"
CERT_EMAIL="${3:?usage: setup_vps.sh <frontend-domain> <api-domain> <email>}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_ROOT=/var/www/medicalai
HTPASSWD=/etc/nginx/medicalai.htpasswd

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }
cd "$REPO_DIR"
log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

log "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y nginx certbot python3-certbot-nginx apache2-utils gettext-base curl git openssl ufw
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker nginx

# Whisper + Kokoro need memory; add swap on small servers.
if ! swapon --show | grep -q .; then
  log "Adding 4 GB swap"
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "Configuring firewall (SSH, HTTP, HTTPS only)"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

set_env() {  # set_env KEY VALUE -- replace or append in .env
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    echo "$1=$2" >> .env
  fi
}

if [ ! -f .env ]; then
  log "Creating .env with generated secrets"
  cp .env.example .env
  DB_PASS="$(openssl rand -hex 24)"
  set_env POSTGRES_PASSWORD "$DB_PASS"
  set_env DATABASE_URL "postgresql+psycopg://medicine_user:${DB_PASS}@db:5432/medicine_agent"
  set_env TEST_DATABASE_URL "postgresql+psycopg://medicine_user:${DB_PASS}@db:5432/medicine_agent_test"
  set_env DASHBOARD_TOKEN "$(openssl rand -hex 32)"
  set_env TELEPHONY_MONITOR_TOKEN "$(openssl rand -hex 32)"
  chmod 600 .env
fi
# Settings that must hold for this deployment (re-applied on every run).
set_env APP_ENV production
set_env API_PORT 8001
set_env API_BIND 127.0.0.1
set_env DASHBOARD_ENABLED true
set_env VOICE_PRELOAD_MODELS true
set_env CORS_ALLOWED_ORIGINS "https://${FRONTEND_DOMAIN}"
set_env TELEPHONY_PUBLIC_BASE_URL "https://${API_DOMAIN}"

log "Downloading Kokoro voice models (first run only)"
mkdir -p models/kokoro
KOKORO_URL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
for f in kokoro-v1.0.int8.onnx voices-v1.0.bin; do
  [ -s "models/kokoro/$f" ] || curl -fL --retry 3 -o "models/kokoro/$f" "$KOKORO_URL/$f"
done

log "Building and starting the API + PostgreSQL"
docker compose up -d --build

log "Building the React dashboard"
docker run --rm -v "$REPO_DIR/frontend:/w" -w /w node:22-alpine \
  sh -c "npm ci --no-audit --no-fund && npm run build"
mkdir -p "$WEB_ROOT"
rm -rf "${WEB_ROOT:?}"/*
cp -r frontend/dist/. "$WEB_ROOT/"
rm -f "$WEB_ROOT/.htaccess"

if [ ! -f "$HTPASSWD" ]; then
  DEMO_PASS="$(openssl rand -base64 12 | tr -d '/+=')"
  htpasswd -bc "$HTPASSWD" demo "$DEMO_PASS" >/dev/null
  chown root:www-data "$HTPASSWD" && chmod 640 "$HTPASSWD"
  echo "demo / $DEMO_PASS" > /root/medicalai-demo-login.txt
  chmod 600 /root/medicalai-demo-login.txt
fi

log "Configuring nginx"
env_value() { grep -m1 "^$1=" .env | cut -d= -f2-; }
API_PORT="$(env_value API_PORT)"
DASHBOARD_TOKEN="$(env_value DASHBOARD_TOKEN)"
TELEPHONY_MONITOR_TOKEN="$(env_value TELEPHONY_MONITOR_TOKEN)"
export FRONTEND_DOMAIN API_DOMAIN API_PORT DASHBOARD_TOKEN TELEPHONY_MONITOR_TOKEN
envsubst '${FRONTEND_DOMAIN} ${API_DOMAIN} ${API_PORT} ${DASHBOARD_TOKEN} ${TELEPHONY_MONITOR_TOKEN}' \
  < deploy/nginx.conf.template > /etc/nginx/sites-available/medicalai
chmod 640 /etc/nginx/sites-available/medicalai
ln -sf /etc/nginx/sites-available/medicalai /etc/nginx/sites-enabled/medicalai
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

log "Requesting HTTPS certificates (needs DNS already pointing here)"
certbot --nginx --non-interactive --agree-tos --redirect --keep-until-expiring -m "$CERT_EMAIL" \
  -d "$FRONTEND_DOMAIN" -d "$API_DOMAIN"

log "Waiting for the API to become healthy"
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:8001/health" >/dev/null 2>&1; then break; fi
  sleep 5
done
curl -fsS "http://127.0.0.1:8001/health" && echo \
  || echo "API not healthy yet (first start downloads models). Check: docker compose logs -f api"

log "Done"
echo "Dashboard : https://${FRONTEND_DOMAIN}   (login: $(cat /root/medicalai-demo-login.txt))"
echo "API docs  : https://${API_DOMAIN}/docs"
echo "Demo login is saved in /root/medicalai-demo-login.txt"
