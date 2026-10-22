"""Fail closed BEFORE spawning Xvfb or Chromium on unadmitted Linux hosts."""
import os
import sys
from dcp_linux_live_isolated import admission, AdmissionDenied
if __name__ == "__main__":
    try:
        admission(os.environ)
    except (AdmissionDenied, OSError, ValueError):
        print("LINUX_PRODUCER_BLOCKED AdmissionDenied",flush=True)
        sys.exit(24)
    sys.exit(0)
