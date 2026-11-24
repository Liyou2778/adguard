#!/usr/bin/env bash
# Run ON the ECS: deploy a gateway release as canary, then verify.
# Usage: remote-deploy-gateway.sh <release-tar-gz> <version>
set -euo pipefail
TARBALL="${1:?release tarball required}"
VERSION="${2:?version required}"
BASE=/opt/adguard
RELEASE="$BASE/releases/$VERSION"

# rm -rf: if a previous deploy failed halfway, the directory/symlink state must be replaced,
# and ln -s onto an existing symlink-to-directory would otherwise create a nested link.
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
tar -xzf "$TARBALL" -C "$RELEASE"
if [ ! -x "$RELEASE/gateway" ]; then chmod +x "$RELEASE/gateway"; fi
rm -f "$BASE/canary"
ln -s "$RELEASE" "$BASE/canary"
systemctl daemon-reload
systemctl restart adguard-gateway-canary
sleep 2
systemctl is-active adguard-gateway-canary
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:28080/healthz >/dev/null 2>&1; then
    echo "CANARY_HEALTHY version=$VERSION"
    exit 0
  fi
  sleep 1
done
echo "CANARY_UNHEALTHY version=$VERSION" >&2
journalctl -u adguard-gateway-canary -n 30 --no-pager >&2
exit 1
