#!/usr/bin/env bash
# Run ON the ECS: cut over canary -> stable, or roll back to the previous release.
# Usage:
#   remote-cutover-or-rollback.sh cutover              # promote canary to stable (canary keeps running)
#   remote-cutover-or-rollback.sh rollback [version]   # point stable back to previous/known-good release
#
# IMPORTANT: cutover does NOT stop the canary service and does NOT reset nginx. The caller must
# switch nginx back to 0% and only then stop canary (otherwise 100%-canary traffic hits a dead
# upstream during the promotion window).
set -euo pipefail
BASE=/opt/adguard
MODE="${1:-cutover}"

if [ "$MODE" = "cutover" ]; then
  TARGET=$(readlink -f "$BASE/canary")
  PREV=$(readlink -f "$BASE/current" || true)
  if [ -z "$TARGET" ] || [ ! -x "$TARGET/gateway" ]; then
    echo "no canary release to promote" >&2
    exit 1
  fi
  echo "promoting $TARGET (previous: ${PREV:-none})"
  mkdir -p "$BASE/releases"
  echo "$PREV" > "$BASE/releases/.previous"
  rm -f "$BASE/current"
  ln -s "$TARGET" "$BASE/current"
  systemctl restart adguard-gateway-stable
  sleep 2
  for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:18080/healthz >/dev/null 2>&1; then
      echo "STABLE_HEALTHY $(basename "$TARGET")"
      exit 0
    fi
    sleep 1
  done
  echo "STABLE_UNHEALTHY after promote - rolling back" >&2
  if [ -n "$PREV" ] && [ -x "$PREV/gateway" ]; then
    rm -f "$BASE/current"
    ln -s "$PREV" "$BASE/current"
    systemctl restart adguard-gateway-stable
    sleep 3
    curl -fsS http://127.0.0.1:18080/healthz >/dev/null 2>&1 && echo "ROLLED_BACK_OK" || echo "ROLLBACK_FAILED" >&2
  fi
  exit 1
fi

# rollback [version]
PREVIOUS="${2:-$(cat "$BASE/releases/.previous" 2>/dev/null || true)}"
if [ -z "$PREVIOUS" ] || [ ! -x "$PREVIOUS/gateway" ]; then
  echo "no previous release to roll back to" >&2
  exit 1
fi
echo "rolling back stable to $PREVIOUS"
rm -f "$BASE/current"
ln -s "$PREVIOUS" "$BASE/current"
systemctl restart adguard-gateway-stable
sleep 2
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:18080/healthz >/dev/null 2>&1; then
    echo "ROLLBACK_OK $(basename "$PREVIOUS")"
    exit 0
  fi
  sleep 1
done
echo "ROLLBACK_FAILED" >&2
exit 1
