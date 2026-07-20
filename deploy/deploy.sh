#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh — Deploy SisiBet to a Ubuntu VPS
#
# Run once from your LOCAL machine to set up the server from scratch:
#   bash deploy.sh setup
#
# Run on every code push to update the live site:
#   bash deploy.sh update
#
# Requirements (local):
#   - SSH access to the server as root (or a sudo user)
#   - git, rsync installed locally
#
# Requirements (server):
#   - Ubuntu 22.04 LTS
#   - Ports 22, 80, 443 open in firewall
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── CONFIG — edit these ──────────────────────────────────────────────────────
SERVER_IP="YOUR_SERVER_IP"          # e.g. 196.30.12.45
SERVER_USER="root"                  # or your sudo user
DOMAIN="sisibet.com"
APP_DIR="/var/www/sisibet"
APP_USER="sisibet"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"   # project root (one level up from deploy/)
# ────────────────────────────────────────────────────────────────────────────

function log { echo -e "\033[1;32m▶ $*\033[0m"; }
function err { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

# ── SETUP — run once ─────────────────────────────────────────────────────────
function setup() {
    log "=== SisiBet first-time server setup ==="

    ssh "$SERVER_USER@$SERVER_IP" bash <<ENDSSH
set -euo pipefail

# 1. System packages
apt-get update -qq
apt-get install -y python3.11 python3.11-venv python3-pip nodejs npm nginx certbot python3-certbot-nginx git

# 2. Create app user
id $APP_USER &>/dev/null || useradd --system --home $APP_DIR --shell /bin/bash $APP_USER

# 3. App directory
mkdir -p $APP_DIR/backend $APP_DIR/frontend
chown -R $APP_USER:$APP_USER $APP_DIR

# 4. Python virtual environment
sudo -u $APP_USER python3.11 -m venv $APP_DIR/venv

echo "Server packages installed."
ENDSSH

    log "Uploading backend code..."
    rsync -az --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' \
        "$REPO_DIR/backend/" "$SERVER_USER@$SERVER_IP:$APP_DIR/backend/"

    log "Installing Python dependencies..."
    ssh "$SERVER_USER@$SERVER_IP" bash <<ENDSSH
$APP_DIR/venv/bin/pip install --quiet --upgrade pip
$APP_DIR/venv/bin/pip install --quiet -r $APP_DIR/backend/requirements.txt
ENDSSH

    log "Building frontend..."
    (cd "$REPO_DIR/frontend" && npm ci && npm run build)

    log "Uploading frontend build..."
    rsync -az "$REPO_DIR/frontend/dist/" "$SERVER_USER@$SERVER_IP:$APP_DIR/frontend/dist/"

    log "Installing systemd service..."
    scp "$REPO_DIR/deploy/sisibet-backend.service" "$SERVER_USER@$SERVER_IP:/etc/systemd/system/"

    log "Installing Nginx config..."
    scp "$REPO_DIR/deploy/nginx.conf" "$SERVER_USER@$SERVER_IP:/etc/nginx/sites-available/sisibet"

    ssh "$SERVER_USER@$SERVER_IP" bash <<ENDSSH
# Enable nginx site
ln -sf /etc/nginx/sites-available/sisibet /etc/nginx/sites-enabled/sisibet
rm -f /etc/nginx/sites-enabled/default
nginx -t

# Own the app files
chown -R $APP_USER:$APP_USER $APP_DIR

# Enable + start services
systemctl daemon-reload
systemctl enable sisibet-backend
systemctl start sisibet-backend
systemctl reload nginx

echo ""
echo "==================================="
echo "Setup complete!"
echo ""
echo "NEXT STEPS:"
echo "  1. Upload your .env file:"
echo "     scp deploy/.env.production $SERVER_USER@$SERVER_IP:$APP_DIR/backend/.env"
echo "     sudo systemctl restart sisibet-backend"
echo ""
echo "  2. Get SSL certificate (once DNS is pointed at $SERVER_IP):"
echo "     sudo certbot --nginx -d $DOMAIN -d www.$DOMAIN"
echo "==================================="
ENDSSH
}

# ── UPDATE — run on every deploy ─────────────────────────────────────────────
function update() {
    log "=== Deploying update to $DOMAIN ==="

    log "Building frontend..."
    (cd "$REPO_DIR/frontend" && npm ci && npm run build)

    log "Syncing frontend..."
    rsync -az --delete "$REPO_DIR/frontend/dist/" "$SERVER_USER@$SERVER_IP:$APP_DIR/frontend/dist/"

    log "Syncing backend..."
    rsync -az --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' \
        "$REPO_DIR/backend/" "$SERVER_USER@$SERVER_IP:$APP_DIR/backend/"

    log "Updating dependencies..."
    ssh "$SERVER_USER@$SERVER_IP" \
        "$APP_DIR/venv/bin/pip install --quiet -r $APP_DIR/backend/requirements.txt"

    log "Restarting backend..."
    ssh "$SERVER_USER@$SERVER_IP" "systemctl restart sisibet-backend"

    log "Reloading Nginx..."
    ssh "$SERVER_USER@$SERVER_IP" "systemctl reload nginx"

    log "=== Deploy complete ✓ ==="
    echo "  Live at: https://$DOMAIN"
}

# ── LOGS ─────────────────────────────────────────────────────────────────────
function logs() {
    ssh "$SERVER_USER@$SERVER_IP" "journalctl -u sisibet-backend -n 100 -f"
}

# ── STATUS ───────────────────────────────────────────────────────────────────
function status() {
    ssh "$SERVER_USER@$SERVER_IP" bash <<'ENDSSH'
echo "── Backend ──────────────────────────"
systemctl status sisibet-backend --no-pager
echo ""
echo "── Nginx ────────────────────────────"
systemctl status nginx --no-pager
echo ""
echo "── Health check ─────────────────────"
curl -sf http://localhost:8010/health || echo "Backend not responding"
ENDSSH
}

# ── DISPATCH ─────────────────────────────────────────────────────────────────
case "${1:-help}" in
    setup)  setup  ;;
    update) update ;;
    logs)   logs   ;;
    status) status ;;
    *)
        echo "Usage: bash deploy.sh [setup|update|logs|status]"
        echo ""
        echo "  setup   — first-time server provisioning"
        echo "  update  — push latest code and restart"
        echo "  logs    — tail backend logs"
        echo "  status  — show service status"
        ;;
esac
