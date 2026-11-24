#!/usr/bin/env bash
# Install the P2 AdGuard systemd units (run on the ECS as root, after deploy/ecs has been
# synced to /opt/adguard-src or /opt/adguard/releases). Idempotent.
#
# Units installed: gateway-stable, gateway-canary, control-plane, pipeline.
# canary is installed but NOT enabled: it is started on demand by remote-deploy-gateway.sh.
set -euo pipefail

SRC_DIR="${1:-/opt/adguard-src/deploy/ecs}"
BASE=/opt/adguard
DEST=/etc/systemd/system

echo "[1/3] copy unit files from $SRC_DIR"
install -m 644 "$SRC_DIR/adguard-gateway-stable.service" "$DEST/adguard-gateway-stable.service"
install -m 644 "$SRC_DIR/adguard-gateway-canary.service" "$DEST/adguard-gateway-canary.service"
install -m 644 "$SRC_DIR/adguard-control-plane.service" "$DEST/adguard-control-plane.service"
install -m 644 "$SRC_DIR/adguard-pipeline.service" "$DEST/adguard-pipeline.service"

echo "[2/3] daemon-reload + enable stable/control-plane/pipeline"
systemctl daemon-reload
systemctl enable adguard-gateway-stable.service >/dev/null 2>&1
systemctl enable adguard-control-plane.service >/dev/null 2>&1
systemctl enable adguard-pipeline.service >/dev/null 2>&1
systemctl disable adguard-gateway-canary.service >/dev/null 2>&1 || true

echo "[3/3] check env files exist"
for env in gateway control-plane pipeline; do
  if [ ! -f "$BASE/config/$env.env" ]; then
    echo "WARN: $BASE/config/$env.env missing (copy from deploy/ecs/config/$env.env.example and fill secrets)" >&2
  else
    chmod 600 "$BASE/config/$env.env" || true
  fi
done
echo "UNITS_INSTALLED"
