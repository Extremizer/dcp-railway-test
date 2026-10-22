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
# Print ONLY bounded local-process / localhost classifications (no env, no raw HTML/logs).
diagnose() {
  docker inspect -f 'CONTAINER_STATE={{.State.Status}} CONTAINER_EXIT_CODE={{.State.ExitCode}}' "$cid"
  if [ "$(docker inspect -f '{{.State.Running}}' "$cid")" != "true" ]; then return; fi
  docker exec -i "$cid" python - <<'PY'
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
names=[]
for p in Path("/proc").iterdir():
    if p.name.isdigit():
        try: names.append((p/"comm").read_text().strip().lower())
        except (OSError,PermissionError): pass
print("DIAG_XVFB_PROCESS=" + ("PRESENT" if any("xvfb" in n for n in names) else "ABSENT"))
print("DIAG_CHROMIUM_PROCESS=" + ("PRESENT" if any(("chromium" in n or n=="chrome") for n in names) else "ABSENT"))
print("DIAG_PYTHON_PROCESS=" + ("PRESENT" if any(n.startswith("python") for n in names) else "ABSENT"))
roles={"XVFB_RUN_WRAPPER":False,"XAUTH":False,"PYTHON_MAIN":False,"XVFB_SERVER":False,"CHROMIUM_LAUNCH":False}
for p in Path("/proc").iterdir():
    if not p.name.isdigit(): continue
    try:
        argv=(p/"cmdline").read_bytes().decode("utf-8","replace").split(chr(0))
    except (OSError,PermissionError):continue
    joined=" ".join(argv).lower()
    if "xvfb-run" in joined: roles["XVFB_RUN_WRAPPER"]=True
    if any(x.endswith("/xauth") for x in argv): roles["XAUTH"]=True
    if "/app/dcp_linux_live_isolated.py" in joined:roles["PYTHON_MAIN"]=True
    if any(x.endswith("/xvfb") for x in argv):roles["XVFB_SERVER"]=True
    if any(x.endswith("/chromium") for x in argv):roles["CHROMIUM_LAUNCH"]=True
for key in sorted(roles): print("DIAG_ROLE_"+key+"="+("PRESENT" if roles[key] else "ABSENT"))

print("DIAG_XVFB_PROCESS_STATES=" + ",".join(sorted(set(
    ((p/"status").read_text().split("State:",1)[1].splitlines()[0].strip().split()[0])
    for p in Path("/proc").iterdir()
    if p.name.isdigit() and (p/"comm").is_file() and "xvfb" in (p/"comm").read_text().strip().lower()
))) if any("xvfb" in n for n in names) else "NONE")
from os import path
print("DIAG_X11_SOCKET=" + ("PRESENT" if any(p.name.startswith("X") for p in Path("/tmp/.X11-unix").glob("*")) else "ABSENT"))
print("DIAG_X11_LOCK=" + ("PRESENT" if any(p.name.startswith(".X") and p.name.endswith("-lock") for p in Path("/tmp").glob(".X*-lock")) else "ABSENT"))

try:
    with socket.create_connection(("127.0.0.1",9222),timeout=1): pass
    print("DIAG_CDP_TCP9222=LISTENING")
except OSError:
    print("DIAG_CDP_TCP9222=UNAVAILABLE")
try:
    with urllib.request.urlopen("http://127.0.0.1:9222/json",timeout=2) as response:
        payload=json.load(response)
    print("DIAG_CDP_HTTP_JSON=VALID" if isinstance(payload,list) else "DIAG_CDP_HTTP_JSON=INVALID")
    print("DIAG_CDP_ABOUT_BLANK=" + ("PRESENT" if isinstance(payload,list) and any(isinstance(x,dict) and x.get("type")=="page" and x.get("url")=="about:blank" for x in payload) else "ABSENT"))
except (OSError,ValueError,TypeError):
    print("DIAG_CDP_HTTP_JSON=UNAVAILABLE")
    print("DIAG_CDP_ABOUT_BLANK=UNVERIFIED")
try:
    with urllib.request.urlopen("http://127.0.0.1:8080/health",timeout=2) as r:
        print("DIAG_HEALTH_HTTP_STATUS="+str(r.status))
except urllib.error.HTTPError as exc:
    print("DIAG_HEALTH_HTTP_STATUS="+str(exc.code))
except OSError:
    print("DIAG_HEALTH_HTTP_STATUS=UNAVAILABLE")
PY
}
# Check CDP localhost from *inside* container only, with no external DNS or HTTP.
for attempt in $(seq 1 30); do
  if [ "$attempt" -eq 5 ] || [ "$attempt" -eq 15 ]; then
    echo "OFFLINE_STARTUP_DIAGNOSTIC_ATTEMPT=$attempt"
    if docker logs "$cid" 2>&1 | grep -q 'LINUX_CHROMIUM_RUNTIME_STARTING_ADMISSION_CLOSED'; then
      echo "DIAG_PYTHON_RUNTIME_ENTRY=REACHED"
    else
      echo "DIAG_PYTHON_RUNTIME_ENTRY=NOT_REACHED"
    fi
    if docker logs "$cid" 2>&1 | grep -q 'LINUX_PRODUCER_BLOCKED'; then
      echo "DIAG_ADMISSION_BLOCKED_MARKER=PRESENT"
    fi
    diagnose
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "$cid")" != "true" ]; then
    echo "ADMITTED_CONTAINER_EXITED"
    docker inspect -f 'EXIT_CODE={{.State.ExitCode}}' "$cid"
    for code in browser_terminated_during_startup browser_loopback_unavailable existing_persistent_state_dir_required interactive_display_required explicit_installed_chromium_required missing_owner_telegram_auth ephemeral_or_root_state_forbidden invalid_http_port; do
      if docker logs "$cid" 2>&1 | grep -Fq "HostConfigurationError $code"; then echo "SAFE_BROWSER_ERROR=$code"; fi
    done
    # One bounded sandbox-compatibility probe on an identically confined,
    # non-root, network-disabled container. No UID maps, elevation, or bypass.
    docker inspect -f 'DIAG_RUNTIME_UID={{.Config.User}} DIAG_NETWORK={{.HostConfig.NetworkMode}} DIAG_CAP_DROP={{json .HostConfig.CapDrop}} DIAG_SECURITY_OPTS={{json .HostConfig.SecurityOpt}}' "$cid"
    docker run --rm --network none --cap-drop=ALL --security-opt=no-new-privileges \
      --entrypoint python dcp-live-candidate:ci -c '
