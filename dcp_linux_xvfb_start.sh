#!/bin/sh
# Offline-safe Xvfb launcher; never starts browser before Python admission preflight.
set -eu
python -u /app/dcp_linux_preflight.py
export DISPLAY=:99
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/dev/null 2>&1 &
xvfb_pid=$!
cleanup() { kill "$xvfb_pid" 2>/dev/null || :; wait "$xvfb_pid" 2>/dev/null || :; }
trap cleanup EXIT HUP INT TERM
n=0
while [ "$n" -lt 30 ]; do
  if ! kill -0 "$xvfb_pid" 2>/dev/null; then
    echo "LOCAL_XVFB_START_EXITED"
    exit 24
  fi
  if [ -S /tmp/.X11-unix/X99 ]; then
    break
  fi
  sleep 1
  n=$((n+1))
done
if [ ! -S /tmp/.X11-unix/X99 ]; then
  echo "LOCAL_XVFB_START_TIMEOUT"
  exit 24
fi
echo "LOCAL_XVFB_SOCKET_READY"
python -u /app/dcp_linux_live_isolated.py
