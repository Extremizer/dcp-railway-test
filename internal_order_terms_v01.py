#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Isolated authoritative Internal P creation prototype for P4.

Trusted versioned warehouse terms + warehouse-confirmed quantities are converted
server-side into partner amount P, then atomically into the existing immutable
Internal payment snapshot. This is not production warehouse auth.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import sqlite3

from internal_order_core_v01 import (
    _strict_positive_int,
    _strict_whole_rub,
    create_payment_snapshot,
    get_payment_snapshot,
)


TRUSTED_TERMS_CONTRACT_VERSION = "internal-terms-v1"


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value, name):
    value=str(value or "").strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


@dataclass(frozen=True)
class TermsConfirmationContext:
    actual_actor: str
    effective_subject: str
    warehouse_id: int
    authorized: bool


def init(db_file):
    with sqlite3.connect(db_file) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS internal_warehouse_terms_v01(
              warehouse_id INTEGER NOT NULL,
              terms_version TEXT NOT NULL,
              oem TEXT NOT NULL,
              partner_unit_rub INTEGER NOT NULL CHECK(partner_unit_rub >= 0),
              source_type TEXT NOT NULL,
              source_ref TEXT NOT NULL,
              approved INTEGER NOT NULL CHECK(approved IN (0,1)),
              created_at TEXT NOT NULL,
              PRIMARY KEY(warehouse_id,terms_version,oem)
            );

            CREATE TABLE IF NOT EXISTS internal_terms_confirmation_audit_v01(
              confirmation_key TEXT PRIMARY KEY,
              order_id TEXT NOT NULL,
              warehouse_id INTEGER NOT NULL,
              terms_version TEXT NOT NULL,
              terms_source_type TEXT NOT NULL,
              terms_source_ref TEXT NOT NULL,
              actual_actor TEXT NOT NULL,
              effective_subject TEXT NOT NULL,
              calculated_partner_amount_rub INTEGER NOT NULL,
              confirmed_lines_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS internal_order_snapshot_terms_provenance_v01(
              order_id TEXT PRIMARY KEY,
              confirmation_key TEXT NOT NULL UNIQUE,
              contract_version TEXT NOT NULL,
              warehouse_id INTEGER NOT NULL,
              client_id TEXT NOT NULL,
              warehouse_order_number TEXT,
              terms_version TEXT NOT NULL,
              terms_source_type TEXT NOT NULL,
              terms_source_ref TEXT NOT NULL,
              calculated_partner_amount_rub INTEGER NOT NULL,
              evidence_sha256 TEXT NOT NULL,
              fingerprint_version TEXT NOT NULL,
              fingerprint_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )


def seed_approved_term(
    db_file, *,
    warehouse_id,
    terms_version,
    oem,
    partner_unit_rub,
    source_type,
    source_ref,
):
    """Disposable test/admin setup for an already approved terms source."""
    warehouse_id=_strict_positive_int(warehouse_id,"warehouse_id")
    terms_version=_text(terms_version,"terms_version")
    oem=_text(oem,"oem")
    partner_unit_rub=_strict_whole_rub(partner_unit_rub,"partner_unit_rub")
    source_type=_text(source_type,"source_type")
    source_ref=_text(source_ref,"source_ref")
    init(db_file)
    with sqlite3.connect(db_file) as c:
        c.execute(
            """INSERT INTO internal_warehouse_terms_v01(
                 warehouse_id,terms_version,oem,partner_unit_rub,
                 source_type,source_ref,approved,created_at)
               VALUES(?,?,?,?,?,?,1,?)""",
            (
                warehouse_id,terms_version,oem,partner_unit_rub,
                source_type,source_ref,_now()
            ),
        )


def _confirmation_evidence_sha256(
    *,
    confirmation_key,
    order_id,
    warehouse_id,
    terms_version,
    terms_source_type,
    terms_source_ref,
    actual_actor,
    effective_subject,
    calculated_partner_amount_rub,
    confirmed_lines_json,
):
    try:
        confirmed_lines = json.loads(str(confirmed_lines_json))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("INVALID_TERMS_CONFIRMATION_EVIDENCE") from exc
    payload = json.dumps(
        {
            "actual_actor": str(actual_actor),
            "calculated_partner_amount_rub": int(calculated_partner_amount_rub),
            "confirmation_key": str(confirmation_key),
            "confirmed_lines": confirmed_lines,
            "effective_subject": str(effective_subject),
            "order_id": str(order_id),
            "terms_source_ref": str(terms_source_ref),
            "terms_source_type": str(terms_source_type),
            "terms_version": str(terms_version),
            "warehouse_id": int(warehouse_id),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_confirmed_evidence_json(confirmed_lines_json, calculated_p):
    try:
        lines = json.loads(str(confirmed_lines_json))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("INVALID_TERMS_CONFIRMATION_EVIDENCE") from exc
    if not isinstance(lines, list) or not lines:
        raise ValueError("INVALID_TERMS_CONFIRMATION_EVIDENCE")
    total = 0
    ids = set()
    for line in lines:
        if not isinstance(line, dict):
            raise ValueError("INVALID_TERMS_CONFIRMATION_EVIDENCE")
        item_id = _strict_positive_int(line.get("order_item_id"), "order_item_id")
        if item_id in ids:
            raise ValueError("INVALID_TERMS_CONFIRMATION_EVIDENCE")
        ids.add(item_id)
        qty = _strict_positive_int(
            line.get("confirmed_quantity"), "confirmed_quantity"
        )
        unit = _strict_whole_rub(
            line.get("partner_unit_rub"), "partner_unit_rub"
        )
        line_total = _strict_whole_rub(
            line.get("partner_line_rub"), "partner_line_rub"
        )
        if line_total != unit * qty:
            raise ValueError("INVALID_TERMS_CONFIRMATION_EVIDENCE")
        if not str(line.get("oem") or "").strip():
            raise ValueError("INVALID_TERMS_CONFIRMATION_EVIDENCE")
        total += line_total
    if total != int(calculated_p):
        raise ValueError("TERMS_EVIDENCE_P_MISMATCH")
    return lines


def get_snapshot_terms_provenance(db_file, order_id, *, conn=None):
    order_id = _text(order_id, "order_id")
    own = conn is None
    c = sqlite3.connect(db_file) if own else conn
    try:
        row = c.execute(
            """SELECT order_id,confirmation_key,contract_version,warehouse_id,
                      client_id,warehouse_order_number,terms_version,
                      terms_source_type,terms_source_ref,
                      calculated_partner_amount_rub,evidence_sha256,
                      fingerprint_version,fingerprint_sha256,created_at
               FROM internal_order_snapshot_terms_provenance_v01
               WHERE order_id=?""",
            (order_id,),
        ).fetchone()
        if row is None:
            raise ValueError("TRUSTED_TERMS_PROVENANCE_REQUIRED")
        names = [
            "order_id","confirmation_key","contract_version","warehouse_id",
            "client_id","warehouse_order_number","terms_version",
            "terms_source_type","terms_source_ref",
            "calculated_partner_amount_rub","evidence_sha256",
            "fingerprint_version","fingerprint_sha256","created_at",
        ]
        return dict(zip(names,row))
    finally:
        if own:
            c.close()


def verify_snapshot_terms_provenance(db_file, order_id, *, conn=None):
    """Verify immutable snapshot -> confirmation -> evidence linkage.

    Current warehouse terms rows are deliberately not consulted. A later terms
    source revision/withdrawal must not rewrite or invalidate historical proof.
    """
    order_id = _text(order_id, "order_id")
    own = conn is None
    c = sqlite3.connect(db_file) if own else conn
    try:
        provenance = get_snapshot_terms_provenance(
            db_file, order_id, conn=c
        )
        if provenance["contract_version"] != TRUSTED_TERMS_CONTRACT_VERSION:
            raise ValueError("UNSUPPORTED_TRUSTED_TERMS_CONTRACT")

        audit = c.execute(
            """SELECT confirmation_key,order_id,warehouse_id,terms_version,
                      terms_source_type,terms_source_ref,actual_actor,
                      effective_subject,calculated_partner_amount_rub,
                      confirmed_lines_json
               FROM internal_terms_confirmation_audit_v01
               WHERE confirmation_key=?""",
            (provenance["confirmation_key"],),
        ).fetchone()
        if audit is None:
            raise ValueError("TRUSTED_TERMS_AUDIT_REQUIRED")

        snapshot = get_payment_snapshot(db_file, order_id, conn=c)
        proof = c.execute(
            """SELECT fingerprint_version,fingerprint_sha256,item_count
               FROM internal_order_terms_integrity_v01
               WHERE order_id=?""",
            (order_id,),
        ).fetchone()
        if proof is None:
            raise ValueError("INTERNAL_TERMS_INTEGRITY_PROOF_REQUIRED")

        _validate_confirmed_evidence_json(
            audit[9], audit[8]
        )
        evidence_sha = _confirmation_evidence_sha256(
            confirmation_key=audit[0],
            order_id=audit[1],
            warehouse_id=audit[2],
            terms_version=audit[3],
            terms_source_type=audit[4],
            terms_source_ref=audit[5],
            actual_actor=audit[6],
            effective_subject=audit[7],
            calculated_partner_amount_rub=audit[8],
            confirmed_lines_json=audit[9],
        )

        expected_created_by = (
            "terms-confirmation:" + str(provenance["confirmation_key"])
        )
        same = (
            str(audit[0]) == str(provenance["confirmation_key"])
            and str(audit[1]) == order_id
            and int(audit[2]) == int(provenance["warehouse_id"])
            and str(audit[3]) == str(provenance["terms_version"])
            and str(audit[4]) == str(provenance["terms_source_type"])
            and str(audit[5]) == str(provenance["terms_source_ref"])
            and int(audit[8]) == int(provenance["calculated_partner_amount_rub"])
            and evidence_sha == str(provenance["evidence_sha256"])
            and str(snapshot["order_id"]) == order_id
            and int(snapshot["warehouse_id"]) == int(provenance["warehouse_id"])
            and str(snapshot["client_id"]) == str(provenance["client_id"])
            and snapshot["warehouse_order_number"]
                == provenance["warehouse_order_number"]
            and int(snapshot["partner_amount_rub"])
                == int(provenance["calculated_partner_amount_rub"])
            and str(snapshot["created_by"]) == expected_created_by
            and str(proof[0]) == str(provenance["fingerprint_version"])
            and str(proof[1]) == str(provenance["fingerprint_sha256"])
        )
        if not same:
            raise ValueError("TRUSTED_TERMS_PROVENANCE_MISMATCH")
        return {
            **provenance,
            "actual_actor": str(audit[6]),
            "effective_subject": str(audit[7]),
            "confirmed_lines": json.loads(str(audit[9])),
            "verified": True,
        }
    finally:
        if own:
            c.close()


def _load_order_lines(c, order_id):
    cols={str(r[1]) for r in c.execute("PRAGMA table_info(order_items)")}
    required={"id","order_id","offer_source","warehouse_id","oem","quantity"}
    if not required.issubset(cols):
        raise RuntimeError("shared order_items schema missing authoritative terms columns")
    rows=c.execute(
        """SELECT id,offer_source,warehouse_id,oem,quantity
           FROM order_items WHERE order_id=? ORDER BY id""",
        (str(order_id),),
    ).fetchall()
    if not rows:
        raise ValueError("internal order has no items")
    return rows


def confirm_terms_and_create_payment_snapshot(
    db_file, *,
    confirmation_key,
    order_id,
    client_id,
    warehouse_order_number,
    terms_version,
    terms_source_type,
    terms_source_ref,
    confirmed_quantities,
    actor_context: TermsConfirmationContext,
    _failure_hook=None,
):
    """Trusted server boundary: confirmed quantities + approved terms -> P.

    No caller-supplied partner amount is accepted. The immutable audit,
    payment snapshot, order-items fingerprint and explicit provenance link are
    created in one SQLite transaction.
    """
    confirmation_key=_text(confirmation_key,"confirmation_key")
    order_id=_text(order_id,"order_id")
    client_id=_text(client_id,"client_id")
    warehouse_order_number=(
        str(warehouse_order_number).strip()
        if warehouse_order_number is not None
        else None
    )
    if warehouse_order_number == "":
        warehouse_order_number = None
    terms_version=_text(terms_version,"terms_version")
    terms_source_type=_text(terms_source_type,"terms_source_type")
    terms_source_ref=_text(terms_source_ref,"terms_source_ref")
    if not isinstance(actor_context,TermsConfirmationContext):
        raise TypeError("actor_context must be TermsConfirmationContext")
    actual_actor=_text(actor_context.actual_actor,"actual_actor")
    effective_subject=_text(actor_context.effective_subject,"effective_subject")
    if not actor_context.authorized:
        raise PermissionError("WAREHOUSE_TERMS_ACTOR_NOT_AUTHORIZED")
    warehouse_id=_strict_positive_int(actor_context.warehouse_id,"warehouse_id")
    if not isinstance(confirmed_quantities,dict) or not confirmed_quantities:
        raise ValueError("confirmed_quantities required")

    normalized={}
    for raw_id,raw_qty in confirmed_quantities.items():
        item_id=_strict_positive_int(raw_id,"confirmed order_item_id")
        qty=_strict_positive_int(raw_qty,"confirmed quantity")
        if item_id in normalized:
            raise ValueError("duplicate confirmed order_item_id")
        normalized[item_id]=qty

    init(db_file)
    c=sqlite3.connect(db_file,timeout=30)
    try:
        c.execute("BEGIN IMMEDIATE")
        prior=c.execute(
            """SELECT order_id,warehouse_id,terms_version,terms_source_type,
                      terms_source_ref,actual_actor,effective_subject,
                      calculated_partner_amount_rub,confirmed_lines_json
               FROM internal_terms_confirmation_audit_v01
               WHERE confirmation_key=?""",
            (confirmation_key,),
        ).fetchone()

        if prior is not None:
            immutable_request_same=(
                str(prior[0])==order_id
                and int(prior[1])==warehouse_id
                and str(prior[2])==terms_version
                and str(prior[3])==terms_source_type
                and str(prior[4])==terms_source_ref
                and str(prior[5])==actual_actor
                and str(prior[6])==effective_subject
            )
            if not immutable_request_same:
                raise ValueError("internal terms confirmation conflict")
            prior_lines=_validate_confirmed_evidence_json(prior[8], prior[7])
            prior_quantities={
                int(line["order_item_id"]): int(line["confirmed_quantity"])
                for line in prior_lines
            }
            if prior_quantities != normalized:
                raise ValueError("internal terms confirmation conflict")

            provenance=get_snapshot_terms_provenance(
                db_file, order_id, conn=c
            )
            if (
                str(provenance["confirmation_key"]) != confirmation_key
                or str(provenance["client_id"]) != client_id
                or provenance["warehouse_order_number"] != warehouse_order_number
            ):
                raise ValueError("internal terms confirmation conflict")
            verified=verify_snapshot_terms_provenance(
                db_file, order_id, conn=c
            )
            snapshot=get_payment_snapshot(db_file, order_id, conn=c)
            c.commit()
            return {
                "confirmation_key":confirmation_key,
                "partner_amount_rub":int(prior[7]),
                "snapshot":snapshot,
                "provenance":verified,
                "replayed":True,
            }

        lines=_load_order_lines(c,order_id)
        expected_ids={int(r[0]) for r in lines}
        if set(normalized) != expected_ids:
            raise ValueError("CONFIRMED_QUANTITY_ITEM_SET_MISMATCH")

        calculated_p=0
        evidence=[]
        source_pairs=set()
        for item_id,source,line_warehouse,oem,ordered_qty in lines:
            if str(source or "").strip().lower()!="warehouse":
                raise ValueError("INTERNAL_ORDER_MUST_CONTAIN_ONLY_WAREHOUSE_LINES")
            if line_warehouse is None or int(line_warehouse)!=warehouse_id:
                raise PermissionError("INTERNAL_TERMS_WAREHOUSE_SCOPE_MISMATCH")
            ordered_qty=_strict_positive_int(ordered_qty,"order quantity")
            confirmed_qty=normalized[int(item_id)]
            if confirmed_qty!=ordered_qty:
                raise ValueError("CONFIRMED_QUANTITY_MUST_MATCH_PAYMENT_READY_ORDER")
            oem=_text(oem,"oem")
            term=c.execute(
                """SELECT partner_unit_rub,source_type,source_ref,approved
                   FROM internal_warehouse_terms_v01
                   WHERE warehouse_id=? AND terms_version=? AND oem=?""",
                (warehouse_id,terms_version,oem),
            ).fetchone()
            if term is None or int(term[3])!=1:
                raise ValueError("APPROVED_WAREHOUSE_TERM_REQUIRED")
            unit=_strict_whole_rub(term[0],"partner_unit_rub")
            if str(term[1])!=terms_source_type or str(term[2])!=terms_source_ref:
                raise ValueError("WAREHOUSE_TERMS_SOURCE_MISMATCH")
            source_pairs.add((str(term[1]),str(term[2])))
            line_total=unit*confirmed_qty
            calculated_p+=line_total
            evidence.append({
                "confirmed_quantity":confirmed_qty,
                "oem":oem,
                "order_item_id":int(item_id),
                "partner_line_rub":line_total,
                "partner_unit_rub":unit,
            })

        if len(source_pairs)!=1:
            raise ValueError("WAREHOUSE_TERMS_SOURCE_NOT_UNIFORM")
        evidence_json=json.dumps(
            evidence,ensure_ascii=False,sort_keys=True,separators=(",",":")
        )
        _validate_confirmed_evidence_json(evidence_json, calculated_p)

        if _failure_hook:
            _failure_hook("calculated")

        c.execute(
            """INSERT INTO internal_terms_confirmation_audit_v01(
                 confirmation_key,order_id,warehouse_id,terms_version,
                 terms_source_type,terms_source_ref,actual_actor,effective_subject,
                 calculated_partner_amount_rub,confirmed_lines_json,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                confirmation_key,order_id,warehouse_id,terms_version,
                terms_source_type,terms_source_ref,actual_actor,effective_subject,
                calculated_p,evidence_json,_now(),
            ),
        )

        if _failure_hook:
            _failure_hook("audit")

        snapshot=create_payment_snapshot(
            db_file,
            order_id=order_id,
            client_id=client_id,
            partner_amount_rub=calculated_p,
            created_by=f"terms-confirmation:{confirmation_key}",
            warehouse_order_number=warehouse_order_number,
            expected_warehouse_id=warehouse_id,
            conn=c,
        )

        if _failure_hook:
            _failure_hook("snapshot")

        proof=c.execute(
            """SELECT fingerprint_version,fingerprint_sha256
               FROM internal_order_terms_integrity_v01
               WHERE order_id=?""",
            (order_id,),
        ).fetchone()
        if proof is None:
            raise ValueError("INTERNAL_TERMS_INTEGRITY_PROOF_REQUIRED")

        evidence_sha=_confirmation_evidence_sha256(
            confirmation_key=confirmation_key,
            order_id=order_id,
            warehouse_id=warehouse_id,
            terms_version=terms_version,
            terms_source_type=terms_source_type,
            terms_source_ref=terms_source_ref,
            actual_actor=actual_actor,
            effective_subject=effective_subject,
            calculated_partner_amount_rub=calculated_p,
            confirmed_lines_json=evidence_json,
        )
        c.execute(
            """INSERT INTO internal_order_snapshot_terms_provenance_v01(
                 order_id,confirmation_key,contract_version,warehouse_id,
                 client_id,warehouse_order_number,terms_version,
                 terms_source_type,terms_source_ref,
                 calculated_partner_amount_rub,evidence_sha256,
                 fingerprint_version,fingerprint_sha256,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                order_id,confirmation_key,TRUSTED_TERMS_CONTRACT_VERSION,
                warehouse_id,client_id,warehouse_order_number,terms_version,
                terms_source_type,terms_source_ref,calculated_p,evidence_sha,
                str(proof[0]),str(proof[1]),_now(),
            ),
        )

        if _failure_hook:
            _failure_hook("provenance")

        verified=verify_snapshot_terms_provenance(
            db_file, order_id, conn=c
        )

        c.commit()
        return {
            "confirmation_key":confirmation_key,
            "partner_amount_rub":calculated_p,
            "snapshot":snapshot,
            "provenance":verified,
            "replayed":False,
        }
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


__all__=[
    "TRUSTED_TERMS_CONTRACT_VERSION",
    "TermsConfirmationContext",
    "init",
    "seed_approved_term",
    "confirm_terms_and_create_payment_snapshot",
    "get_snapshot_terms_provenance",
    "verify_snapshot_terms_provenance",
]
