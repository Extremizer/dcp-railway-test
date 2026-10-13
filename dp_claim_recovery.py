"""Read-only plan and explicit administrative revocation. Never requeue an OEM."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import dp_live_health
from dp_result_completion import CompletionError


def _plan(conn, db_file, request_id):
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM dp_live_requests WHERE request_id=?", (request_id,)).fetchone()
    if row is None:
        raise CompletionError(404, "request_not_found")
    if row["status"] != "orphaned":
        raise CompletionError(409, "request_not_quarantined")
    plan = {"version": 1, "db_path": str(Path(db_file).resolve()), "request_id": request_id,
            "status": row["status"], "producer_id": row["claim_owner"],
            "claim_generation": row["claim_generation"], "lease_expires_at": row["lease_expires_at"],
            "orphaned_at": row["orphaned_at"], "action": "revoke_without_retry",
            "claim_token_sha256": hashlib.sha256(str(row["claim_token"] or "").encode()).hexdigest()}
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**plan, "plan_sha256": digest}


def recovery_plan(db_file, request_id):
    uri = Path(db_file).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        return _plan(conn, db_file, request_id)


def revoke_orphan(db_file, request_id, *, approved_plan_sha256, operator,
                  producer_inactive_confirmed, evidence_sha256):
    """An operator must confirm the OLD operation stopped, not just timed out.

    This is an administrative function, not an automatic timer or public route.
    A fresh READY does not prove inactivity; both conditions are mandatory.
    """
    if producer_inactive_confirmed is not True or not str(operator or "").strip():
        raise CompletionError(403, "administrative_inactivity_confirmation_required")
    if not re.fullmatch(r"[a-f0-9]{64}", str(evidence_sha256 or "")):
        raise CompletionError(400, "invalid_recovery_evidence")
    with closing(sqlite3.connect(str(db_file), timeout=10, isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            plan = _plan(conn, db_file, request_id)
            if plan["plan_sha256"] != approved_plan_sha256:
                raise CompletionError(409, "recovery_plan_changed")
            health = conn.execute("SELECT status,checked_at FROM dp_live_health WHERE singleton_id=1").fetchone()
            checked = dp_live_health._parse_datetime(health["checked_at"]) if health else None
            quarantined = dp_live_health._parse_datetime(plan["orphaned_at"])
            now = datetime.now(timezone.utc)
            if (not health or health["status"] != "READY" or not checked or not quarantined
                or checked <= quarantined or checked > now or (now-checked).total_seconds() > 60):
                raise CompletionError(409, "fresh_ready_after_quarantine_required")
            circuit = conn.execute("SELECT blocked_status,blocked_at FROM dp_live_coordination WHERE singleton_id=1").fetchone()
            blocked_at = dp_live_health._parse_datetime(circuit["blocked_at"]) if circuit else None
            if circuit and circuit["blocked_status"] and (not blocked_at or checked <= blocked_at):
                raise CompletionError(409, "dcp_circuit_open")
            conn.execute("""UPDATE dp_live_requests SET status='finished',finished_at=?,
                result_status='TECHNICAL_ERROR',error_code='admin_revoked_without_retry',
                claim_generation=claim_generation+1,claim_token=NULL
                WHERE request_id=? AND status='orphaned'""", (now.isoformat(),request_id))
            conn.execute("UPDATE dp_live_coordination SET blocked_status=NULL,blocked_at=NULL WHERE singleton_id=1")
            conn.execute("""INSERT INTO dp_live_recovery_events
                (request_id,claim_generation,event_type,actor,evidence_sha256,plan_sha256,created_at)
                VALUES(?,?,'admin_revoked',?,?,?,?)""",
                (request_id,plan["claim_generation"],str(operator)[:100],evidence_sha256,approved_plan_sha256,now.isoformat()))
            conn.commit()
            return {"ok": True, "request_id": request_id, "requeued": False}
        except Exception:
            conn.rollback()
            raise


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_file")
    parser.add_argument("request_id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approve-plan-sha256")
    parser.add_argument("--operator")
    parser.add_argument("--evidence-sha256")
    parser.add_argument("--producer-inactive-confirmed", action="store_true")
    args = parser.parse_args()
    try:
        result = (revoke_orphan(args.db_file,args.request_id,
            approved_plan_sha256=args.approve_plan_sha256,operator=args.operator,
            producer_inactive_confirmed=args.producer_inactive_confirmed,
            evidence_sha256=args.evidence_sha256) if args.apply else recovery_plan(args.db_file,args.request_id))
        print(json.dumps(result,sort_keys=True))
    except CompletionError as exc:
        print(json.dumps({"ok":False,"code":exc.code}))
        raise SystemExit(1)
