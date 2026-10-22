"""Single Linux persistent Chromium/owner-login candidate. No broker claims without security gate.

Uses established DCP cloud browser host + owner-authorized recovery, never a
Windows cookie. Separates runtime health from signed ownership admission.
No live lookup, cart mutation, or price completion implemented in this entry.
"""
import hmac
import os
import sys
from pathlib import Path
from dcp_linux_admission import persist_journal, AdmissionError
from dcp_cloud_browser_host import configuration as browser_configuration, run as browser_run, HostConfigurationError

class AdmissionDenied(RuntimeError):pass

def admission(env):
    """Configuration admission only; not a proof of an actual authenticated session."""
    if sys.platform!="linux":raise AdmissionDenied("LINUX_REQUIRED")
    if env.get("DCP_PHONE_STAGING_MOCK_ONLY")!="0":raise AdmissionDenied("MOCK_BROWSER_MODE_FORBIDDEN")
    if env.get("DCP_CLOUD_PRODUCER_ENABLED")!="0":raise AdmissionDenied("CLAIM_PROCESS_MUST_BE_SEPARATELY_ADMITTED")
    if env.get("DCP_CLOUD_BROWSER_HOST_ENABLED")!="1" or env.get("DCP_PHONE_RECOVERY_ENABLED")!="1":
        raise AdmissionDenied("OWNER_OPERATED_BROWSER_REQUIRED")
    if not env.get("EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT"):
        raise AdmissionDenied("EXPECTED_DEALER_ACCOUNT_MISSING")
    if env.get("EXTREMIZER_DP_OWNERSHIP_ENFORCE")!="1":
        raise AdmissionDenied("OWNERSHIP_FENCING_NOT_ENABLED")
    state=Path(env.get("DCP_CLOUD_STATE_DIR",""))
    if not state.is_absolute() or not state.is_dir() or state.is_symlink():
        raise AdmissionDenied("PERSISTENT_JOURNAL_REQUIRED")
    for name in ("DCP_RECOVERY_TELEGRAM_BOT_TOKEN","DCP_RECOVERY_OWNER_ID"):
        if not env.get(name):raise AdmissionDenied("PROTECTED_OWNER_LOGIN_MISSING")
    # Source integrity MUST be tied to a build-supplied exact HEAD, not an ENV assertion.
    baked=Path("/app/dcp_build_source_head")
    reviewed=env.get("DCP_EXPECTED_SOURCE_HEAD","")
    if not baked.is_file() or len(reviewed)!=40 or baked.read_text().strip()!=reviewed:
        raise AdmissionDenied("SOURCE_IDENTITY_UNVERIFIED")
    return True

def prepare_journal(state):
    root=Path(state)
    journal=root/"linux-producer-journal.json"
    if not journal.exists():
        # First use ONLY: refuse to silently reinitialize a used persistent volume.
        if any(root.iterdir()):
            raise AdmissionDenied("EXISTING_PROFILE_WITHOUT_JOURNAL_QUARANTINED")
        persist_journal(root,{"state":"CLEAN","claim":None,"status":"NEW_PROFILE"})
    if journal.is_symlink():
        raise AdmissionDenied("JOURNAL_SYMLINK")

def main():
    try:
        admission(os.environ)
        config=browser_configuration()
        prepare_journal(config["state"])
        print("LINUX_CHROMIUM_RUNTIME_STARTING_ADMISSION_CLOSED",flush=True)
        browser_run(config,app_module='dcp_linux_live_app:app')
        return 0
    except (AdmissionDenied,AdmissionError,HostConfigurationError,BlockingIOError,ValueError,OSError) as exc:
        print("LINUX_PRODUCER_BLOCKED",type(exc).__name__,flush=True)
        return 24

if __name__=="__main__":raise SystemExit(main())
