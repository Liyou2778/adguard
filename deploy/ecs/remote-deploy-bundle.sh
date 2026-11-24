#!/usr/bin/env bash
# Run ON the ECS: install a full deployment bundle (gateway + control-plane.jar + pipeline),
# point /opt/adguard/current at it and restart services.
# Usage: remote-deploy-bundle.sh <bundle-tar-gz> <version>
set -euo pipefail
TARBALL="${1:?bundle tarball required}"
VERSION="${2:?version required}"
BASE=/opt/adguard
RELEASE="$BASE/releases/$VERSION"
VENV="$BASE/venvs/pipeline"

echo "[1/5] extract bundle -> $RELEASE"
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
tar -xzf "$TARBALL" -C "$RELEASE"
chmod +x "$RELEASE/gateway"

echo "[2/5] python venv (shared, hashing embedder)"
mkdir -p "$BASE/venvs"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q --no-cache-dir -r "$RELEASE/pipeline-requirements.txt" \
    -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
fi
# pipeline lives as current/pipeline; expose source + venv under the release
rm -rf "$RELEASE/pipeline"
mkdir -p "$RELEASE/pipeline"
cp -r "$RELEASE/pipeline-src" "$RELEASE/pipeline/src"
cp -r "$RELEASE/pipeline-data" "$RELEASE/pipeline/data"
ln -s "$VENV" "$RELEASE/pipeline/venv"

echo "[3/5] point current -> $VERSION"
PREV=$(readlink -f "$BASE/current" || true)
echo "$PREV" > "$BASE/releases/.previous"
rm -f "$BASE/current"
ln -s "$RELEASE" "$BASE/current"

echo "[4/5] restart control-plane + pipeline + stable gateway"
systemctl restart adguard-control-plane
systemctl restart adguard-pipeline
if [ -x "$BASE/current/gateway" ]; then
  systemctl restart adguard-gateway-stable
fi

echo "[5/5] health checks"
check() {
  local name="$1" url="$2" tries=30
  for _ in $(seq 1 "$tries"); do
    if curl -fsS "$url" >/dev/null 2>&1; then echo "OK $name"; return 0; fi
    sleep 1
  done
  echo "FAIL $name ($url)" >&2
  journalctl -u "$name" -n 20 --no-pager >&2 || true
  return 1
}
check adguard-control-plane http://127.0.0.1:8081/actuator/health
check adguard-pipeline http://127.0.0.1:8090/healthz
check adguard-gateway-stable http://127.0.0.1:18080/healthz
echo "BUNDLE_DEPLOYED version=$VERSION"
