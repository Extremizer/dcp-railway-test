#!/bin/sh
# Railway isolated volume ownership handoff, before unprivileged browser launch.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "VOLUME_INIT_BLOCKED_NOT_ROOT"; exit 24; }
[ "${DCP_CLOUD_STATE_DIR:-}" = "/data" ] || { echo "VOLUME_INIT_BLOCKED_STATE_PATH"; exit 24; }
[ -d /data ] && [ ! -L /data ] || { echo "VOLUME_INIT_BLOCKED_NO_DIRECTORY"; exit 24; }
python - <<'PY'
from pathlib import Path
mounts = Path('/proc/self/mountinfo').read_text().splitlines()
if not any(line.split(' - ', 1)[0].split()[4] == '/data' for line in mounts):
    raise SystemExit('VOLUME_INIT_BLOCKED_NOT_A_MOUNT')
PY
# Do not recurse into an existing dealer profile or touch its contents.
chown 10001:10001 /data
chmod 0700 /data
echo "VOLUME_OWNERSHIP_READY"
exec runuser -u dcp-runner -- sh /app/dcp_linux_xvfb_start.sh
