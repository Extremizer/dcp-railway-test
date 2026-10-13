#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Admin Acting-As security/audit layer for CONTRACT v1.4.

No target-user password or session token is used. The caller remains responsible
for authenticating the real admin and supplying the allowed permission sets.
This module never bypasses business guards.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


SUBJECT_TYPES={"client","dealer","rf_warehouse_user","usa_warehouse_user"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value,name):
    value=str(value or "").strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


def init(db_file):
    with sqlite3.connect(db_file) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS impersonation_sessions(
          session_id TEXT PRIMARY KEY,
          actual_admin_id TEXT NOT NULL,
          effective_subject_type TEXT NOT NULL,
          effective_subject_id TEXT NOT NULL,
          effective_role TEXT NOT NULL,
          data_scope TEXT NOT NULL,
          reason TEXT NOT NULL,
          started_at TEXT NOT NULL,
          expires_at TEXT NOT NULL,
          ended_at TEXT,
          status TEXT NOT NULL CHECK(status IN ('active','ended'))
        );

        CREATE TABLE IF NOT EXISTS acting_as_audit_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          actual_actor_id TEXT NOT NULL,
          actual_actor_role TEXT NOT NULL,
          effective_subject_type TEXT NOT NULL,
          effective_subject_id TEXT NOT NULL,
          effective_role TEXT NOT NULL,
          data_scope TEXT NOT NULL,
          action TEXT NOT NULL,
          resource_type TEXT NOT NULL,
          resource_id TEXT NOT NULL,
          reason TEXT NOT NULL,
          result TEXT NOT NULL,
          details_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          FOREIGN KEY(session_id) REFERENCES impersonation_sessions(session_id)
        );
        CREATE INDEX IF NOT EXISTS idx_acting_as_audit_session
          ON acting_as_audit_events(session_id,id);
        """)


def start_session(
    db_file, *,
    session_id,
    actual_admin_id,
    effective_subject_type,
    effective_subject_id,
    effective_role,
    data_scope,
    reason,
    expires_at,
    parent_impersonation_session_id=None,
):
    session_id=_text(session_id,"session_id")
    actual_admin_id=_text(actual_admin_id,"actual_admin_id")
    subject_type=_text(effective_subject_type,"effective_subject_type")
    if subject_type not in SUBJECT_TYPES:
        raise ValueError("unsupported effective subject type")
    subject_id=_text(effective_subject_id,"effective_subject_id")
    role=_text(effective_role,"effective_role")
    scope=_text(data_scope,"data_scope")
    reason=_text(reason,"reason")
    expires_at=_text(expires_at,"expires_at")
    if parent_impersonation_session_id:
        raise ValueError("nested impersonation is prohibited")
    try:
        exp=datetime.fromisoformat(expires_at.replace("Z","+00:00"))
    except ValueError as exc:
        raise ValueError("expires_at must be ISO-8601") from exc
    if exp.tzinfo is None:
        exp=exp.replace(tzinfo=timezone.utc)
    if exp<=datetime.now(timezone.utc):
        raise ValueError("expires_at must be in the future")
    init(db_file)
    with sqlite3.connect(db_file,timeout=30) as c:
        c.execute("BEGIN IMMEDIATE")
        prior=c.execute(
            "SELECT * FROM impersonation_sessions WHERE session_id=?",
            (session_id,)
        ).fetchone()
        if prior:
            raise ValueError("impersonation session already exists")
        c.execute(
            """INSERT INTO impersonation_sessions(
                 session_id,actual_admin_id,effective_subject_type,
                 effective_subject_id,effective_role,data_scope,reason,
                 started_at,expires_at,status)
               VALUES(?,?,?,?,?,?,?,?,?,'active')""",
            (session_id,actual_admin_id,subject_type,subject_id,role,scope,
             reason,_now(),expires_at)
        )
        c.commit()
    return session_id


def _active_session_conn(c,session_id):
    c.row_factory=sqlite3.Row
    row=c.execute(
        "SELECT * FROM impersonation_sessions WHERE session_id=?",
        (_text(session_id,"session_id"),)
    ).fetchone()
    if not row:
        raise ValueError("impersonation session not found")
    row=dict(row)
    if row["status"]!="active" or row["ended_at"]:
        raise ValueError("impersonation session is not active")
    exp=datetime.fromisoformat(str(row["expires_at"]).replace("Z","+00:00"))
    if exp.tzinfo is None:
        exp=exp.replace(tzinfo=timezone.utc)
    if exp<=datetime.now(timezone.utc):
        raise ValueError("impersonation session expired")
    return row


def get_active_session(db_file,session_id):
    init(db_file)
    with sqlite3.connect(db_file) as c:
        return _active_session_conn(c,session_id)


def end_session(db_file,*,session_id,actual_admin_id):
    actual_admin_id=_text(actual_admin_id,"actual_admin_id")
    init(db_file)
    with sqlite3.connect(db_file,timeout=30) as c:
        c.execute("BEGIN IMMEDIATE")
        row=_active_session_conn(c,session_id)
        if row["actual_admin_id"]!=actual_admin_id:
            raise ValueError("only actual admin may end impersonation session")
        ended=_now()
        c.execute(
            "UPDATE impersonation_sessions SET status='ended',ended_at=? WHERE session_id=?",
            (ended,session_id)
        )
        c.commit()
    return ended


def authorize(
    db_file, *,
    session_id,
    action,
    resource_scope,
    admin_permissions,
    effective_permissions,
    business_guard_passed,
    critical=False,
):
    """Enforce permission intersection and normal business guards."""
    row=get_active_session(db_file,session_id)
    action=_text(action,"action")
    resource_scope=_text(resource_scope,"resource_scope")
    admin_permissions=set(admin_permissions or ())
    effective_permissions=set(effective_permissions or ())
    if "ACT_AS" not in admin_permissions:
        raise PermissionError("admin lacks ACT_AS")
    if action not in admin_permissions:
        raise PermissionError("admin lacks action permission")
    if action not in effective_permissions:
        raise PermissionError("effective subject lacks action permission")
    if critical and "ACT_AS_CRITICAL" not in admin_permissions:
        raise PermissionError("admin lacks ACT_AS_CRITICAL")
    if resource_scope!=row["data_scope"]:
        raise PermissionError("resource outside impersonation data scope")
    if not business_guard_passed:
        raise PermissionError("business guard rejected operation")
    return row


def record_event(
    db_file, *,
    session_id,
    action,
    resource_type,
    resource_id,
    result,
    details=None,
):
    action=_text(action,"action")
    resource_type=_text(resource_type,"resource_type")
    resource_id=_text(resource_id,"resource_id")
    result=_text(result,"result")
    init(db_file)
    with sqlite3.connect(db_file,timeout=30) as c:
        c.row_factory=sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        row=_active_session_conn(c,session_id)
        cur=c.execute(
            """INSERT INTO acting_as_audit_events(
                 session_id,actual_actor_id,actual_actor_role,
                 effective_subject_type,effective_subject_id,effective_role,
                 data_scope,action,resource_type,resource_id,reason,
                 result,details_json,created_at)
               VALUES(?,?,'admin',?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id,
                row["actual_admin_id"],
                row["effective_subject_type"],
                row["effective_subject_id"],
                row["effective_role"],
                row["data_scope"],
                action,
                resource_type,
                resource_id,
                row["reason"],
                result,
                json.dumps(details or {},ensure_ascii=False,sort_keys=True),
                _now(),
            )
        )
        event_id=int(cur.lastrowid)
        c.commit()
        return event_id


def audit_events(db_file,session_id):
    init(db_file)
    with sqlite3.connect(db_file) as c:
        c.row_factory=sqlite3.Row
        return [
            dict(x) for x in c.execute(
                "SELECT * FROM acting_as_audit_events WHERE session_id=? ORDER BY id",
                (session_id,)
            ).fetchall()
        ]
