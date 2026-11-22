#!/usr/bin/env bash
# Switch canary traffic percentage on the ECS nginx (M6 gray release).
# Usage: bash switch-canary.sh <0|10|50|100>
set -euo pipefail
PERCENT="${1:?usage: switch-canary.sh <0|10|50|100>}"
case "$PERCENT" in
  0|10|50|100) ;;
  *) echo "unsupported percentage: $PERCENT (allowed: 0,10,50,100)"; exit 1 ;;
esac

CONF=/etc/nginx/conf.d/adguard-canary.conf
python3 - "$PERCENT" "$CONF" <<'PY'
import re, sys
percent, path = sys.argv[1], sys.argv[2]
text = open(path).read()
text = re.sub(r'\{\n\s*\d+%\s+adguard_canary;', '{\n    %s%%     adguard_canary;' % percent, text, count=1)
open(path, 'w').write(text)
PY
nginx -t
systemctl reload nginx
echo "canary traffic: ${PERCENT}% -> :28080, rest -> :18080"

for i in $(seq 1 10); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/healthz || true)
  if [ "$code" = "200" ]; then echo "nginx healthz OK"; exit 0; fi
  sleep 1
done
echo "WARN: healthz not 200 after reload (last=$code)" >&2
exit 1
