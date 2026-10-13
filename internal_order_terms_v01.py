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
import json
import sqlite3

from internal_order_core_v01 import (
    WarehouseActorContext,
    _strict_positive_int,
    _strict_whole_rub,
    create_payment_snapshot,
)


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
    """Calculate P from approved terms and confirmed quantities, atomically.

    confirmed_quantities is a server-side mapping {order_item_id: confirmed_qty}.
    v0.1 prototype requires every current order item to be represented and the
    confirmed quantity to equal the current payment-ready quantity. Partial
    composition changes must be resolved before this command until owner approves
    a revision/composition workflow.
    """
    confirmation_key=_text(confirmation_key,"confirmation_key")
    order_id=_text(order_id,"order_id")
    client_id=_text(client_id,"client_id")
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

        lines=_load_order_lines(c,order_id)
        expected_ids={int(r[0]) for r in lines}
        supplied_ids=set()
        normalized={}
        for raw_id,raw_qty in confirmed_quantities.items():
            item_id=_strict_positive_int(raw_id,"confirmed order_item_id")
            qty=_strict_positive_int(raw_qty,"confirmed quantity")
            supplied_ids.add(item_id)
            normalized[item_id]=qty
        if supplied_ids!=expected_ids:
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

        if prior is not None:
            same=(
                str(prior[0])==order_id
                and int(prior[1])==warehouse_id
                and str(prior[2])==terms_version
                and str(prior[3])==terms_source_type
                and str(prior[4])==terms_source_ref
                and str(prior[5])==actual_actor
                and str(prior[6])==effective_subject
                and int(prior[7])==calculated_p
                and str(prior[8])==evidence_json
            )
            if not same:
                raise ValueError("internal terms confirmation conflict")
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
            c.commit()
            return {
                "confirmation_key":confirmation_key,
                "partner_amount_rub":calculated_p,
                "snapshot":snapshot,
                "replayed":True,
            }

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

        c.commit()
        return {
            "confirmation_key":confirmation_key,
            "partner_amount_rub":calculated_p,
            "snapshot":snapshot,
            "replayed":False,
        }
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


__all__=[
    "TermsConfirmationContext",
    "init",
    "seed_approved_term",
    "confirm_terms_and_create_payment_snapshot",
]
