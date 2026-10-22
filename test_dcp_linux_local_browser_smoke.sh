#!/usr/bin/env bash
# Isolated positive startup smoke: local-only Xvfb + Chromium; zero DCP network access.
set -euo pipefail
state="$(mktemp -d)"
cid=""
cleanup() {
  if [ -n "$cid" ]; then docker rm -f "$cid" >/dev/null 2>&1 || true; fi
  sudo rm -rf -- "$state"
}
trap cleanup EXIT
sudo chown 10001:10001 "$state"
sudo chmod 0700 "$state"
cid="$(docker run -d --network none --cap-drop=ALL --security-opt=no-new-privileges \
  --mount "type=bind,source=$state,target=/data" \
  --env DCP_PHONE_STAGING_MOCK_ONLY=0 \
  --env DCP_CLOUD_PRODUCER_ENABLED=0 \
  --env DCP_CLOUD_BROWSER_HOST_ENABLED=1 \
  --env DCP_PHONE_RECOVERY_ENABLED=1 \
  --env EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT=fixture@example.invalid \
  --env EXTREMIZER_DP_OWNERSHIP_ENFORCE=1 \
  --env DCP_RECOVERY_TELEGRAM_BOT_TOKEN=synthetic-ci-token-not-valid \
  --env DCP_RECOVERY_OWNER_ID=12345 \
  --env DCP_CLOUD_STATE_DIR=/data \
  --env DCP_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
  --env "DCP_EXPECTED_SOURCE_HEAD=$GITHUB_SHA" \
  --env DCP_LIVE_NAVIGATION_GO=0 \
  --env PORT=8080 \
  dcp-live-candidate:ci)"
echo "ISOLATED_TEST_NETWORK_NONE=TRUE"
echo "DCP_NAVIGATION_GO=OFF"
echo "BROKER_CLAIMS=DISABLED"
# Check CDP localhost from *inside* container only, with no external DNS or HTTP.
for attempt in $(seq 1 30); do
  if [ "$(docker inspect -f '{{.State.Running}}' "$cid")" != "true" ]; then
    echo "ADMITTED_CONTAINER_EXITED"
    docker inspect -f 'EXIT_CODE={{.State.ExitCode}}' "$cid"
    exit 1
  fi
  if docker exec "$cid" python -c '
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=2) as response:
    pages=json.load(response)
assert isinstance(pages,list)
assert any(p.get("type")=="page" and p.get("url")=="about:blank" for p in pages)
' >/dev/null 2>&1; then
    echo "CDP_LOCALHOST_ABOUT_BLANK_PASS"
    docker exec "$cid" python -c '
from pathlib import Path
processes=[]
for p in Path("/proc").iterdir():
    if p.name.isdigit():
        try: processes.append((p/"comm").read_text().strip().lower())
        except (OSError,PermissionError): pass
assert any("xvfb" in name for name in processes), "XVFB_PROCESS_MISSING"
assert any(("chromium" in name or name=="chrome") for name in processes), "CHROMIUM_PROCESS_MISSING"
print("XVFB_CHROMIUM_PROCESSES_PASS")
'
    docker exec "$cid" python -c '
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as r:
    assert r.status == 200
print("LOCAL_HEALTH_HTTP_PASS")
'
    echo "ISOLATED_ADMITTED_XVFB_CHROMIUM_PASS"
    exit 0
  fi
  sleep 2
done
echo "LOCAL_XVFB_CHROMIUM_STARTUP_TIMEOUT"
docker inspect -f 'STATE={{.State.Status}} EXIT_CODE={{.State.ExitCode}}' "$cid"
exit 1
