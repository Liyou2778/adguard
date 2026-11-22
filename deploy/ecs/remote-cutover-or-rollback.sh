#!/usr/bin/env bash
# Run ON the ECS: cut over canary -> stable, or roll back to the previous release.
# Usage:
#   remote-cutover.sh                    # promote canary to stable (traffic already 100% canary)
#   remote-rollback.sh <previous-version>  # point stable back to a known-good release
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
  echo "promoting $TARGET (previous: $PREV)"
  echo "$PREV" > "$BASE/releases/.previous"
  ln -sfn "$TARGET" "$BASE/current"
  systemctl restart adguard-gateway-stable
  sleep 2
  for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:18080/healthz >/dev/null 2>&1; then
      echo "STABLE_HEALTHY $(basename "$TARGET")"
      systemctl stop adguard-gateway-canary || true
      exit 0
    fi
    sleep 1
  done
  echo "STABLE_UNHEALTHY after promote - rolling back" >&2
  if [ -n "$PREV" ] && [ -x "$PREV/gateway" ]; then
    ln -sfn "$PREV" "$BASE/current"
    systemctl restart adguard-gateway-stable
    sleep 3
    curl -fsS http://127.0.0.1:18080/healthz >/dev/null 2>&1 && echo "ROLLED_BACK_OK" || echo "ROLLBACK_FAILED" >&2
  fi
  exit 1
fi

# rollback <version>
PREVIOUS="${2:-$(cat "$BASE/releases/.previous" 2>/dev/null || true)}"
if [ -z "$PREVIOUS" ] || [ ! -x "$PREVIOUS/gateway" ]; then
  echo "no previous release to roll back to" >&2
  exit 1
fi
echo "rolling back stable to $PREVIOUS"
ln -sfn "$PREVIOUS" "$BASE/current"
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
