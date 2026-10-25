"""Native VPS envelope for the existing PHONE-FIRST mock acceptance path.

No DCP navigation or broker process. Chromium still uses the shared launcher.
"""
import fcntl
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
from dcp_cloud_browser_host import configuration,run,mock_page_uri,HostConfigurationError

def validate(env):
    if os.geteuid()==0: raise HostConfigurationError('nonroot_required')
    if (env.get('DCP_PHONE_STAGING_MOCK_ONLY')!='1'
        or env.get('DCP_CLOUD_PRODUCER_ENABLED')!='0'
        or env.get('DCP_LIVE_NAVIGATION_GO')!='0'
        or env.get('DCP_RECOVERY_BIND_HOST')!='127.0.0.1'
        or env.get('DCP_RECOVERY_NOTIFICATIONS_ENABLED','0')!='0'
        or env.get('DCP_STAGING_TEST_NOTICE_ID')):
        raise HostConfigurationError('offline_owner_acceptance_only')
    baked=Path(__file__).with_name('dcp_build_source_head')
    expected=env.get('DCP_EXPECTED_SOURCE_HEAD','')
    if len(expected)!=40 or not baked.is_file() or baked.read_text().strip()!=expected:
        raise HostConfigurationError('source_identity_unverified')
    mock_page_uri(env)

def main():
    validate(os.environ)
    state=Path(os.environ['DCP_CLOUD_STATE_DIR'])
    descriptor=os.open(state/'vps-runtime.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    display=':99'; authority=state/'Xauthority'; xserver=None
    try:
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if Path('/tmp/.X11-unix/X99').exists():raise HostConfigurationError('display_already_in_use')
        if authority.is_symlink():raise HostConfigurationError('authority_symlink')
        authority.touch(mode=0o600,exist_ok=True);os.chmod(authority,0o600)
        subprocess.run(['xauth','-f',str(authority),'add',display,'.',secrets.token_hex(16)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        os.environ.update(DISPLAY=display,XAUTHORITY=str(authority))
        xserver=subprocess.Popen(['Xvfb',display,'-screen','0','1280x800x24','-nolisten','tcp','-auth',str(authority)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(50):
            if xserver.poll() is not None:raise HostConfigurationError('xvfb_exited')
            if Path('/tmp/.X11-unix/X99').exists():break
            time.sleep(.1)
        else:raise HostConfigurationError('xvfb_start_timeout')
        print('VPS_PHONE_FIRST_MOCK_STARTING',flush=True)
        run(configuration(),app_module='dcp_phone_vps_app:app')
        return 0
    finally:
        if xserver is not None and xserver.poll() is None:
            xserver.terminate()
            try:xserver.wait(timeout=5)
            except subprocess.TimeoutExpired:xserver.kill();xserver.wait()
        os.close(descriptor)

if __name__=='__main__':
    try:raise SystemExit(main())
    except (HostConfigurationError,OSError,ValueError,subprocess.SubprocessError) as exc:
        print('VPS_PHONE_FIRST_BLOCKED',type(exc).__name__,flush=True)
        raise SystemExit(24)
