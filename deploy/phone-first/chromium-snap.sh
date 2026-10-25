#!/bin/sh
# Clean browser environment: never inherit Telegram or control credentials.
set -eu
[ "$(id -un)" = extremizer-dcp ] || exit 24
runtime_dir="/run/user/$(id -u)"
exec env -i HOME=/home/extremizer-dcp USER=extremizer-dcp LOGNAME=extremizer-dcp \
 PATH=/usr/bin:/bin:/snap/bin LANG=C.UTF-8 \
 DISPLAY="${DISPLAY:?}" XAUTHORITY="${XAUTHORITY:?}" \
 XDG_RUNTIME_DIR="$runtime_dir" DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus" \
 /snap/bin/chromium --disable-gpu --disable-background-networking --disable-component-update \
 --window-size=1280,800 "$@"