import ctypes, errno, os, pathlib, stat, subprocess, sys
root=pathlib.Path("/proc")
status={}
try:
    for line in (root/"self/status").read_text().splitlines():
        if line.partition(":")[0] in {"NoNewPrivs","Seccomp","CapEff","CapBnd"}:
            key,value=line.split(":",1);status[key]=value.strip()
except OSError: pass
print("DIAG_PROC_UID="+str(os.getuid()))
print("DIAG_NO_NEW_PRIVS="+status.get("NoNewPrivs","UNKNOWN"))
print("DIAG_SECCOMP_MODE="+status.get("Seccomp","UNKNOWN"))
print("DIAG_CAP_EFF_ZERO="+str(int(status.get("CapEff","1"),16)==0))
for name,file in (
 ("UNPRIV_USERNS_CLONE","/proc/sys/kernel/unprivileged_userns_clone"),
 ("MAX_USER_NAMESPACES","/proc/sys/user/max_user_namespaces"),
 ("APPARMOR_RESTRICT_USERNS","/proc/sys/kernel/apparmor_restrict_unprivileged_userns"),
):
    try:
        value=pathlib.Path(file).read_text().strip()
        if not value.isdigit(): value="UNKNOWN"
    except OSError: value="UNAVAILABLE"
    print("DIAG_"+name+"="+value)
try:
    profile=(root/"self/attr/current").read_text().strip().lower()
    kind=("DOCKER_DEFAULT" if "docker-default" in profile else
          "UNCONFINED" if "unconfined" in profile else
          "OTHER_ENFORCED" if "enforce" in profile else "UNKNOWN")
except OSError: kind="UNAVAILABLE"
print("DIAG_APPARMOR_PROFILE="+kind)
found=False
for item in ("/usr/lib/chromium/chrome-sandbox",
             "/usr/lib/chromium/chromium-sandbox",
             "/usr/bin/chrome-sandbox"):
    try:
        st=os.stat(item)
        if stat.S_ISREG(st.st_mode):
            found=True
            print("DIAG_SETUID_SANDBOX_HELPER="+("ROOT_SETUID" if
                st.st_uid==0 and st.st_mode & stat.S_ISUID else "NOT_ROOT_SETUID"))
            break
    except OSError: pass
