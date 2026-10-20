"""Offline-only Linux Chromium for owner phone acceptance, no DCP URL."""
import os,subprocess,sys,time,urllib.request,fcntl
from pathlib import Path

def main():
    if os.getenv("DCP_PHONE_STAGING_MOCK_ONLY")!="1":
        print("MOCK_ONLY_REQUIRED");return 24
    state=Path(os.getenv("DCP_CLOUD_STATE_DIR","/data"))
    if not state.is_dir() or not os.access(state,os.W_OK):
        print("PERSISTENT_VOLUME_REQUIRED");return 24
    fd=os.open(state/"staging.lock",os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    browser=None;xvfb=None
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        profile=state/"browser-profile"
        profile.mkdir(exist_ok=True,mode=0o700)
        xvfb=subprocess.Popen(["Xvfb",":92","-screen","0","1280x800x24","-nolisten","tcp"])
        env={**os.environ,"DISPLAY":":92"}
        browser=subprocess.Popen(["/usr/bin/chromium","--no-sandbox","--disable-dev-shm-usage",
          "--disable-background-networking","--no-first-run","--no-default-browser-check",
          "--remote-debugging-address=127.0.0.1","--remote-debugging-port=9222",
          "--remote-allow-origins=http://127.0.0.1:9222",
          "--user-data-dir="+str(profile),"--window-size=1280,800",
          "file:///app/dcp_phone_staging_mock.html"],env=env,
          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(50):
            if browser.poll() is not None:raise RuntimeError("browser_failed")
            try:
                with urllib.request.urlopen("http://127.0.0.1:9222/json/version",timeout=1):break
            except Exception:time.sleep(.2)
        else:raise RuntimeError("mock_cdp_unavailable")
        import uvicorn
        uvicorn.run("dcp_phone_staging_app:app",host="0.0.0.0",port=int(os.getenv("PORT","8080")),
            log_level="warning",access_log=False,workers=1)
        return 0
    except Exception as exc:
        print("STAGING_BLOCKED",type(exc).__name__)
        return 24
    finally:
        if browser and browser.poll() is None:browser.terminate()
        if xvfb and xvfb.poll() is None:xvfb.terminate()
        os.close(fd)

if __name__=="__main__":raise SystemExit(main())
