"""VPS-side PHONE-FIRST DRY-RUN worker.

This worker only handles the SAFE-MFR / SAFE-OEM-000 staging fixture over the
existing signed-v2 producer contract. It does not import the live DCP agent,
does not use dealer credentials, and never writes dealer price cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from dp_cloud_producer_client import CloudV2Producer, ProducerContractError


SAFE_MANUFACTURER = "SAFE-MFR"
SAFE_OEM = "SAFE-OEM-000"
SAFE_STATUS = "DRY_RUN_OK"
DEFAULT_PRODUCER_ID = "cloud-vps-dryrun-0001"


class WorkerBlocked(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def signing_key(scoped_token: str) -> bytes:
    if not isinstance(scoped_token, str) or len(scoped_token.strip()) < 32:
        raise WorkerBlocked("dedicated_dp_signing_token_required")
    return hashlib.sha256(
        b"extremizer-dp-sync-v1\0" + scoped_token.strip().encode()
    ).digest()


def load_token_file(path: str | os.PathLike[str]) -> str:
    token_file = Path(path).expanduser()
    try:
        st = token_file.stat()
    except OSError as exc:
        raise WorkerBlocked("dp_sync_token_file_unreadable") from exc
    if not stat.S_ISREG(st.st_mode) or token_file.is_symlink():
        raise WorkerBlocked("dp_sync_token_file_invalid")
    if os.name == "posix" and (st.st_mode & 0o077):
        raise WorkerBlocked("dp_sync_token_file_permissions")
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise WorkerBlocked("dp_sync_token_file_unreadable") from exc
    if "\n" in token or "\r" in token:
        raise WorkerBlocked("dp_sync_token_file_invalid")
    signing_key(token)
    return token


def _valid_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url.rstrip("/"))
    if parsed.scheme == "https" and parsed.netloc:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    if (
        os.getenv("DCP_PHONE_DRYRUN_ALLOW_LOCAL_HTTP") == "1"
        and parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and parsed.netloc
    ):
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    raise WorkerBlocked("https_base_url_required")


def http_send_factory(base_url: str, *, timeout: float = 10.0):
    root = _valid_base_url(base_url)

    def send(method: str, path: str, body: bytes, headers: dict[str, str]) -> dict:
        if not path.startswith("/internal/dp-cloud/"):
            raise WorkerBlocked("path_not_allowed")
        request = urllib.request.Request(
            root + path,
            data=None if method.upper() == "GET" else body,
            method=method.upper(),
            headers={
                **headers,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read(1024 * 1024)
        except urllib.error.HTTPError as exc:
            raise WorkerBlocked(f"http_{exc.code}") from None
        except TimeoutError as exc:
            raise WorkerBlocked("http_timeout") from exc
        except OSError as exc:
            raise WorkerBlocked("http_transport_failed") from exc
        try:
            return json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise WorkerBlocked("invalid_json_response") from exc

    return send


def _safe_result(claim: dict | None) -> dict | None:
    if claim is None:
        return None
    if claim.get("manufacturer") != SAFE_MANUFACTURER or claim.get("oem") != SAFE_OEM:
        raise WorkerBlocked("unsafe_claim_rejected")
    return {
        "status": SAFE_STATUS,
        "manufacturer": SAFE_MANUFACTURER,
        "oem": SAFE_OEM,
    }


def run_once(producer: CloudV2Producer) -> dict:
    acquired = False
    try:
        producer.acquire()
        acquired = True
        claim = producer.next()
        result = _safe_result(claim)
        if result is None:
            return {"status": "NO_WORK"}
        ack = producer.complete(claim, result)
        return {
            "status": ack["status"],
            "request_id": claim["request_id"],
            "source": "vps-dry-run-no-dcp",
        }
    except (ProducerContractError, WorkerBlocked, OSError, TimeoutError, ValueError) as exc:
        code = exc.code if isinstance(exc, WorkerBlocked) else "producer_fail_closed"
        raise WorkerBlocked(code) from exc
    finally:
        if acquired and producer.owner_epoch:
            try:
                producer.release()
            except Exception:
                pass


def _event(status: str, **extra: str) -> None:
    print(json.dumps({"status": status, **extra}, separators=(",", ":")), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PHONE-FIRST staging DRY-RUN worker")
    parser.add_argument("--base-url", default=os.getenv("DCP_PHONE_DRYRUN_BASE_URL", ""))
    parser.add_argument("--token-file", default=os.getenv("DP_SYNC_TOKEN_FILE", ""))
    parser.add_argument("--producer-id", default=os.getenv("DCP_PHONE_DRYRUN_PRODUCER_ID", DEFAULT_PRODUCER_ID))
    parser.add_argument("--interval-seconds", type=float, default=float(os.getenv("DCP_PHONE_DRYRUN_INTERVAL_SECONDS", "10")))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("DCP_PHONE_DRYRUN_TIMEOUT_SECONDS", "10")))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    if not args.base_url or not args.token_file:
        _event("BLOCKED", code="configuration_missing")
        return 24
    try:
        token = load_token_file(args.token_file)
        key = signing_key(token)
        send = http_send_factory(args.base_url, timeout=args.timeout_seconds)
        while True:
            producer = CloudV2Producer(args.producer_id, key, send)
            outcome = run_once(producer)
            _event(outcome["status"], request_id=str(outcome.get("request_id", "")))
            if args.once:
                return 0
            time.sleep(max(1.0, args.interval_seconds))
    except WorkerBlocked as exc:
        _event("BLOCKED", code=exc.code)
        return 24
    except KeyboardInterrupt:
        _event("STOPPED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