if not found:print("DIAG_SETUID_SANDBOX_HELPER=NOT_INSTALLED")
# No uid mapping or privileged operation; the disposable child only attempts
# a new, unmapped user namespace and reports the denial errno.
code="import ctypes,errno; c=ctypes.CDLL(None,use_errno=True); r=c.unshare(0x10000000); e=ctypes.get_errno(); print(chr(65)+chr(76)+chr(76)+chr(79)+chr(87)+chr(69)+chr(68) if r==0 else errno.errorcode.get(e,chr(85)+chr(78)+chr(75)+chr(78)+chr(79)+chr(87)+chr(78)))"
try:
    r=subprocess.run([sys.executable,"-c",code],capture_output=True,text=True,timeout=3,check=False)
    value=r.stdout.strip() if r.returncode==0 else "EXEC_FAILED"
except (OSError,subprocess.TimeoutExpired): value="UNAVAILABLE"
print("DIAG_UNMAPPED_USERNS_UNSHARE="+value if value in
    {"ALLOWED","EPERM","EACCES","EINVAL","ENOSYS","EXEC_FAILED","UNAVAILABLE"} else
    "DIAG_UNMAPPED_USERNS_UNSHARE=OTHER_ERRNO")
'
    # Only on a failed offline startup, run Chromium with the same hardening
    # and no network, no DCP path, and classify stderr without exposing it.
    set +e
    timeout --signal=TERM --kill-after=2s 8s docker run --rm --network none --cap-drop=ALL --security-opt=no-new-privileges \
      --entrypoint /usr/bin/chromium dcp-live-candidate:ci \
      --headless --disable-gpu --no-first-run --user-data-dir=/tmp/ci-browser-diagnostic about:blank > chromium-diag.log 2>&1
    chromium_code=$?
    set -e
    echo "SAFE_CHROMIUM_DIAG_EXIT_CODE=$chromium_code"
    if grep -Eqi 'no usable sandbox|sandbox.*failed|zygote.*sandbox|namespace|operation not permitted' chromium-diag.log; then echo "SAFE_CHROMIUM_DIAG=SANDBOX_OR_NAMESPACE"; fi
    if grep -Eqi 'Failed to move to new namespace|Operation not permitted|No usable sandbox|Failed to unshare' chromium-diag.log; then echo "DIAG_CHROMIUM_NAMESPACE_CREATION_DENIED=TRUE"; fi
    if grep -Eqi 'No usable sandbox' chromium-diag.log; then echo "DIAG_CHROMIUM_NO_USABLE_SANDBOX=TRUE"; fi
    if grep -Eqi 'setuid.*sandbox|suid.*sandbox|chrome-sandbox' chromium-diag.log; then echo "DIAG_CHROMIUM_SETUID_SANDBOX_CONFLICT=TRUE"; fi
    if grep -Eqi 'apparmor' chromium-diag.log; then echo "DIAG_CHROMIUM_APPARMOR_MENTION=TRUE"; fi
    if grep -Eqi 'seccomp' chromium-diag.log; then echo "DIAG_CHROMIUM_SECCOMP_MENTION=TRUE"; fi
    if grep -Eqi 'permission denied|access denied|read-only file system' chromium-diag.log; then echo "SAFE_CHROMIUM_DIAG=FILE_PERMISSION"; fi
    if grep -Eqi 'missing.*shared libraries|error while loading shared libraries|cannot open shared object file' chromium-diag.log; then echo "SAFE_CHROMIUM_DIAG=LIBRARY_MISSING"; fi
    if grep -Eqi 'failed to connect.*display|cannot open display|no display' chromium-diag.log; then echo "SAFE_CHROMIUM_DIAG=DISPLAY_UNAVAILABLE"; fi
    for name in LINUX_PRODUCER_BLOCKED LOCAL_XVFB_START_EXITED LOCAL_XVFB_START_TIMEOUT LOCAL_XVFB_SOCKET_READY DIAG_MAIN_ENTERED DIAG_ADMISSION_VERIFIED DIAG_BROWSER_CONFIG_VERIFIED DIAG_JOURNAL_READY LINUX_CHROMIUM_RUNTIME_STARTING_ADMISSION_CLOSED; do
      if docker logs "$cid" 2>&1 | grep -q "$name"; then echo "EARLY_EXIT_MARKER=$name"; fi
    done
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
if docker logs "$cid" 2>&1 | grep -q 'LINUX_CHROMIUM_RUNTIME_STARTING_ADMISSION_CLOSED'; then
  echo "DIAG_PYTHON_RUNTIME_ENTRY=REACHED"
else
  echo "DIAG_PYTHON_RUNTIME_ENTRY=NOT_REACHED"
fi
diagnose
docker inspect -f 'STATE={{.State.Status}} EXIT_CODE={{.State.ExitCode}}' "$cid"
exit 1
